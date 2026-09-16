"""Tests for custats.state — the status engine."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from custats.core.models import (
    Account,
    Provider,
    ProviderLimits,
    Usage,
    UsageStatus,
)
from custats.state import compute_aggregate, compute_status


def _account(provider: Provider = Provider.CLAUDE) -> Account:
    return Account(
        id="acc-1",
        alias="work",
        provider=provider,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def _usage(
    *,
    five: float | None = 10.0,
    seven: float | None = 5.0,
    five_resets_in_h: float = 4.0,
    seven_resets_in_h: float = 120.0,
) -> Usage:
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    return Usage(
        account_id="acc-1",
        provider=Provider.CLAUDE,
        fetched_at=now,
        five_hour=ProviderLimits(five_hour_percent=five) if five is not None else None,
        five_hour_resets_at=now + timedelta(hours=five_resets_in_h),
        seven_day=(
            ProviderLimits(seven_day_percent=seven) if seven is not None else None
        ),
        seven_day_resets_at=now + timedelta(hours=seven_resets_in_h),
    )


class TestComputeStatus:
    def test_normal_usage_populates_fields(self) -> None:
        usage = _usage()
        status = compute_status(_account(), usage, pace_enabled=True)
        assert status.account_id == "acc-1"
        assert status.provider is Provider.CLAUDE
        assert status.alias == "work"
        assert status.five_hour_percent == 10.0
        assert status.seven_day_percent == 5.0
        assert status.five_hour_status is UsageStatus.GOOD
        assert status.seven_day_status is UsageStatus.GOOD
        assert status.five_hour_resets_at is not None
        assert status.seven_day_resets_at is not None
        assert status.error is None
        assert status.fetched_at == usage.fetched_at

    def test_low_percent_pace_label_healthy(self) -> None:
        status = compute_status(_account(), _usage(), pace_enabled=True)
        # 5% used with 120h left of a 7-day window → tiny projected
        assert status.pace_projection_percent is not None
        assert status.pace_projection_percent < 90.0
        assert status.pace_label == "Healthy"

    def test_pace_disabled_returns_none(self) -> None:
        status = compute_status(_account(), _usage(), pace_enabled=False)
        assert status.pace_projection_percent is None
        assert status.pace_label == "Unknown"

    def test_no_usage_with_error(self) -> None:
        status = compute_status(
            _account(), None, pace_enabled=True, error="boom"
        )
        assert status.five_hour_status is UsageStatus.UNKNOWN
        assert status.seven_day_status is UsageStatus.UNKNOWN
        assert status.five_hour_percent is None
        assert status.seven_day_percent is None
        assert status.five_hour_resets_at is None
        assert status.seven_day_resets_at is None
        assert status.pace_projection_percent is None
        assert status.pace_label == "Unknown"
        assert status.error == "boom"

    def test_no_usage_without_error(self) -> None:
        status = compute_status(_account(), None, pace_enabled=True)
        assert status.error is None
        assert status.five_hour_status is UsageStatus.UNKNOWN

    def test_five_hour_only(self) -> None:
        """Cursor-style usage where the 5-hour window is absent."""
        usage = Usage(
            account_id="acc-1",
            provider=Provider.CURSOR,
            fetched_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            seven_day=ProviderLimits(seven_day_percent=25.0),
        )
        status = compute_status(_account(Provider.CURSOR), usage, pace_enabled=True)
        assert status.five_hour_percent is None
        assert status.seven_day_percent == 25.0
        assert status.five_hour_status is UsageStatus.UNKNOWN
        assert status.seven_day_status is UsageStatus.GOOD

    def test_critical_status_above_threshold(self) -> None:
        usage = _usage(five=92.0)
        status = compute_status(_account(), usage, pace_enabled=False)
        assert status.five_hour_status is UsageStatus.CRITICAL

    def test_at_limit_status_at_full(self) -> None:
        usage = _usage(five=100.0)
        status = compute_status(_account(), usage, pace_enabled=False)
        assert status.five_hour_status is UsageStatus.AT_LIMIT

    def test_pace_projection_with_short_remaining(self) -> None:
        """When the 7-day window is nearly over, projection approaches current."""
        # 7-day window resets in 1h → 167h elapsed, 1h remaining.
        usage = _usage(seven=10.0, seven_resets_in_h=1.0)
        status = compute_status(_account(), usage, pace_enabled=True)
        assert status.pace_projection_percent is not None
        # Very close to current value (10%).
        assert abs(status.pace_projection_percent - 10.0) < 0.5

    def test_pace_unavailable_when_no_resets_at(self) -> None:
        usage = Usage(
            account_id="acc-1",
            provider=Provider.CLAUDE,
            fetched_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            seven_day=ProviderLimits(seven_day_percent=5.0),
            seven_day_resets_at=None,
        )
        status = compute_status(_account(), usage, pace_enabled=True)
        assert status.pace_projection_percent is None
        assert status.pace_label == "Unknown"


class TestComputeAggregate:
    def test_empty_returns_unknown(self) -> None:
        assert compute_aggregate([]) is UsageStatus.UNKNOWN

    def test_worst_of_two(self) -> None:
        status = compute_status(_account(), _usage(five=92.0), pace_enabled=False)
        assert compute_aggregate([status]) is UsageStatus.CRITICAL

    def test_picks_max_across_accounts(self) -> None:
        good = compute_status(_account(), _usage(five=10.0), pace_enabled=False)
        caution = compute_status(_account(), _usage(five=75.0), pace_enabled=False)
        critical = compute_status(_account(), _usage(five=95.0), pace_enabled=False)
        assert compute_aggregate([good, caution, critical]) is UsageStatus.CRITICAL

    def test_handles_all_good(self) -> None:
        a = compute_status(_account(), _usage(five=10.0), pace_enabled=False)
        b = compute_status(_account(), _usage(seven=20.0), pace_enabled=False)
        assert compute_aggregate([a, b]) is UsageStatus.GOOD

    def test_unerroring_unknown_is_lowest(self) -> None:
        """A failed poll (UNKNOWN) must not be picked over a real CRITICAL."""
        failed = compute_status(
            _account(), None, pace_enabled=False, error="x"
        )
        ok = compute_status(_account(), _usage(five=95.0), pace_enabled=False)
        assert compute_aggregate([failed, ok]) is UsageStatus.CRITICAL
