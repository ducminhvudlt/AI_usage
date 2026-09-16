"""Tests for custats.core.status."""

from __future__ import annotations

import pytest

from custats.core.models import UsageStatus
from custats.core.status import (
    AT_LIMIT_THRESHOLD,
    CAUTION_THRESHOLD,
    CRITICAL_THRESHOLD,
    usage_status,
    worst_status,
)


class TestUsageStatusFunction:
    def test_none_is_unknown(self) -> None:
        assert usage_status(None) is UsageStatus.UNKNOWN

    @pytest.mark.parametrize(
        "value,expected",
        [
            (-1.0, UsageStatus.GOOD),
            (0.0, UsageStatus.GOOD),
            (0.0001, UsageStatus.GOOD),
            (50.0, UsageStatus.GOOD),
            (69.9, UsageStatus.GOOD),
            (70.0, UsageStatus.CAUTION),
            (70.1, UsageStatus.CAUTION),
            (89.9, UsageStatus.CAUTION),
            (90.0, UsageStatus.CRITICAL),
            (90.1, UsageStatus.CRITICAL),
            (99.9, UsageStatus.CRITICAL),
            (100.0, UsageStatus.AT_LIMIT),
            (100.0001, UsageStatus.AT_LIMIT),
            (250.0, UsageStatus.AT_LIMIT),
        ],
    )
    def test_boundaries(self, value: float, expected: UsageStatus) -> None:
        assert usage_status(value) is expected


class TestWorstStatus:
    def test_empty_iterable(self) -> None:
        assert worst_status([]) is UsageStatus.UNKNOWN

    def test_single(self) -> None:
        assert worst_status([UsageStatus.GOOD]) is UsageStatus.GOOD

    def test_picks_max_severity(self) -> None:
        statuses = [
            UsageStatus.GOOD,
            UsageStatus.CRITICAL,
            UsageStatus.CAUTION,
            UsageStatus.UNKNOWN,
        ]
        assert worst_status(statuses) is UsageStatus.CRITICAL

    def test_generator_input(self) -> None:
        gen = (s for s in [UsageStatus.GOOD, UsageStatus.AT_LIMIT])
        assert worst_status(gen) is UsageStatus.AT_LIMIT

    def test_unknown_with_unknown(self) -> None:
        assert worst_status([UsageStatus.UNKNOWN, UsageStatus.UNKNOWN]) is UsageStatus.UNKNOWN


def test_threshold_constants() -> None:
    # Lock the documented thresholds so a future tweak is intentional.
    assert CAUTION_THRESHOLD == 70.0
    assert CRITICAL_THRESHOLD == 90.0
    assert AT_LIMIT_THRESHOLD == 100.0
