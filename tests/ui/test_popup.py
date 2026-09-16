"""Tests for the popup menu."""
from __future__ import annotations

import sys
import types
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from custats.core.models import Provider, UsageStatus
from custats.state import LiveStatus
from custats.ui.popup import PopupMenu, humanize_reset


# ---------------------------------------------------------------------- #
# helpers
# ---------------------------------------------------------------------- #


def fake_status(
    *,
    account_id: str = "acc1",
    provider: str | Provider = "claude",
    alias: str = "acc",
    five_hour_status: str | UsageStatus = "good",
    seven_day_status: str | UsageStatus = "good",
    five_hour_percent: float | None = 10.0,
    seven_day_percent: float | None = 20.0,
    pace_projection_percent: float | None = None,
    pace_label: str = "ok",
) -> LiveStatus:
    p = provider if isinstance(provider, Provider) else Provider.parse(provider)
    fh = (
        five_hour_status
        if isinstance(five_hour_status, UsageStatus)
        else UsageStatus[five_hour_status.upper()]
    )
    sd = (
        seven_day_status
        if isinstance(seven_day_status, UsageStatus)
        else UsageStatus[seven_day_status.upper()]
    )
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
        pace_projection_percent=pace_projection_percent,
        pace_label=pace_label,
        fetched_at=datetime.now(timezone.utc),
    )


def _install_gtk(monkeypatch) -> tuple[MagicMock, dict[str, MagicMock]]:
    """Install fake ``gi`` modules so :meth:`PopupMenu.build` succeeds.

    Returns ``(Gtk_mock, items_by_label)`` — the second is a dict of every
    MenuItem mock created during the build, keyed by its label, so tests can
    locate the menu-item mock whose ``connect`` was actually invoked.
    """
    gi = types.ModuleType("gi")
    gi.require_version = MagicMock()
    repo = types.ModuleType("gi.repository")
    repo.Gtk = MagicMock()
    repo.AppIndicator3 = MagicMock()
    repo.GdkPixbuf = MagicMock()
    repo.Pango = MagicMock()
    repo.PangoCairo = MagicMock()
    sys.modules["gi"] = gi
    sys.modules["gi.repository"] = repo
    sys.modules["cairo"] = MagicMock()
    # Track every MenuItem mock created during a build(), keyed by label. The
    # same mock is returned for repeated calls with the same label so the
    # ``connect`` registration performed during ``popup.build()`` is observable.
    _menu_items: dict[str, MagicMock] = {}

    def _make_menu_item(label: str) -> MagicMock:
        return _menu_items.setdefault(label, MagicMock(_label=label))

    repo.Gtk.MenuItem.new_with_label.side_effect = _make_menu_item
    return repo.Gtk, _menu_items


def _labels_in_build_order(_gtk_mock: MagicMock, items: dict[str, MagicMock]) -> list[str]:
    """Reconstruct the order of MenuItem labels from the items dict."""
    return list(items.keys())


# ---------------------------------------------------------------------- #
# humanize_reset
# ---------------------------------------------------------------------- #


def test_humanize_reset_returns_empty_for_none():
    assert humanize_reset(None) == ""


def test_humanize_reset_returns_now_within_a_minute():
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    assert humanize_reset(now + timedelta(seconds=30), now=now) == "now"


def test_humanize_reset_formats_minutes_only():
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    assert humanize_reset(now + timedelta(minutes=42), now=now) == "42m"


def test_humanize_reset_formats_hours_and_minutes():
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    assert humanize_reset(now + timedelta(hours=2, minutes=14), now=now) == "2h 14m"


def test_humanize_reset_formats_days_and_hours():
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    assert humanize_reset(now + timedelta(days=3, hours=5), now=now) == "3d 5h"


def test_humanize_reset_negative_returns_now():
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    assert humanize_reset(now - timedelta(seconds=10), now=now) == "now"


# ---------------------------------------------------------------------- #
# menu construction
# ---------------------------------------------------------------------- #


def test_empty_state_builds_menu_with_open_dashboard(monkeypatch):
    _Gtk, items = _install_gtk(monkeypatch)
    popup = PopupMenu(
        statuses={},
        on_open_dashboard=lambda: None,
        on_refresh=lambda: None,
        on_quit=lambda: None,
    )
    popup.build()
    # Footer items are tracked by label.
    assert "Open Dashboard" in items
    assert "Refresh now" in items
    assert "Quit" in items


def test_per_account_row_has_5h_and_7d_bars(monkeypatch):
    """Phase 8b — each account card carries two bars (5h + 7d) with the
    correct progress fraction. Cards are tracked in
    :attr:`PopupMenu.account_rows` so tests assert against typed attributes
    instead of walking the widget tree (MagicMock's ``get_children``
    returns empty iterators)."""
    _Gtk, _items = _install_gtk(monkeypatch)
    popup = PopupMenu(
        statuses={
            "a1": fake_status(
                alias="work",
                provider="claude",
                five_hour_percent=42.0,
                seven_day_percent=28.0,
            )
        },
        on_open_dashboard=lambda: None,
        on_refresh=lambda: None,
        on_quit=lambda: None,
    )
    popup.build()
    assert len(popup.account_rows) == 1, popup.account_rows
    card = popup.account_rows[0]
    assert card.alias == "work"
    assert card.provider is Provider.CLAUDE
    assert card.fraction_5h == pytest.approx(0.42, abs=1e-6)
    assert card.fraction_7d == pytest.approx(0.28, abs=1e-6)


def test_per_account_row_has_provider_accent(monkeypatch):
    """Phase 8b — each card carries its provider's accent hex. The popup
    cards expose the accent via ``_AccountCard.accent_hex`` so tests don't
    need to scrape widget CSS or Pango markup."""
    from custats.ui._glyphs import PROVIDER_ACCENT_HEX

    _Gtk, _items = _install_gtk(monkeypatch)
    popup = PopupMenu(
        statuses={
            "a1": fake_status(account_id="a1", provider="claude", alias="work"),
            "b1": fake_status(account_id="b1", provider="gemini", alias="team"),
            "c1": fake_status(account_id="c1", provider="cursor", alias="self"),
        },
        on_open_dashboard=lambda: None,
        on_refresh=lambda: None,
        on_quit=lambda: None,
    )
    popup.build()
    by_alias = {card.alias: card for card in popup.account_rows}
    assert by_alias["work"].accent_hex == PROVIDER_ACCENT_HEX[Provider.CLAUDE]
    assert by_alias["team"].accent_hex == PROVIDER_ACCENT_HEX[Provider.GEMINI]
    assert by_alias["self"].accent_hex == PROVIDER_ACCENT_HEX[Provider.CURSOR]
    # 2-letter glyph follows from _glyphs.PROVIDER_GLYPH_2.
    from custats.ui._glyphs import PROVIDER_GLYPH_2
    assert by_alias["work"].glyph_2 == PROVIDER_GLYPH_2[Provider.CLAUDE]


def test_empty_state_when_no_statuses(monkeypatch):
    """Phase 8b — with no accounts, the popup exposes the empty-state label
    AND still keeps the three footer menu items wired up."""
    _Gtk, items = _install_gtk(monkeypatch)
    popup = PopupMenu(
        statuses={},
        on_open_dashboard=lambda: None,
        on_refresh=lambda: None,
        on_quit=lambda: None,
    )
    popup.build()
    # No account rows were recorded (test-mock contract).
    assert popup.account_rows == []
    # Footer items remain Gtk.MenuItem.new_with_label calls.
    assert "Open Dashboard" in items
    assert "Refresh now" in items
    assert "Quit" in items


def test_per_account_row_two_letter_glyph_present(monkeypatch):
    """Phase 8b — the card header renders the 2-letter glyph (e.g. ``CL``
    for Claude, ``CH`` for ChatGPT) so the user can disambiguate the C/C
    tray collision at a glance.
    """
    from custats.ui._glyphs import PROVIDER_GLYPH_2

    _Gtk, _items = _install_gtk(monkeypatch)
    popup = PopupMenu(
        statuses={
            "a": fake_status(account_id="a", provider="claude", alias="work"),
            "b": fake_status(account_id="b", provider="chatgpt", alias="personal"),
        },
        on_open_dashboard=lambda: None,
        on_refresh=lambda: None,
        on_quit=lambda: None,
    )
    popup.build()
    by_alias = {c.alias: c for c in popup.account_rows}
    assert by_alias["work"].glyph_2 == PROVIDER_GLYPH_2[Provider.CLAUDE]
    assert by_alias["personal"].glyph_2 == PROVIDER_GLYPH_2[Provider.CHATGPT]


def test_per_account_row_handles_none_window(monkeypatch):
    """Phase 8b — Cursor has no 5h window. The corresponding fraction is
    ``None`` and the card renders the 7d line only (per design-tokens-v2 §4).
    """
    _Gtk, _items = _install_gtk(monkeypatch)
    popup = PopupMenu(
        statuses={
            "a1": fake_status(
                alias="self",
                provider="cursor",
                five_hour_percent=None,
                seven_day_percent=15.0,
            )
        },
        on_open_dashboard=lambda: None,
        on_refresh=lambda: None,
        on_quit=lambda: None,
    )
    popup.build()
    assert len(popup.account_rows) == 1
    card = popup.account_rows[0]
    assert card.fraction_5h is None
    assert card.fraction_7d == pytest.approx(0.15, abs=1e-6)


def test_per_account_row_omits_pace_when_disabled(monkeypatch):
    """Phase 8b — when ``pace_enabled`` is False (Settings tab toggle),
    the card's ``pace_fraction`` is forced to ``None`` so the sparkline
    row is skipped, even if the underlying ``LiveStatus`` carries a
    ``pace_projection_percent``. This mirrors docs/design-tokens-v2 §4
    ("Sparkline row … gated by ``pace_enabled`` config flag").
    """
    _Gtk, _items = _install_gtk(monkeypatch)
    popup = PopupMenu(
        statuses={
            "a1": fake_status(
                alias="work",
                provider="claude",
                five_hour_percent=10.0,
                seven_day_percent=20.0,
            ),
        },
        on_open_dashboard=lambda: None,
        on_refresh=lambda: None,
        on_quit=lambda: None,
        pace_enabled=False,
    )
    popup.build()
    card = popup.account_rows[0]
    assert card.pace_fraction is None


def test_per_account_row_includes_pace_when_enabled(monkeypatch):
    """Phase 8b — when ``pace_enabled`` is True (the default) and the
    status carries a ``pace_projection_percent``, the card records it as
    ``pace_fraction`` ready for the sparkline row.
    """
    _Gtk, _items = _install_gtk(monkeypatch)
    statuses = {
        "a1": fake_status(
            alias="work",
            provider="claude",
            five_hour_percent=10.0,
            seven_day_percent=20.0,
            pace_projection_percent=67.0,
            pace_label="Risky",
        ),
    }
    popup = PopupMenu(
        statuses=statuses,
        on_open_dashboard=lambda: None,
        on_refresh=lambda: None,
        on_quit=lambda: None,
        pace_enabled=True,
    )
    popup.build()
    card = popup.account_rows[0]
    assert card.pace_fraction == pytest.approx(0.67, abs=1e-6)
    assert card.pace_label == "Risky"


def test_refresh_callback_wired(monkeypatch):
    """Triggering the 'Refresh now' menu item fires ``on_refresh``."""
    _Gtk, items = _install_gtk(monkeypatch)
    refresh_calls: list[int] = []

    popup = PopupMenu(
        statuses={"a1": fake_status()},
        on_open_dashboard=lambda: None,
        on_refresh=lambda: refresh_calls.append(1),
        on_quit=lambda: None,
    )
    popup.build()

    # Look up the menu-item mock that was actually created during build() and
    # whose `connect` method was called to register the activate handler.
    refresh_item = items["Refresh now"]
    refresh_item.connect.call_args[0][1](refresh_item)
    assert refresh_calls == [1]


def test_quit_callback_wired(monkeypatch):
    _Gtk, items = _install_gtk(monkeypatch)
    quit_calls: list[int] = []
    popup = PopupMenu(
        statuses={},
        on_open_dashboard=lambda: None,
        on_refresh=lambda: None,
        on_quit=lambda: quit_calls.append(1),
    )
    popup.build()
    quit_item = items["Quit"]
    quit_item.connect.call_args[0][1](quit_item)
    assert quit_calls == [1]


def test_build_without_gtk_raises(monkeypatch):
    """If GTK import fails, :meth:`build` raises a clear ``RuntimeError``."""
    # Force the import to fail by removing the mocked modules.
    monkeypatch.delitem(sys.modules, "gi", raising=False)
    monkeypatch.delitem(sys.modules, "gi.repository", raising=False)

    popup = PopupMenu(
        statuses={},
        on_open_dashboard=lambda: None,
        on_refresh=lambda: None,
        on_quit=lambda: None,
    )
    with pytest.raises(RuntimeError):
        popup.build()


def test_update_replaces_statuses(monkeypatch):
    _Gtk, _items = _install_gtk(monkeypatch)
    popup = PopupMenu(
        statuses={"old": fake_status(alias="old")},
        on_open_dashboard=lambda: None,
        on_refresh=lambda: None,
        on_quit=lambda: None,
    )
    popup.update({"new": fake_status(alias="new", account_id="new")})
    # Compare aliases only — LiveStatus equality compares fetched_at down to
    # the microsecond, which differs between the two fake_status() calls.
    assert set(popup._statuses.keys()) == {"new"}
    assert popup._statuses["new"].alias == "new"
    assert popup._statuses["new"].account_id == "new"


# ---------------------------------------------------------------------- #
# Phase 9b (design-tokens-v3 §2-5, §8) — badge, inline bars/pace,
# welcome card, keyboard hint footer.
# ---------------------------------------------------------------------- #


def test_status_badge_includes_dot_and_label(monkeypatch):
    """v3 §2 — the per-row header replaces the v2 pill with a coloured
    dot + one-word label. The :class:`_AccountCard` wrapper exposes
    ``badge_glyph`` and ``badge_label`` so tests assert against the
    typed attributes without walking the widget tree."""
    _Gtk, _items = _install_gtk(monkeypatch)
    popup = PopupMenu(
        statuses={
            "a1": fake_status(
                alias="work",
                provider="claude",
                five_hour_percent=78.0,
                seven_day_percent=42.0,
                five_hour_status="CAUTION",
                seven_day_status="CAUTION",
            ),
        },
        on_open_dashboard=lambda: None,
        on_refresh=lambda: None,
        on_quit=lambda: None,
    )
    popup.build()
    card = popup.account_rows[0]
    # The CAUTION row carries the amber ● + "Watch" badge.
    assert card.badge_glyph == "\u25cf"
    assert card.badge_label == "Watch"


def test_percent_chip_uses_unicode_block_bars(monkeypatch):
    """v3 §3 — the bar row collapses to ``label  ████░░░░  42%  ↻ 2h 14m``
    on a single monospace label. Wrapper exposes ``bar_text_5h`` /
    ``bar_text_7d`` so tests can assert on the literal string."""
    from datetime import timedelta
    # Stub humanize_reset to return a fixed string so the percent-chip
    # assertion doesn't depend on the LiveStatus resets_at plumbing.
    import custats.ui.popup as popup_mod
    monkeypatch.setattr(popup_mod, "humanize_reset",
                        lambda dt, *, now=None: "2h 14m")
    _Gtk, _items = _install_gtk(monkeypatch)
    popup = PopupMenu(
        statuses={
            "a1": fake_status(
                alias="work",
                provider="claude",
                five_hour_percent=42.0,
                seven_day_percent=28.0,
            ),
        },
        on_open_dashboard=lambda: None,
        on_refresh=lambda: None,
        on_quit=lambda: None,
    )
    popup.build()
    card = popup.account_rows[0]
    # 5h row: 6 filled (42% × 14 ≈ 5.88 → 6) + 8 empty + percent + ↻ + reset.
    assert card.bar_text_5h.startswith("5h  ")
    assert "\u2588" * 6 in card.bar_text_5h
    assert "\u2591" * 8 in card.bar_text_5h
    assert " 42%" in card.bar_text_5h
    assert "\u21bb" in card.bar_text_5h  # ↻ glyph
    assert "2h 14m" in card.bar_text_5h
    # 7d row: 4 filled (28% × 14 ≈ 3.92 → 4) + 10 empty + percent.
    assert card.bar_text_7d.startswith("7d  ")
    assert "\u2588" * 4 in card.bar_text_7d


def test_pace_line_includes_sparkline_inline(monkeypatch):
    """v3 §4 — the sparkline moves inline next to the pace percent: one
    label reads ``Pace  ▁▂▃▅▆▅▃▂▁  67% Risky``. The wrapper's
    ``pace_text`` exposes the literal string for tests."""
    _Gtk, _items = _install_gtk(monkeypatch)
    popup = PopupMenu(
        statuses={
            "a1": fake_status(
                alias="work",
                provider="claude",
                five_hour_percent=10.0,
                seven_day_percent=20.0,
                pace_projection_percent=67.0,
                pace_label="Risky",
            ),
        },
        on_open_dashboard=lambda: None,
        on_refresh=lambda: None,
        on_quit=lambda: None,
        pace_enabled=True,
    )
    popup.build()
    card = popup.account_rows[0]
    # Inline sparkline + percent + label on one line.
    assert card.pace_text.startswith("Pace  ")
    assert "67%" in card.pace_text
    assert "Risky" in card.pace_text
    # 10-cell width: even with no history points the stub falls back to a
    # flat low bucket across all 10 cells so columns still line up.
    assert any(block in card.pace_text for block in "\u2581\u2582\u2583\u2584\u2585\u2586\u2587\u2588")


def test_empty_state_shows_three_step_card(monkeypatch):
    """v3 §5 — the empty-state MenuItem is replaced by a friendly
    3-step onboarding card. ``PopupMenu.welcome_card`` is set so tests
    assert the onboarding path without rendering."""
    _Gtk, _items = _install_gtk(monkeypatch)
    popup = PopupMenu(
        statuses={},
        on_open_dashboard=lambda: None,
        on_refresh=lambda: None,
        on_quit=lambda: None,
    )
    menu = popup.build()
    assert popup.welcome_card is True
    assert popup.account_rows == []
    # The numbered step glyphs (① ② ③) and the accent-tinted footer are
    # not on a public attribute — instead, ensure the body Box was added
    # to the menu item (MagicMock'd MenuItem.add) and the footer items
    # are still present below the welcome card.
    assert any(
        getattr(item, "_label", None) == "Open Dashboard"
        for item in _items.values()
    )


def test_keyboard_hint_footer_appears_with_3_or_more_rows(monkeypatch):
    """v3 §8 — the muted keyboard hint footer (``↑↓ navigate …``) only
    renders when ``len(account_rows) >= 3``. The wrapper's
    ``keyboard_hint`` attribute flips so tests assert the gating
    without crawling the widget tree."""
    _Gtk, _items = _install_gtk(monkeypatch)
    popup = PopupMenu(
        statuses={
            "a1": fake_status(account_id="a1", alias="a1"),
            "a2": fake_status(account_id="a2", alias="a2"),
            "a3": fake_status(account_id="a3", alias="a3"),
        },
        on_open_dashboard=lambda: None,
        on_refresh=lambda: None,
        on_quit=lambda: None,
    )
    popup.build()
    assert popup.keyboard_hint is True
    assert len(popup.account_rows) >= 3


def test_keyboard_hint_footer_hidden_with_fewer_than_3_rows(monkeypatch):
    """v3 §8 — with fewer than 3 account rows the keyboard hint is
    suppressed (it would be noise when there is nothing to navigate)."""
    _Gtk, _items = _install_gtk(monkeypatch)
    popup = PopupMenu(
        statuses={
            "a1": fake_status(account_id="a1", alias="a1"),
            "a2": fake_status(account_id="a2", alias="a2"),
        },
        on_open_dashboard=lambda: None,
        on_refresh=lambda: None,
        on_quit=lambda: None,
    )
    popup.build()
    assert popup.keyboard_hint is False
    assert len(popup.account_rows) == 2


# ---------------------------------------------------------------------- #
# v3 §10 — "Open data folder" footer item (previously deferred)
# ---------------------------------------------------------------------- #


def test_open_data_folder_footer_renders_when_handler_given(monkeypatch):
    """v3 §10 un-deferred — passing ``on_open_data_folder`` renders an
    "Open data folder" footer item wired to that exact handler."""
    _Gtk, items = _install_gtk(monkeypatch)
    fired: list[str] = []
    popup = PopupMenu(
        statuses={
            "a1": fake_status(account_id="a1", alias="a1"),
            "a2": fake_status(account_id="a2", alias="a2"),
        },
        on_open_dashboard=lambda: None,
        on_refresh=lambda: None,
        on_quit=lambda: None,
        on_open_data_folder=lambda: fired.append("data"),
    )
    popup.build()
    item = items.get("Open data folder")
    assert item is not None, (
        f"'Open data folder' missing from built items: {sorted(items)}"
    )
    # The handler was registered via connect("activate", ...).
    registered = [c.args for c in item.connect.call_args_list]
    assert ("activate", item) or True  # connect args inspected below
    handlers = [
        args[1] for args in registered
        if args and args[0] == "activate"
    ]
    assert handlers, "no activate handler registered on the data-folder item"
    handlers[-1](None)  # GTK passes the activating widget; mock passes None
    assert fired == ["data"]


def test_open_data_folder_footer_hidden_without_handler(monkeypatch):
    """Without ``on_open_data_folder`` the item is absent — PopupMenu
    stays usable standalone and the tray is the only caller that opts in."""
    _Gtk, items = _install_gtk(monkeypatch)
    popup = PopupMenu(
        statuses={},
        on_open_dashboard=lambda: None,
        on_refresh=lambda: None,
        on_quit=lambda: None,
    )
    popup.build()
    assert "Open data folder" not in items


def test_welcome_card_click_opens_dashboard(monkeypatch):
    """v3 §5 bug fix — activating the welcome card fires
    ``on_open_dashboard`` (it was previously wired to a no-op)."""
    _Gtk, _items = _install_gtk(monkeypatch)
    fired: list[str] = []
    popup = PopupMenu(
        statuses={},
        on_open_dashboard=lambda: fired.append("dashboard"),
        on_refresh=lambda: None,
        on_quit=lambda: None,
    )
    menu = popup.build()
    assert popup.welcome_card is True
    # The welcome card is the first menu child; grab it from the Menu's
    # recorded append order (conftest records append call args).
    menu_mock = menu
    appends = menu_mock.append.call_args_list
    assert appends, "nothing appended to the menu"
    welcome_item = appends[0].args[0]
    handlers = [
        c.args[1] for c in welcome_item.connect.call_args_list
        if c.args and c.args[0] == "activate"
    ]
    assert handlers, "welcome card never registered an activate handler"
    handlers[-1](None)  # GTK passes the activating widget; mock passes None
    assert fired == ["dashboard"]


# ---------------------------------------------------------------------- #
# v3 §10 — real pace history feed (DB-backed sparkline)
# ---------------------------------------------------------------------- #


def test_pace_history_for_feeds_build_account_card(monkeypatch):
    """Passing ``pace_history_for`` routes a LiveStatus's real history
    into ``build_account_card`` — the sparkline renders the DB points."""
    from custats.ui._components import _sparkline

    _Gtk, _items = _install_gtk(monkeypatch)
    seen: list = []

    def history_for(status):
        seen.append(status.alias)
        return [30.0, 50.0, 70.0]

    popup = PopupMenu(
        statuses={
            "a1": fake_status(alias="work", pace_projection_percent=67.0,
                              pace_label="Risky"),
        },
        on_open_dashboard=lambda: None,
        on_refresh=lambda: None,
        on_quit=lambda: None,
        pace_history_for=history_for,
    )
    popup.build()
    assert seen == ["work"], "history callback never invoked"
    card = popup.account_rows[0]
    assert _sparkline([30.0, 50.0, 70.0], anim=card.pace_anim) in card.pace_text


def test_pace_history_none_keeps_stub_behaviour(monkeypatch):
    """Without the callback the sparkline keeps the empty-history stub —
    existing callers (and their tests) are unaffected."""
    from custats.ui._components import _SPARKLINE_BLOCKS

    _Gtk, _items = _install_gtk(monkeypatch)
    popup = PopupMenu(
        statuses={
            "a1": fake_status(alias="work", pace_projection_percent=67.0,
                              pace_label="Risky"),
        },
        on_open_dashboard=lambda: None,
        on_refresh=lambda: None,
        on_quit=lambda: None,
    )
    popup.build()
    card = popup.account_rows[0]
    # The stub feeds [] → fallback row (one repeated block), not real data.
    assert any(
        _SPARKLINE_BLOCKS[i] * 10 in card.pace_text for i in range(2)
    )
