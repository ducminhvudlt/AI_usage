"""Tests for custats.core.pace."""

from __future__ import annotations

import math

import pytest

from custats.core.models import UsageStatus
from custats.core.pace import (
    PROJECTION_CAP,
    calculate_pace,
    pace_label,
    pace_status,
)


class TestCalculatePace:
    def test_spec_fixture_risky(self) -> None:
        # 40% used over 72h of a 168h window → ~93.3% projected.
        projected = calculate_pace(current_percent=40.0, elapsed_hours=72, remaining_hours=96)
        assert math.isclose(projected, 40 + (40 / 72) * 96, rel_tol=1e-9)
        assert math.isclose(projected, 93.3333333, rel_tol=1e-6)
        assert pace_status(projected) is UsageStatus.CRITICAL

    def test_spec_fixture_over(self) -> None:
        # 20% used over 31h of a 168h window → ~108.4% projected.
        projected = calculate_pace(
            current_percent=20.0, elapsed_hours=31, remaining_hours=137
        )
        assert math.isclose(projected, 20 + (20 / 31) * 137, rel_tol=1e-9)
        assert math.isclose(projected, 108.3870967, rel_tol=1e-6)
        assert pace_status(projected) is UsageStatus.AT_LIMIT

    def test_spec_fixture_healthy(self) -> None:
        projected = calculate_pace(
            current_percent=0.0, elapsed_hours=10, remaining_hours=100
        )
        assert projected == 0.0
        assert pace_status(projected) is UsageStatus.GOOD

    @pytest.mark.parametrize("elapsed", [0, 0.0, -1, -10])
    def test_elapsed_zero_or_negative_guard(self, elapsed: float) -> None:
        assert calculate_pace(50.0, elapsed, 100) == 0.0

    def test_negative_current_clamped(self) -> None:
        # Negative current shouldn't produce negative projections.
        projected = calculate_pace(-5.0, 10, 100)
        assert projected >= 0.0

    def test_remaining_zero_returns_current(self) -> None:
        projected = calculate_pace(42.5, 24, 0)
        assert projected == 42.5

    def test_projection_capped(self) -> None:
        projected = calculate_pace(99.0, 1, 10000)
        assert projected == PROJECTION_CAP


class TestPaceStatus:
    def test_below_risky(self) -> None:
        assert pace_status(0.0) is UsageStatus.GOOD
        assert pace_status(50.0) is UsageStatus.GOOD
        assert pace_status(89.999) is UsageStatus.GOOD

    def test_risky_window(self) -> None:
        assert pace_status(90.0) is UsageStatus.CRITICAL
        assert pace_status(99.9) is UsageStatus.CRITICAL

    def test_over(self) -> None:
        assert pace_status(100.0) is UsageStatus.AT_LIMIT
        assert pace_status(150.0) is UsageStatus.AT_LIMIT

    def test_negative_is_good(self) -> None:
        # Defensive — projected should never be negative, but just in case.
        assert pace_status(-1.0) is UsageStatus.GOOD


class TestPaceLabel:
    def test_healthy(self) -> None:
        assert pace_label(0.0) == "Healthy"
        assert pace_label(89.9) == "Healthy"

    def test_risky(self) -> None:
        assert pace_label(90.0) == "Risky"
        assert pace_label(99.9) == "Risky"

    def test_over(self) -> None:
        assert pace_label(100.0) == "Over"
        assert pace_label(250.0) == "Over"

    def test_unknown(self) -> None:
        assert pace_label(None) == "Unknown"
