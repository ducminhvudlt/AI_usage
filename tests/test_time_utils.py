"""Tests for time_utils."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from custats.core.time_utils import humanize_reset, iso, now_utc, parse_iso


class TestNowUtc:
    def test_is_timezone_aware(self) -> None:
        n = now_utc()
        assert n.tzinfo is not None
        assert n.tzinfo.utcoffset(n) == timedelta(0)


class TestHumanizeReset:
    def test_now(self) -> None:
        assert humanize_reset(timedelta(seconds=0)) == "now"
        assert humanize_reset(timedelta(seconds=-10)) == "now"

    def test_seconds(self) -> None:
        assert humanize_reset(timedelta(seconds=5)) == "5s"
        assert humanize_reset(timedelta(seconds=59)) == "59s"

    def test_minutes(self) -> None:
        assert humanize_reset(timedelta(minutes=14, seconds=5)) == "14m"
        assert humanize_reset(timedelta(minutes=1)) == "1m"
        assert humanize_reset(timedelta(minutes=59)) == "59m"

    def test_hours_and_minutes(self) -> None:
        assert humanize_reset(timedelta(hours=2, minutes=14)) == "2h 14m"

    def test_hours_only(self) -> None:
        assert humanize_reset(timedelta(hours=3)) == "3h"

    def test_days_and_hours(self) -> None:
        assert humanize_reset(timedelta(days=5, hours=3)) == "5d 3h"

    def test_days_only(self) -> None:
        assert humanize_reset(timedelta(days=2)) == "2d"


class TestParseIso:
    def test_z_suffix(self) -> None:
        dt = parse_iso("2026-01-02T03:04:05Z")
        assert dt == datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)

    def test_explicit_offset(self) -> None:
        dt = parse_iso("2026-01-02T03:04:05+00:00")
        assert dt.tzinfo is not None
        assert dt.utcoffset() == timedelta(0)

    def test_naive_assumed_utc(self) -> None:
        dt = parse_iso("2026-01-02T03:04:05")
        assert dt.tzinfo is timezone.utc

    def test_round_trip_with_iso(self) -> None:
        original = datetime(2026, 6, 15, 12, 30, 45, tzinfo=timezone.utc)
        assert parse_iso(iso(original)) == original

    def test_strips_whitespace(self) -> None:
        assert parse_iso("  2026-01-02T03:04:05Z  ") == datetime(
            2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc
        )

    def test_wrong_type(self) -> None:
        with pytest.raises(TypeError):
            parse_iso(12345)  # type: ignore[arg-type]


class TestIso:
    def test_utc_format(self) -> None:
        dt = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
        assert iso(dt) == "2026-01-02T03:04:05Z"

    def test_normalizes_to_utc(self) -> None:
        eastern = timezone(timedelta(hours=-5))
        dt = datetime(2026, 1, 2, 8, 0, 0, tzinfo=eastern)
        assert iso(dt) == "2026-01-02T13:00:00Z"

    def test_naive_assumed_utc(self) -> None:
        dt = datetime(2026, 1, 2, 3, 4, 5)
        assert iso(dt) == "2026-01-02T03:04:05Z"
