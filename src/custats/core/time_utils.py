"""Time and date helpers used across the project."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


def now_utc() -> datetime:
    """Return the current UTC time as a timezone-aware datetime."""
    return datetime.now(timezone.utc)


def humanize_reset(delta: timedelta) -> str:
    """Format a :class:`datetime.timedelta` for menu-bar display.

    Examples::

        >>> humanize_reset(timedelta(seconds=0))
        'now'
        >>> humanize_reset(timedelta(minutes=14, seconds=5))
        '14m'
        >>> humanize_reset(timedelta(hours=2, minutes=14))
        '2h 14m'
        >>> humanize_reset(timedelta(days=5, hours=3))
        '5d 3h'
    """
    total_seconds = int(delta.total_seconds())
    if total_seconds <= 0:
        return "now"
    if total_seconds < 60:
        return f"{total_seconds}s"
    minutes, seconds = divmod(total_seconds, 60)
    if minutes < 60:
        return f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        if minutes == 0:
            return f"{hours}h"
        return f"{hours}h {minutes}m"
    days, hours = divmod(hours, 24)
    if hours == 0:
        return f"{days}d"
    return f"{days}d {hours}h"


def parse_iso(value: str) -> datetime:
    """Parse an ISO 8601 timestamp into a timezone-aware datetime.

    Accepts the trailing ``Z`` shorthand for ``+00:00`` and the
    output of :func:`now_utc`. Naive values are assumed UTC.
    """
    if not isinstance(value, str):
        raise TypeError(f"parse_iso expects a str, got {type(value).__name__}")
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def iso(dt: datetime) -> str:
    """Serialize a datetime as an ISO 8601 string (UTC, ``Z`` suffix)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


__all__ = [
    "humanize_reset",
    "iso",
    "now_utc",
    "parse_iso",
]
