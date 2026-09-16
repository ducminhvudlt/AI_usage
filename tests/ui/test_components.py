"""Tests for the popup card helpers — v3 §10 animated sparklines.

The animation is deliberately boring: two phase frames (~2 fps) where
every sparkline bucket shifts one step up the Unicode block ramp. No
Cairo, no timers — a build() at tick N and tick N+1 render different
strings, which is the whole observable contract.
"""
from __future__ import annotations

from custats.ui import _components as comp
from custats.ui._components import (
    _SPARKLINE_BUCKETS,
    _SPARKLINE_BLOCKS,
    _render_pace_line,
    _sparkline,
    build_account_card,
    get_tick,
)


# ---------------------------------------------------------------------- #
# _sparkline — animation phases
# ---------------------------------------------------------------------- #


class FakeStatus:
    """Minimal status stand-in for ``build_account_card``."""

    def __init__(self, *, alias="work", provider="claude", pace=67.0,
                 label="Risky", five=42.0, seven=20.0, error=None):
        from custats.core.models import Provider, UsageStatus

        self.account_id = "a1"
        self.alias = alias
        self.provider = Provider.parse(provider)
        self.five_hour_status = UsageStatus.GOOD
        self.seven_day_status = UsageStatus.GOOD
        self.five_hour_percent = five
        self.seven_day_percent = seven
        self.five_hour_resets_at = None
        self.seven_day_resets_at = None
        self.pace_projection_percent = pace
        self.pace_label = label
        self.error = error


def test_sparkline_phase_0_matches_unanimated_output():
    points = [0.0, 25.0, 50.0, 75.0, 100.0]
    assert _sparkline(points, anim=0) == _sparkline(points)


def test_sparkline_phase_1_shifts_buckets_up():
    points = [10.0, 20.0, 30.0]
    frame0 = _sparkline(points, anim=0)
    frame1 = _sparkline(points, anim=1)
    assert frame0 != frame1
    # Each bucket moved exactly one step up the ramp.
    for a, b in zip(frame0, frame1):
        assert _SPARKLINE_BLOCKS.index(b) == _SPARKLINE_BLOCKS.index(a) + 1


def test_sparkline_phase_wraps_modulo_frames():
    points = [10.0]
    # Phase 2 wraps back to frame 0 — only two frames exist.
    assert _sparkline(points, anim=2) == _sparkline(points, anim=0)
    assert _sparkline(points, anim=3) == _sparkline(points, anim=1)


def test_sparkline_clamps_at_top_of_ramp():
    # A 100% bucket is already the top block; animating must not overflow.
    frame1 = _sparkline([100.0], anim=1)
    assert frame1 == _SPARKLINE_BLOCKS[-1] * 1


def test_sparkline_empty_points_stay_empty_under_animation():
    assert _sparkline([], anim=1) == ""


# ---------------------------------------------------------------------- #
# _render_pace_line — fallback row breathes
# ---------------------------------------------------------------------- #


def test_pace_line_fallback_differs_between_frames():
    line0 = _render_pace_line(67.0, "", "Risky", anim=0)
    line1 = _render_pace_line(67.0, "", "Risky", anim=1)
    assert line0 != line1
    # Frame 0 fallback is the bottom block, frame 1 the next one up.
    assert _SPARKLINE_BLOCKS[0] * _SPARKLINE_BUCKETS in line0
    assert _SPARKLINE_BLOCKS[1] * _SPARKLINE_BUCKETS in line1


def test_pace_line_real_spark_overrides_fallback():
    spark = _sparkline([50.0] * 10, anim=0)
    line = _render_pace_line(67.0, spark, "Risky", anim=1)
    # The provided spark wins — no fallback substitution.
    assert spark in line


# ---------------------------------------------------------------------- #
# get_tick — phase source
# ---------------------------------------------------------------------- #


def test_get_tick_is_non_negative_int():
    tick = get_tick()
    assert isinstance(tick, int)
    assert tick >= 0


def test_get_tick_advances_within_a_second():
    a = get_tick()
    b = get_tick()
    # 2 fps: two ticks per second; a busy test loop may or may not cross
    # a boundary, but the tick must be monotonically non-decreasing.
    assert b >= a


# ---------------------------------------------------------------------- #
# build_account_card — animation plumbing
# ---------------------------------------------------------------------- #


def _build(Gtk_mock, tick, history=None):
    from datetime import datetime, timezone
    from unittest.mock import MagicMock

    return build_account_card(
        MagicMock(),
        FakeStatus(),
        pace_enabled=True,
        now=datetime.now(timezone.utc),
        on_open_dashboard=lambda: None,
        tick=tick,
        pace_history=history,
    )


def test_card_pace_anim_follows_tick(monkeypatch):
    _menu0, card0 = _build(None, tick=0)
    _menu1, card1 = _build(None, tick=1)
    assert card0.pace_anim == 0
    assert card1.pace_anim == 1
    # Different frames render different pace text.
    assert card0.pace_text != card1.pace_text


def test_card_pace_anim_wraps_modulo_two(monkeypatch):
    _menu, card = _build(None, tick=4)
    assert card.pace_anim == 0
    _menu, card = _build(None, tick=5)
    assert card.pace_anim == 1


def test_card_pace_history_override_beats_stub(monkeypatch):
    history = [25.0, 50.0, 75.0]
    _menu, card = _build(None, tick=0, history=history)
    assert _sparkline(history, anim=0) in card.pace_text


def test_card_default_tick_uses_get_tick(monkeypatch):
    """Without an explicit tick, the card uses the module-level phase."""
    monkeypatch.setattr(comp, "get_tick", lambda: 1)
    from datetime import datetime, timezone
    from unittest.mock import MagicMock

    _menu, card = build_account_card(
        MagicMock(),
        FakeStatus(),
        pace_enabled=True,
        now=datetime.now(timezone.utc),
        on_open_dashboard=lambda: None,
        pace_history=[],
    )
    assert card.pace_anim == 1
    # Fallback row at frame 1 uses the second block.
    assert _SPARKLINE_BLOCKS[1] * _SPARKLINE_BUCKETS in card.pace_text


def test_card_without_pace_keeps_animation_off_the_row(monkeypatch):
    """No pace projection → no pace line at all; nothing to animate."""
    from datetime import datetime, timezone
    from unittest.mock import MagicMock

    _menu, card = build_account_card(
        MagicMock(),
        FakeStatus(pace=None, label=""),
        pace_enabled=True,
        now=datetime.now(timezone.utc),
        on_open_dashboard=lambda: None,
        tick=1,
    )
    assert card.pace_text == ""
    assert card.pace_anim == 1
