"""Tests for custats.core.models."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from custats.core.models import Account, Provider, ProviderLimits, Usage, UsageStatus


class TestProvider:
    def test_members(self) -> None:
        assert {p.name for p in Provider} == {
            "CLAUDE",
            "CHATGPT",
            "CODEX",
            "COPILOT",
            "GEMINI",
            "GROK",
            "OPENROUTER",
            "DEEPSEEK",
            "MISTRAL",
            "KIMI",
            "CURSOR",
        }

    def test_values_are_lowercase(self) -> None:
        for member in Provider:
            assert member.value == member.value.lower()

    def test_is_str_subclass(self) -> None:
        # Enum members should serialize cleanly as strings.
        assert str(Provider.CLAUDE.value) == "claude"

    def test_parse_by_value(self) -> None:
        assert Provider.parse("claude") is Provider.CLAUDE
        assert Provider.parse("codex") is Provider.CODEX

    def test_parse_by_name_case_insensitive(self) -> None:
        assert Provider.parse("Claude") is Provider.CLAUDE
        assert Provider.parse("GROK") is Provider.GROK

    def test_parse_strips_whitespace(self) -> None:
        assert Provider.parse("  cursor  ") is Provider.CURSOR

    @pytest.mark.parametrize("bad", ["", "openai", "anthropic", "random"])
    def test_parse_invalid(self, bad: str) -> None:
        with pytest.raises(ValueError):
            Provider.parse(bad)

    def test_parse_wrong_type(self) -> None:
        with pytest.raises(ValueError):
            Provider.parse(123)  # type: ignore[arg-type]


class TestUsageStatus:
    def test_severity_order(self) -> None:
        assert (
            UsageStatus.UNKNOWN.severity
            < UsageStatus.GOOD.severity
            < UsageStatus.CAUTION.severity
            < UsageStatus.CRITICAL.severity
            < UsageStatus.AT_LIMIT.severity
        )

    def test_all_members(self) -> None:
        names = {s.name for s in UsageStatus}
        assert names == {"UNKNOWN", "GOOD", "CAUTION", "CRITICAL", "AT_LIMIT"}


class TestProviderLimits:
    def test_defaults(self) -> None:
        limits = ProviderLimits()
        assert limits.five_hour_percent is None
        assert limits.seven_day_percent is None
        assert limits.grok_window is None
        assert limits.codex_reset_credits is None
        assert limits.cursor_plan is None
        assert limits.monthly_percent is None

    def test_monthly_percent_round_trip(self) -> None:
        limits = ProviderLimits(monthly_percent=42.0)
        assert limits.monthly_percent == 42.0

    def test_frozen(self) -> None:
        limits = ProviderLimits(five_hour_percent=42.0)
        with pytest.raises((AttributeError, Exception)):
            limits.five_hour_percent = 99.0  # type: ignore[misc]

    def test_grok_window_assignment(self) -> None:
        limits = ProviderLimits(grok_window="weekly")
        assert limits.grok_window == "weekly"
        limits = ProviderLimits(grok_window="monthly")
        assert limits.grok_window == "monthly"


class TestUsage:
    def test_minimal_construction(self) -> None:
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        usage = Usage(
            account_id="abc",
            provider=Provider.CLAUDE,
            fetched_at=now,
            five_hour=ProviderLimits(five_hour_percent=12.5),
        )
        assert usage.account_id == "abc"
        assert usage.provider is Provider.CLAUDE
        assert usage.fetched_at == now
        assert usage.five_hour.five_hour_percent == 12.5
        assert usage.five_hour_resets_at is None
        assert usage.seven_day is None
        assert usage.seven_day_resets_at is None
        assert usage.extra_credits_remaining is None
        assert usage.raw is None

    def test_full_construction(self) -> None:
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        reset = datetime(2026, 1, 1, 5, tzinfo=timezone.utc)
        usage = Usage(
            account_id="abc",
            provider=Provider.GROK,
            fetched_at=now,
            five_hour=ProviderLimits(five_hour_percent=10, grok_window="weekly"),
            five_hour_resets_at=reset,
            seven_day=ProviderLimits(seven_day_percent=20),
            seven_day_resets_at=reset,
            extra_credits_remaining=42.0,
            raw={"foo": "bar"},
        )
        assert usage.seven_day is not None
        assert usage.seven_day.seven_day_percent == 20
        assert usage.five_hour.grok_window == "weekly"
        assert usage.raw == {"foo": "bar"}


class TestAccount:
    def test_new_id_is_hex_uuid(self) -> None:
        ident = Account.new_id()
        assert len(ident) == 32
        int(ident, 16)  # must be valid hex

    def test_new_id_is_unique(self) -> None:
        ids = {Account.new_id() for _ in range(50)}
        assert len(ids) == 50

    def test_defaults(self) -> None:
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        acct = Account(
            id="x", alias="work", provider=Provider.CLAUDE, created_at=now
        )
        assert acct.is_active is True
        assert acct.last_seen_at is None
