"""Tests for the system-tray icon."""
from __future__ import annotations

import sys
import types
from dataclasses import dataclass
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from custats.core.models import Provider, UsageStatus
from custats.state import LiveStatus
from custats.ui.tray import (
    TrayIcon,
    TrayUnavailable,
    _PROVIDER_GLYPH,
    _STATUS_COLOURS_HEX,
    _worst_account,
    _worst_status,
)
from tests.ui.conftest import install_gtk_mocks


# ---------------------------------------------------------------------- #
# helpers
# ---------------------------------------------------------------------- #


def _coerce_usage_status(value):
    """Accept ``UsageStatus``, lowercase string (``"good"``), or uppercase
    enum name (``"GOOD"``)."""
    if isinstance(value, UsageStatus):
        return value
    if isinstance(value, str):
        # Normalise uppercase enum names ("GOOD") to lowercase values ("good").
        if value.isupper():
            return UsageStatus[value]
        return UsageStatus(value)
    raise TypeError(f"unsupported status value: {value!r}")


# ---------------------------------------------------------------------- #
# helpers
# ---------------------------------------------------------------------- #


def fake_status(
    *,
    account_id: str = "acc1",
    provider: str | Provider = "claude",
    alias: str = "acc",
    five_hour_status: str | UsageStatus = "GOOD",
    seven_day_status: str | UsageStatus = "GOOD",
    five_hour_percent: float | None = 10.0,
    seven_day_percent: float | None = 20.0,
    error: str | None = None,
) -> LiveStatus:
    """Build a LiveStatus with sensible defaults for tray tests."""
    p = provider if isinstance(provider, Provider) else Provider.parse(provider)
    fh = _coerce_usage_status(five_hour_status)
    sd = _coerce_usage_status(seven_day_status)
    return LiveStatus(
        account_id=account_id,
        provider=p,
        alias=alias,
        five_hour_status=fh,
        seven_day_status=sd,
        five_hour_percent=five_hour_percent,
        seven_day_percent=seven_day_percent,
        five_hour_resets_at=None,
        seven_day_resets_at=None,
        pace_projection_percent=None,
        pace_label="ok",
        fetched_at=datetime.now(timezone.utc),
        error=error,
    )


# ---------------------------------------------------------------------- #
# construction & unavailability
# ---------------------------------------------------------------------- #


def test_tray_unavailable_when_gtk_missing(monkeypatch):
    """Without GTK, ``TrayIcon(...)`` raises ``TrayUnavailable``."""
    import custats.ui.tray as tray_mod

    monkeypatch.setattr(tray_mod, "_GTK_OK", False, raising=False)
    monkeypatch.setattr(tray_mod, "_GTK_PROBED", True, raising=False)

    with pytest.raises(TrayUnavailable):
        TrayIcon(
            on_open_dashboard=lambda: None,
            on_quit=lambda: None,
            on_refresh=lambda: None,
        )


# ---------------------------------------------------------------------- #
# _worst_status (R1 regression)
# ---------------------------------------------------------------------- #


class TestWorstStatus:
    def test_picks_critical_from_seven_day_window(self):
        """R1: an account's seven_day CRITICAL must dominate, even when the
        five_hour window is GOOD. The previous implementation only inspected
        ``five_hour_status``.
        """
        statuses = {
            "a": fake_status(five_hour_status="GOOD", seven_day_status="CRITICAL"),
        }
        assert _worst_status(statuses) == "CRITICAL"

    def test_unknown_when_no_statuses(self):
        assert _worst_status({}) == "UNKNOWN"

    def test_caution_from_five_hour_window(self):
        statuses = {"a": fake_status(five_hour_status="CAUTION")}
        assert _worst_status(statuses) == "CAUTION"

    def test_picks_at_limit_above_critical(self):
        statuses = {
            "a": fake_status(five_hour_status="CRITICAL"),
            "b": fake_status(account_id="b", five_hour_status="AT_LIMIT"),
        }
        assert _worst_status(statuses) == "AT_LIMIT"


class TestWorstAccountTieBreak:
    """R8: ties must resolve deterministically by (severity, Provider order,
    alias) so the glyph doesn't flicker between two equal-severity accounts.
    ``max()`` returns the highest tuple — so cursor (index 3) > codex (1)
    > claude (0), and lexically larger aliases win."""

    def test_breaks_ties_by_provider_order(self):
        statuses = {
            "a": fake_status(account_id="a", provider="claude", alias="aaa",
                             five_hour_status="CRITICAL"),
            "b": fake_status(account_id="b", provider="codex", alias="aaa",
                             five_hour_status="CRITICAL"),
        }
        chosen = _worst_account(statuses)
        # codex (index 1) wins over claude (index 0) because the tuple is
        # ascending and ``max()`` picks the highest.
        assert chosen.provider is Provider.CODEX

    def test_breaks_ties_by_alias_when_provider_same(self):
        statuses = {
            "a": fake_status(account_id="a", provider="codex", alias="aaa",
                             five_hour_status="CRITICAL"),
            "b": fake_status(account_id="b", provider="codex", alias="zzz",
                             five_hour_status="CRITICAL"),
        }
        chosen = _worst_account(statuses)
        assert chosen.alias == "zzz"


# ---------------------------------------------------------------------- #
# icon rendering
# ---------------------------------------------------------------------- #


def test_tray_icon_set_with_glyph(monkeypatch):
    """With one Claude GOOD status, the indicator gets a 'C' label."""
    install_gtk_mocks()
    tray = TrayIcon(
        on_open_dashboard=lambda: None,
        on_quit=lambda: None,
        on_refresh=lambda: None,
        statuses={"a1": fake_status(provider="claude", five_hour_status="GOOD")},
    )
    indicator = tray._indicator
    assert indicator.set_icon_full.called
    label = indicator.set_icon_full.call_args[0][1]
    assert isinstance(label, str) and label
    assert "C" in label


def test_tray_icon_uses_critical_colour_for_worst(monkeypatch):
    """When accounts span GOOD + CRITICAL, the icon uses the CRITICAL hex
    from docs/design-tokens.md §1 (#FF7A6B)."""
    install_gtk_mocks()
    tray = TrayIcon(
        on_open_dashboard=lambda: None,
        on_quit=lambda: None,
        on_refresh=lambda: None,
        statuses={
            "a": fake_status(account_id="a", provider="claude", five_hour_status="GOOD"),
            "b": fake_status(account_id="b", provider="codex", five_hour_status="CRITICAL"),
        },
    )
    last_args = tray._indicator.set_icon_full.call_args[0]
    assert last_args[0] == "custats-X-#FF7A6B"


def test_idle_glyph_when_no_accounts(monkeypatch):
    """Empty snapshot still triggers ``set_icon_full`` with the idle glyph.

    Phase 9b (design-tokens-v3 §1) replaces v2's ``\u2026`` ellipsis with
    the UNKNOWN outline circle ``\u25CB`` — the ellipsis read as noise
    in a 3px horizontal tray slot.
    """
    install_gtk_mocks()
    tray = TrayIcon(
        on_open_dashboard=lambda: None,
        on_quit=lambda: None,
        on_refresh=lambda: None,
        statuses={},
    )
    assert tray._indicator.set_icon_full.called
    label = tray._indicator.set_icon_full.call_args[0][1]
    assert isinstance(label, str) and label
    # v3 §1 — idle key uses the UNKNOWN outline circle, not an ellipsis.
    assert "\u25CB" in tray._indicator.set_icon_full.call_args[0][0]
    assert "\u2026" not in tray._indicator.set_icon_full.call_args[0][0]


def test_update_does_not_duplicate_calls(monkeypatch):
    """Re-rendering with the same status doesn't re-set the icon."""
    install_gtk_mocks()
    tray = TrayIcon(
        on_open_dashboard=lambda: None,
        on_quit=lambda: None,
        on_refresh=lambda: None,
        statuses={"a1": fake_status()},
    )
    call_count = tray._indicator.set_icon_full.call_count
    tray.update({"a1": fake_status()})
    assert tray._indicator.set_icon_full.call_count == call_count


def test_shutdown_is_idempotent(monkeypatch):
    install_gtk_mocks()
    tray = TrayIcon(
        on_open_dashboard=lambda: None,
        on_quit=lambda: None,
        on_refresh=lambda: None,
    )
    tray.shutdown()
    tray.shutdown()  # should not raise


# ---------------------------------------------------------------------- #
# R9: partial-init guard
# ---------------------------------------------------------------------- #


def test_shutdown_safe_when_indicator_never_built(monkeypatch):
    """R9: ``shutdown`` must be safe even if ``__init__`` raised before
    ``_indicator`` was assigned. Simulate that by deleting the attribute."""
    import custats.ui.tray as tray_mod

    install_gtk_mocks()
    tray = TrayIcon(
        on_open_dashboard=lambda: None,
        on_quit=lambda: None,
        on_refresh=lambda: None,
    )
    delattr(tray, "_indicator")
    tray.shutdown()  # must not raise


# ---------------------------------------------------------------------- #
# R4: menu rebuilds on update
# ---------------------------------------------------------------------- #


class TestTrayMenuRebuilds:
    def test_menu_rebuilt_on_update(self, monkeypatch):
        """R4: every ``update(...)`` call must rebuild the right-click menu
        via ``PopupMenu`` and call ``indicator.set_menu`` again."""
        install_gtk_mocks()
        tray = TrayIcon(
            on_open_dashboard=lambda: None,
            on_refresh=lambda: None,
            on_quit=lambda: None,
        )
        set_menu_calls_before = tray._indicator.set_menu.call_count
        tray.update({"a": fake_status(account_id="a", alias="work")})
        # set_menu called again after update().
        assert tray._indicator.set_menu.call_count > set_menu_calls_before
        last_menu = tray._indicator.set_menu.call_args_list[-1].args[0]
        # 1 account row + 1 separator + 3 footer = 5 append calls.
        assert len(last_menu.append.call_args_list) >= 4

    def test_initial_menu_is_empty_state(self, monkeypatch):
        """R4: the first ``set_menu`` call (during ``__init__`` with empty
        statuses) must produce the empty-state menu + 3 footer items."""
        install_gtk_mocks()
        tray = TrayIcon(
            on_open_dashboard=lambda: None,
            on_refresh=lambda: None,
            on_quit=lambda: None,
        )
        assert tray._indicator.set_menu.called
        first_menu = tray._indicator.set_menu.call_args_list[0].args[0]
        # 1 empty-state label + 1 separator + 3 footer = 5 appends.
        assert len(first_menu.append.call_args_list) >= 4


# ---------------------------------------------------------------------- #
# R8: glyph deterministic on ties
# ---------------------------------------------------------------------- #


class TestGlyphDeterministicOnTies:
    def test_glyph_deterministic_on_ties(self, monkeypatch):
        """R8: two CRITICAL accounts (different providers) → the same glyph
        is chosen across two consecutive ``update(...)`` calls. Without
        tie-breaking, ``max()`` could flicker between dict insertion orders
        when callbacks race."""
        install_gtk_mocks()
        tray = TrayIcon(
            on_open_dashboard=lambda: None,
            on_refresh=lambda: None,
            on_quit=lambda: None,
        )
        statuses_a = {
            "a": fake_status(account_id="a", provider="cursor", alias="zzz",
                             five_hour_status="CRITICAL"),
            "b": fake_status(account_id="b", provider="codex", alias="aaa",
                             five_hour_status="CRITICAL"),
        }
        statuses_b = dict(reversed(list(statuses_a.items())))
        tray.update(statuses_a)
        key_a = tray._indicator.set_icon_full.call_args_list[-1].args[0]
        tray.update(statuses_b)
        key_b = tray._indicator.set_icon_full.call_args_list[-1].args[0]
        assert key_a == key_b


# ---------------------------------------------------------------------- #
# symbol tables
# ---------------------------------------------------------------------- #


def test_colour_table_has_all_statuses():
    assert set(_STATUS_COLOURS_HEX) == {"GOOD", "CAUTION", "CRITICAL", "AT_LIMIT", "UNKNOWN"}


def test_colour_table_matches_design_tokens():
    """Phase 9b — the tray palette must match design-tokens-v3 §1.

    v3 darkens GOOD/CAUTION foregrounds (``#3a9d5d``/``#d4a72c``) for chroma
    separation at 16px; CRITICAL/AT_LIMIT/UNKNOWN are unchanged from v2
    because they're already distinct.
    """
    assert _STATUS_COLOURS_HEX["GOOD"] == "#3a9d5d"
    assert _STATUS_COLOURS_HEX["CAUTION"] == "#d4a72c"
    assert _STATUS_COLOURS_HEX["CRITICAL"] == "#FF7A6B"
    assert _STATUS_COLOURS_HEX["AT_LIMIT"] == "#FF4D4D"
    assert _STATUS_COLOURS_HEX["UNKNOWN"] == "#8A8F98"


def test_provider_glyph_table_has_all_providers():
    # Phase 9b — v2 design tokens cover 10 providers; Phase 8c added
    # Copilot (11th). Single-letter glyphs collide intentionally for popup
    # disambiguation; the tray only needs the single letter (see
    # docs/design-tokens-v2.md §3).
    expected = {"claude", "chatgpt", "codex", "copilot", "gemini", "grok",
                "openrouter", "deepseek", "mistral", "kimi", "cursor"}
    assert set(_PROVIDER_GLYPH) == expected


def test_tray_uses_two_letter_glyph_in_menu(monkeypatch):
    """Phase 8b — the popup rows built for the menu must contain the
    2-letter glyph (e.g. ``CL`` for Claude, ``CH`` for ChatGPT) so the
    tray menu disambiguates collisions without relying on colour.

    Implementation note: we monkeypatch :class:`PopupMenu` so the
    ``TrayIcon.update`` call captures the popup it built, then assert on
    :attr:`PopupMenu.account_rows` (Phase 8b manual tracking list — see
    docs/design-tokens-v2.md §13). This keeps the test independent of
    how the popup physically embeds the glyph in its widget tree.
    """
    from custats.ui._glyphs import PROVIDER_GLYPH_2
    import custats.ui.popup as popup_mod

    captured: list = []

    real_init = popup_mod.PopupMenu.__init__

    def _capturing_init(self, **kwargs):
        real_init(self, **kwargs)
        captured.append(self)

    monkeypatch.setattr(popup_mod.PopupMenu, "__init__", _capturing_init)

    install_gtk_mocks()
    TrayIcon(
        on_open_dashboard=lambda: None,
        on_refresh=lambda: None,
        on_quit=lambda: None,
        statuses={
            "a": fake_status(account_id="a", provider="claude", alias="work"),
            "b": fake_status(account_id="b", provider="chatgpt", alias="personal"),
            "c": fake_status(account_id="c", provider="gemini", alias="team"),
            "d": fake_status(account_id="d", provider="grok", alias="g1"),
        },
    )
    assert captured, "TrayIcon.update never built a PopupMenu"
    popup = captured[-1]
    popup.build()
    by_alias = {row.alias: row for row in popup.account_rows}
    # Each provider's 2-letter glyph appears on its card.
    assert by_alias["work"].glyph_2 == PROVIDER_GLYPH_2[Provider.CLAUDE]
    assert by_alias["personal"].glyph_2 == PROVIDER_GLYPH_2[Provider.CHATGPT]
    assert by_alias["team"].glyph_2 == PROVIDER_GLYPH_2[Provider.GEMINI]
    assert by_alias["g1"].glyph_2 == PROVIDER_GLYPH_2[Provider.GROK]


def test_worst_account_deterministic_on_tiebreaks(monkeypatch):
    """Three-way tie: cursor (idx 9) > codex (2) > claude (0). The
    deterministic tiebreak is provider enum order, then alias. Higher index
    wins under ``max()`` of an ascending tuple."""
    install_gtk_mocks()
    tray = TrayIcon(
        on_open_dashboard=lambda: None,
        on_refresh=lambda: None,
        on_quit=lambda: None,
    )
    statuses_claude_first = {
        "a": fake_status(account_id="a", provider="claude", alias="aaa",
                          five_hour_status="CRITICAL"),
        "b": fake_status(account_id="b", provider="codex", alias="aaa",
                          five_hour_status="CRITICAL"),
        "c": fake_status(account_id="c", provider="cursor", alias="aaa",
                          five_hour_status="CRITICAL"),
    }
    statuses_cursor_first = dict(reversed(list(statuses_claude_first.items())))
    tray.update(statuses_claude_first)
    key_first = tray._indicator.set_icon_full.call_args_list[-1].args[0]
    tray.update(statuses_cursor_first)
    key_second = tray._indicator.set_icon_full.call_args_list[-1].args[0]
    assert key_first == key_second
    # Cursor's tray glyph is "U" and CRITICAL colour is #FF7A6B.
    assert key_first == "custats-U-#FF7A6B"


def test_idle_glyph_when_no_accounts_v2(monkeypatch):
    """Phase 9b — idle glyph is the UNKNOWN outline circle ``\u25CB`` in
    UNKNOWN colour (``#8A8F98``) when there are no accounts. The shape
    replaces v2's ``\u2026`` ellipsis, which read as noise in 3px
    horizontal tray slots."""
    from custats.ui._glyphs import _STATUS_SHAPE, PROVIDER_GLYPH

    install_gtk_mocks()
    tray = TrayIcon(
        on_open_dashboard=lambda: None,
        on_refresh=lambda: None,
        on_quit=lambda: None,
        statuses={},
    )
    last_key = tray._indicator.set_icon_full.call_args_list[-1].args[0]
    # Idle shape (U+25CB) and UNKNOWN colour (#8A8F98) are baked into the key.
    assert _STATUS_SHAPE["UNKNOWN"] in last_key
    assert "#8A8F98" in last_key
    # The single-letter provider glyph map must NOT be used for the idle
    # state — that's a v2 invariant.
    for glyph in PROVIDER_GLYPH.values():
        assert last_key != f"custats-{glyph}-#8A8F98"


# ---------------------------------------------------------------------- #
# Phase 9b (design-tokens-v3 §1) — shape variants per status
# ---------------------------------------------------------------------- #


def test_tray_uses_shape_for_good_status(monkeypatch):
    """v3 §1 — a single GOOD account renders the GOOD shape (``\u25CB``)
    in the tray icon key, not the provider letter. The shape replaces
    the letter so severity reads at a glance from outline alone."""
    from custats.ui._glyphs import _STATUS_SHAPE

    install_gtk_mocks()
    tray = TrayIcon(
        on_open_dashboard=lambda: None,
        on_quit=lambda: None,
        on_refresh=lambda: None,
        statuses={"a1": fake_status(provider="claude", five_hour_status="GOOD")},
    )
    last_key = tray._indicator.set_icon_full.call_args_list[-1].args[0]
    assert _STATUS_SHAPE["GOOD"] in last_key
    assert tray._last_shape == _STATUS_SHAPE["GOOD"]


def test_tray_uses_shape_for_caution_status(monkeypatch):
    """v3 §1 — single CAUTION account renders the half-filled circle
    (``\u25D0``), the CAUTION shape."""
    from custats.ui._glyphs import _STATUS_SHAPE

    install_gtk_mocks()
    tray = TrayIcon(
        on_open_dashboard=lambda: None,
        on_quit=lambda: None,
        on_refresh=lambda: None,
        statuses={"a1": fake_status(
            provider="codex", five_hour_status="CAUTION",
            seven_day_status="CAUTION",
        )},
    )
    last_key = tray._indicator.set_icon_full.call_args_list[-1].args[0]
    assert _STATUS_SHAPE["CAUTION"] in last_key
    assert tray._last_shape == _STATUS_SHAPE["CAUTION"]


def test_tray_uses_shape_for_critical_status(monkeypatch):
    """v3 §1 — single CRITICAL account renders the triangle outline
    (``\u25B3``), the CRITICAL shape."""
    from custats.ui._glyphs import _STATUS_SHAPE

    install_gtk_mocks()
    tray = TrayIcon(
        on_open_dashboard=lambda: None,
        on_quit=lambda: None,
        on_refresh=lambda: None,
        statuses={"a1": fake_status(
            provider="gemini", five_hour_status="CRITICAL",
            seven_day_status="CRITICAL",
        )},
    )
    last_key = tray._indicator.set_icon_full.call_args_list[-1].args[0]
    assert _STATUS_SHAPE["CRITICAL"] in last_key
    assert tray._last_shape == _STATUS_SHAPE["CRITICAL"]


def test_tray_uses_shape_for_at_limit_status(monkeypatch):
    """v3 §1 — single AT_LIMIT account renders the filled square
    (``\u25A0``), the AT_LIMIT shape."""
    from custats.ui._glyphs import _STATUS_SHAPE

    install_gtk_mocks()
    tray = TrayIcon(
        on_open_dashboard=lambda: None,
        on_quit=lambda: None,
        on_refresh=lambda: None,
        statuses={"a1": fake_status(
            provider="openrouter", five_hour_status="AT_LIMIT",
            seven_day_status="AT_LIMIT",
        )},
    )
    last_key = tray._indicator.set_icon_full.call_args_list[-1].args[0]
    assert _STATUS_SHAPE["AT_LIMIT"] in last_key
    assert tray._last_shape == _STATUS_SHAPE["AT_LIMIT"]


def test_tray_shape_colour_matches_status(monkeypatch):
    """v3 §1 — each shape is paired with its tray-only colour from
    :data:`custats.ui.tray._STATUS_COLOURS_HEX`. Shape+colour form a
    redundant signal so colour-blind users can still read severity from
    the outline."""
    cases = (
        ("GOOD",      "\u25CB", "#3a9d5d"),
        ("CAUTION",   "\u25D0", "#d4a72c"),
        ("CRITICAL",  "\u25B3", "#FF7A6B"),
        ("AT_LIMIT",  "\u25A0", "#FF4D4D"),
    )
    for status_name, shape_glyph, expected_colour in cases:
        install_gtk_mocks()
        tray = TrayIcon(
            on_open_dashboard=lambda: None,
            on_quit=lambda: None,
            on_refresh=lambda: None,
            statuses={"a1": fake_status(
                provider="claude", alias="x",
                five_hour_status=status_name,
                seven_day_status=status_name,
            )},
        )
        last_key = tray._indicator.set_icon_full.call_args_list[-1].args[0]
        assert shape_glyph in last_key, f"{status_name}: missing shape {shape_glyph!r} in {last_key!r}"
        assert expected_colour in last_key, f"{status_name}: missing colour {expected_colour!r} in {last_key!r}"
        assert tray._last_shape == shape_glyph


def test_tray_multi_account_falls_back_to_provider_letter(monkeypatch):
    """v3 §1 (orchestrator note) — with ≥ 2 accounts the tray falls back
    to v2's provider-letter+colour logic, so users can still see *which*
    account is the worst at a glance. Shape is set to ``None`` so the
    visual distinguishes single- vs multi-account modes."""
    install_gtk_mocks()
    tray = TrayIcon(
        on_open_dashboard=lambda: None,
        on_quit=lambda: None,
        on_refresh=lambda: None,
        statuses={
            "a": fake_status(account_id="a", provider="claude", alias="work",
                             five_hour_status="CRITICAL"),
            "b": fake_status(account_id="b", provider="codex", alias="team",
                             five_hour_status="GOOD"),
        },
    )
    last_key = tray._indicator.set_icon_full.call_args_list[-1].args[0]
    # Letter-and-colour format from v2 (cursor wins via deterministic
    # tiebreak — codex is index 1 > claude 0 for equal CRITICAL? No,
    # this is asymmetric; only claude is CRITICAL so worst = claude = "C").
    assert "C" in last_key
    assert "#FF7A6B" in last_key
    assert tray._last_shape is None


# ---------------------------------------------------------------------- #
# v3 §10 — "Open data folder" wiring + status-change alert animation
# ---------------------------------------------------------------------- #


def test_tray_wires_open_data_folder_into_popup(monkeypatch):
    """v3 §10 un-deferred — TrayIcon forwards ``on_open_data_folder`` to
    PopupMenu so the footer item appears."""
    import custats.ui.popup as popup_mod

    install_gtk_mocks()
    captured: list = []

    real_init = popup_mod.PopupMenu.__init__

    def _capturing_init(self, **kwargs):
        real_init(self, **kwargs)
        captured.append(self)

    monkeypatch.setattr(popup_mod.PopupMenu, "__init__", _capturing_init)

    fired: list[str] = []
    TrayIcon(
        on_open_dashboard=lambda: None,
        on_refresh=lambda: None,
        on_quit=lambda: None,
        on_open_data_folder=lambda: fired.append("data"),
        statuses={},
    )
    assert captured, "TrayIcon never built a PopupMenu"
    assert captured[-1]._on_open_data_folder is not None
    captured[-1]._on_open_data_folder()
    assert fired == ["data"]


def test_tray_no_data_folder_handler_passed_when_unspecified(monkeypatch):
    """Without ``on_open_data_folder`` the tray forwards ``None`` and the
    popup hides the footer item (backward-compatible default)."""
    install_gtk_mocks()
    tray = TrayIcon(
        on_open_dashboard=lambda: None,
        on_refresh=lambda: None,
        on_quit=lambda: None,
        statuses={},
    )
    assert tray._on_open_data_folder is None


def test_tray_attention_flips_on_worsening_status(monkeypatch):
    """v3 §10 alert animation — a severity increase (GOOD → CRITICAL)
    flips the indicator to ``IndicatorStatus.ATTENTION``; an equal or
    improving snapshot never does."""
    install_gtk_mocks()
    # Import AFTER install_gtk_mocks(): each install replaces the
    # gi.repository mock, and mock identity is what we assert against.
    from gi.repository import AppIndicator3  # mocked by conftest
    tray = TrayIcon(
        on_open_dashboard=lambda: None,
        on_refresh=lambda: None,
        on_quit=lambda: None,
        statuses={"a1": fake_status(provider="claude", five_hour_status="GOOD")},
    )
    indicator = tray._indicator
    assert tray._attention_on is False
    assert all(
        c.args[0] is not AppIndicator3.IndicatorStatus.ATTENTION
        for c in indicator.set_status.call_args_list
    ), "initial prime should not alert (severity starts at its baseline)"

    # Worsen: GOOD → CRITICAL must alert.
    tray.update(
        {"a1": fake_status(provider="claude", five_hour_status="CRITICAL")}
    )
    statuses = [c.args[0] for c in indicator.set_status.call_args_list]
    assert AppIndicator3.IndicatorStatus.ATTENTION in statuses

    # Same severity again — no additional alert.
    indicator.set_status.reset_mock()
    tray.update(
        {"a1": fake_status(provider="claude", five_hour_status="CRITICAL")}
    )
    assert not indicator.set_status.call_args_list


def test_tray_attention_reverts_on_improvement(monkeypatch):
    """Recovery (CRITICAL → GOOD) never alerts, and closes an open
    attention window (reverts to ACTIVE)."""
    install_gtk_mocks()
    from gi.repository import AppIndicator3  # mocked by conftest
    tray = TrayIcon(
        on_open_dashboard=lambda: None,
        on_refresh=lambda: None,
        on_quit=lambda: None,
        statuses={"a1": fake_status(provider="claude", five_hour_status="GOOD")},
    )
    tray.update(
        {"a1": fake_status(provider="claude", five_hour_status="AT_LIMIT")}
    )
    assert tray._attention_on is True

    # Improve: AT_LIMIT → GOOD — no new alert, ATTENTION reverts.
    tray.update(
        {"a1": fake_status(provider="claude", five_hour_status="GOOD")}
    )
    indicator = tray._indicator
    last_status = indicator.set_status.call_args_list[-1].args[0]
    assert last_status is AppIndicator3.IndicatorStatus.ACTIVE
    assert tray._attention_on is False


def test_tray_attention_blink_window_expires(monkeypatch):
    """The attention window is time-bounded: forcing the monotonic
    deadline into the past makes the next update revert to ACTIVE even
    without a severity change."""
    import custats.ui.tray as tray_mod

    install_gtk_mocks()
    from gi.repository import AppIndicator3  # mocked by conftest
    tray = TrayIcon(
        on_open_dashboard=lambda: None,
        on_refresh=lambda: None,
        on_quit=lambda: None,
        statuses={"a1": fake_status(provider="claude", five_hour_status="GOOD")},
    )
    tray.update(
        {"a1": fake_status(provider="claude", five_hour_status="CAUTION")}
    )
    assert tray._attention_on is True

    # Simulate the blink window expiring.
    tray._alert_blink_until = 0.0
    tray.update(
        {"a1": fake_status(provider="claude", five_hour_status="CAUTION")}
    )
    last_status = tray._indicator.set_status.call_args_list[-1].args[0]
    assert last_status is AppIndicator3.IndicatorStatus.ACTIVE
    assert tray_mod._ALERT_BLINK_SECONDS > 0


def test_tray_forwards_pace_history_for(monkeypatch):
    """v3 §10 — TrayIcon forwards ``pace_history_for`` to the popup so the
    animated sparkline renders DB-backed history; default stays ``None``."""
    import custats.ui.popup as popup_mod

    install_gtk_mocks()
    captured: list = []

    real_init = popup_mod.PopupMenu.__init__

    def _capturing_init(self, **kwargs):
        real_init(self, **kwargs)
        captured.append(self)

    monkeypatch.setattr(popup_mod.PopupMenu, "__init__", _capturing_init)

    def history_for(status):
        return [40.0, 60.0]

    TrayIcon(
        on_open_dashboard=lambda: None,
        on_refresh=lambda: None,
        on_quit=lambda: None,
        pace_history_for=history_for,
        statuses={
            "a1": fake_status(account_id="a1", provider="claude", alias="work"),
        },
    )
    assert captured, "TrayIcon never built a PopupMenu"
    popup = captured[-1]
    assert popup._pace_history_for is history_for

    install_gtk_mocks()
    captured.clear()
    TrayIcon(
        on_open_dashboard=lambda: None,
        on_refresh=lambda: None,
        on_quit=lambda: None,
        statuses={},
    )
    assert captured[-1]._pace_history_for is None
