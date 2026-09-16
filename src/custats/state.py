"""Status engine — combines a :class:`Usage` with prior state to
produce the high-level :class:`LiveStatus` snapshot the UI consumes.
Pure module: no I/O.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from .core.models import Account, Provider, ProviderLimits, Usage, UsageStatus
from .core.pace import calculate_pace, pace_label
from .core.status import usage_status, worst_status
from .core.time_utils import now_utc

# Default 7-day window length for pace projection.
DEFAULT_SEVEN_DAY_HOURS = 24 * 7


@dataclass(frozen=True)
class LiveStatus:
    """High-level status snapshot for one account."""
    account_id: str
    provider: Provider
    alias: str
    five_hour_status: UsageStatus
    seven_day_status: UsageStatus
    five_hour_percent: float | None
    seven_day_percent: float | None
    five_hour_resets_at: datetime | None
    seven_day_resets_at: datetime | None
    pace_projection_percent: float | None  # 7-day pace; None if disabled/unknown
    pace_label: str
    fetched_at: datetime
    error: str | None = None  # adapter error text if last poll failed


def compute_status(
    account: Account,
    usage: Usage | None,
    *,
    pace_enabled: bool,
    error: str | None = None,
) -> LiveStatus:
    """Build a :class:`LiveStatus` from the latest poll result."""
    now = now_utc()
    if usage is None:
        return LiveStatus(
            account_id=account.id, provider=account.provider, alias=account.alias,
            five_hour_status=UsageStatus.UNKNOWN, seven_day_status=UsageStatus.UNKNOWN,
            five_hour_percent=None, seven_day_percent=None,
            five_hour_resets_at=None, seven_day_resets_at=None,
            pace_projection_percent=None, pace_label="Unknown",
            fetched_at=now, error=error,
        )
    five_hour_pct = _pct(usage.five_hour, "five_hour_percent")
    seven_day_pct = _pct(usage.seven_day, "seven_day_percent")
    pace_projection = None
    if pace_enabled and seven_day_pct is not None and usage.seven_day_resets_at is not None:
        pace_projection = _project_pace(seven_day_pct, usage.seven_day_resets_at, now)
    return LiveStatus(
        account_id=account.id, provider=account.provider, alias=account.alias,
        five_hour_status=usage_status(five_hour_pct),
        seven_day_status=usage_status(seven_day_pct),
        five_hour_percent=five_hour_pct, seven_day_percent=seven_day_pct,
        five_hour_resets_at=usage.five_hour_resets_at,
        seven_day_resets_at=usage.seven_day_resets_at,
        pace_projection_percent=pace_projection,
        pace_label=pace_label(pace_projection),
        fetched_at=usage.fetched_at, error=error,
    )


def compute_aggregate(statuses: Iterable[LiveStatus]) -> UsageStatus:
    """Return the worst status across many accounts (menu-bar badge)."""
    return worst_status(
        [s.five_hour_status for s in statuses]
        + [s.seven_day_status for s in statuses]
    )


def _pct(limits: ProviderLimits | None, field: str) -> float | None:
    return getattr(limits, field, None) if limits is not None else None


def _project_pace(
    seven_day_pct: float,
    seven_day_resets: datetime,
    now: datetime,
) -> float | None:
    """Project the 7-day burn-rate forward; ``None`` when inputs are unusable."""
    if seven_day_resets.tzinfo is None:
        seven_day_resets = seven_day_resets.replace(tzinfo=timezone.utc)
    remaining_hours = (seven_day_resets - now).total_seconds() / 3600.0
    elapsed_hours = DEFAULT_SEVEN_DAY_HOURS - remaining_hours
    if elapsed_hours <= 0:
        return None
    return calculate_pace(
        current_percent=seven_day_pct,
        elapsed_hours=elapsed_hours,
        remaining_hours=max(0.0, remaining_hours),
    )


__all__ = [
    "DEFAULT_SEVEN_DAY_HOURS",
    "LiveStatus",
    "compute_aggregate",
    "compute_status",
]
