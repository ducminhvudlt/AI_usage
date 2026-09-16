"""Usage-status bucketing helpers.

Pure functions that map raw percentages to enum buckets. The
thresholds intentionally match the macOS CUStats defaults so the
Linux port behaves the same way.
"""

from __future__ import annotations

from typing import Iterable

from .models import UsageStatus

# Threshold constants — kept here so tests and docs can reference them.
CAUTION_THRESHOLD = 70.0
CRITICAL_THRESHOLD = 90.0
AT_LIMIT_THRESHOLD = 100.0


def usage_status(percent: float | None) -> UsageStatus:
    """Bucket a raw percentage into a :class:`UsageStatus`.

    ``None`` is treated as ``UNKNOWN`` (no data available yet).
    Boundaries are inclusive on the low side: ``70.0`` is
    ``CAUTION``, ``90.0`` is ``CRITICAL``, ``100.0`` is
    ``AT_LIMIT``.
    """
    if percent is None:
        return UsageStatus.UNKNOWN
    if percent >= AT_LIMIT_THRESHOLD:
        return UsageStatus.AT_LIMIT
    if percent >= CRITICAL_THRESHOLD:
        return UsageStatus.CRITICAL
    if percent >= CAUTION_THRESHOLD:
        return UsageStatus.CAUTION
    return UsageStatus.GOOD


def worst_status(statuses: Iterable[UsageStatus]) -> UsageStatus:
    """Return the most-severe status from an iterable.

    An empty iterable returns :data:`UsageStatus.UNKNOWN` — there is
    literally no signal to act on.
    """
    return max(statuses, key=lambda s: s.severity, default=UsageStatus.UNKNOWN)


__all__ = [
    "AT_LIMIT_THRESHOLD",
    "CAUTION_THRESHOLD",
    "CRITICAL_THRESHOLD",
    "usage_status",
    "worst_status",
]
