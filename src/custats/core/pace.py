"""Pace-projection helpers.

Given how much of a usage window we've burned through and how much
is left, ``calculate_pace`` projects where we'll land at the end of
the window. The companion helpers translate that projection into a
status enum and a human-readable label.
"""

from __future__ import annotations

from .models import UsageStatus

# Cap projections to avoid silly floats when inputs are pathological.
PROJECTION_CAP = 999.0

# Thresholds match the macOS CUStats defaults. These intentionally share
# values with ``status.CRITICAL_THRESHOLD`` / ``status.AT_LIMIT_THRESHOLD``
# — the pace projection and the snapshot thresholds are the same number
# by design so a "risky" projection maps directly to "CRITICAL".
RISKY_THRESHOLD = 90.0
OVER_THRESHOLD = 100.0


def calculate_pace(
    current_percent: float,
    elapsed_hours: float,
    remaining_hours: float,
) -> float:
    """Project final-window usage given the current burn rate.

    Formula: ``projected = current + (current / elapsed) * remaining``.
    Guards against ``elapsed_hours <= 0`` (returns 0.0) and caps the
    result at :data:`PROJECTION_CAP` so a stale snapshot can't
    produce an absurd value.
    """
    if elapsed_hours <= 0:
        return 0.0
    if current_percent < 0:
        current_percent = 0.0
    if remaining_hours <= 0:
        return float(min(current_percent, PROJECTION_CAP))
    rate = current_percent / elapsed_hours
    projected = current_percent + rate * remaining_hours
    if projected > PROJECTION_CAP:
        return PROJECTION_CAP
    if projected < 0.0:
        return 0.0
    return projected


def pace_status(projected: float) -> UsageStatus:
    """Translate a projected percentage into a status bucket.

    CUStats' macOS UI distinguishes "Healthy" / "Risky" / "Over".
    We keep our enum small (no separate "Risky") and map "Risky" →
    :data:`UsageStatus.CRITICAL` here. Use :func:`pace_label` if
    you need the original three-way label.
    """
    if projected < 0:
        return UsageStatus.GOOD
    if projected >= OVER_THRESHOLD:
        return UsageStatus.AT_LIMIT
    if projected >= RISKY_THRESHOLD:
        return UsageStatus.CRITICAL
    return UsageStatus.GOOD


def pace_label(projected: float | None) -> str:
    """Return one of ``Healthy`` / ``Risky`` / ``Over`` / ``Unknown``.

    Mirrors the vocabulary of the original CUStats macOS app.
    """
    if projected is None:
        return "Unknown"
    if projected >= OVER_THRESHOLD:
        return "Over"
    if projected >= RISKY_THRESHOLD:
        return "Risky"
    return "Healthy"


__all__ = [
    "OVER_THRESHOLD",
    "PROJECTION_CAP",
    "RISKY_THRESHOLD",
    "calculate_pace",
    "pace_label",
    "pace_status",
]
