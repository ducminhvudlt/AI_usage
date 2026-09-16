"""Popup menu attached to the tray icon. Built lazily on each open.

Phase 8b: replaces the flat ``GtkMenuItem`` rows with a vertical stack of
card widgets (a ``Gtk.Box`` per account) — see
``docs/design-tokens-v2.md`` §4.

Phase 9b (design-tokens-v3):
- §5 — empty state becomes a 3-step welcome card instead of a dim label.
- §8 — keyboard hint footer renders only when there are ≥ 3 account rows.
The heavy card-building logic lives in
:mod:`custats.ui._components` so this file stays under the line budget
(§§ and the test-mock contract in §13).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

from ..core.models import Provider
from ._components import _AccountCard, build_account_card
from ._glyphs import _STATUS_DOT, _STATUS_LEGEND_TEXT
from ._helpers import esc


def humanize_reset(dt: datetime | None, *, now: datetime | None = None) -> str:
    """Format a reset datetime as a human-friendly relative string.

    Returns "" if ``dt`` is None; "now" for values within the next minute;
    otherwise ``"Xd Yh"``, ``"Xh Ym"``, or ``"Xm"`` depending on size.
    """
    if dt is None:
        return ""
    now = now or datetime.now(timezone.utc)
    delta = dt - now
    secs = int(delta.total_seconds())
    if secs < 60:
        return "now"
    days, rem = divmod(secs, 86400)
    hours, rem = divmod(rem, 3600)
    mins = rem // 60
    if days >= 1:
        return f"{days}d {hours}h"
    if hours >= 1:
        return f"{hours}h {mins}m"
    return f"{mins}m"


# v3 §8 — keyboard hint footer shown only when ``len(account_rows) >= 3``.
# Mirrors the v2 §11 footer but adds a navigation hint and gates on row count.
_KEYBOARD_HINT_TEXT = "\u2191\u2193 navigate \u00b7 Enter open \u00b7 R refresh \u00b7 Q quit"
_KEYBOARD_HINT_MIN_ROWS = 3

# v3 §5 — three-step welcome card body. Numbered glyphs are circled-digit
# Unicode (U+2460–U+2462); the inline command at the bottom is accent-tinted.
_WELCOME_STEPS = (
    ("\u2460", "Pick providers \u2014 Claude, Codex, GitHub, OpenAI, Cursor, ..."),
    ("\u2461", "Add with one command"),
    ("\u2462", "Run `custats run` to start"),
)
_WELCOME_HEADER = "Welcome to custats"
_WELCOME_FOOTER = "[ Run `custats onboard` ]"


class PopupMenu:
    """Builds a per-account GTK menu. Caller attaches it to the tray.

    The class holds no GTK state — :meth:`build` is called every time the
    tray is clicked and produces a fresh ``Gtk.Menu`` containing one
    ``Gtk.MenuItem`` per account (each wrapping a ``Gtk.Box`` card built
    by :func:`custats.ui._components.build_account_card`) plus footer
    ``Gtk.MenuItem``s (Open Dashboard / Open data folder / Refresh now /
    Quit).

    The "Open data folder" item was deferred in design-tokens-v2 §10 and
    v3 §10; it ships here as an optional item that renders only when the
    caller passes ``on_open_data_folder`` (:class:`~custats.ui.tray.TrayIcon`
    always does — it opens ``state_dir()`` via ``xdg-open``).

    After :meth:`build` returns, ``self.account_rows`` is a ``list`` of
    :class:`_AccountCard` wrappers and ``self.welcome_card`` a bool so
    tests can assert against typed attributes without relying on GTK's
    ``get_children()`` (which returns empty iterators under MagicMock).
    """

    def __init__(
        self,
        *,
        statuses: dict,
        on_open_dashboard: Callable[[], None],
        on_refresh: Callable[[], None],
        on_quit: Callable[[], None],
        on_open_data_folder: Callable[[], None] | None = None,
        pace_enabled: bool = True,
    ) -> None:
        self._statuses = statuses
        self._on_open_dashboard = on_open_dashboard
        self._on_refresh = on_refresh
        self._on_quit = on_quit
        self._on_open_data_folder = on_open_data_folder
        self._pace_enabled = pace_enabled
        # Populated by :meth:`build`. Tests assert against this list.
        self.account_rows: list[_AccountCard] = []
        # v3 §9 — wrapper exposes ``welcome_card`` so tests can assert the
        # empty-state onboarding path without rendering GTK widgets.
        self.welcome_card: bool = False
        # v3 §9 — wrapper exposes ``keyboard_hint`` so tests can assert
        # the footer renders only when ``len(account_rows) >= 3``.
        self.keyboard_hint: bool = False

    def update(self, statuses: dict) -> None:
        """Replace the cached snapshot used by the next :meth:`build`."""
        self._statuses = statuses

    def build(self) -> "object":
        """Build and return a fresh ``Gtk.Menu``. Raises if GTK is missing."""
        try:
            import gi  # type: ignore[import-not-found]

            gi.require_version("Gtk", "3.0")
            from gi.repository import Gtk
        except (ImportError, ValueError) as exc:
            raise RuntimeError(
                f"GTK not available — cannot build popup menu: {exc}"
            )

        self.account_rows = []  # reset per-build tracker
        self.welcome_card = False
        self.keyboard_hint = False
        menu = Gtk.Menu()
        now = datetime.now(timezone.utc)

        if not self._statuses:
            welcome_item = self._build_welcome_card(Gtk)
            menu.append(welcome_item)
            self.welcome_card = True
        else:
            ordered = sorted(
                self._statuses.values(),
                key=lambda s: (list(Provider).index(s.provider), s.alias),
            )
            for status in ordered:
                item, card = build_account_card(
                    Gtk, status,
                    pace_enabled=self._pace_enabled,
                    now=now,
                    on_open_dashboard=self._on_open_dashboard,
                )
                menu.append(item)
                self.account_rows.append(card)

        if not self.welcome_card and len(self.account_rows) >= _KEYBOARD_HINT_MIN_ROWS:
            hint = self._build_keyboard_hint(Gtk)
            menu.append(hint)
            self.keyboard_hint = True

        menu.append(Gtk.SeparatorMenuItem.new())
        footer_items = [
            ("Open Dashboard", self._on_open_dashboard),
        ]
        # "Open data folder" — deferred in design-tokens-v2 §10 / v3 §10,
        # now shipped as an optional footer item. Hidden entirely when the
        # caller passes no handler (keeps PopupMenu usable standalone).
        if self._on_open_data_folder is not None:
            footer_items.append(("Open data folder", self._on_open_data_folder))
        footer_items.extend(
            [
                ("Refresh now", self._on_refresh),
                ("Quit", self._on_quit),
            ]
        )
        for label, callback in footer_items:
            item = Gtk.MenuItem.new_with_label(label)
            item.connect("activate", lambda _w, cb=callback: cb())
            menu.append(item)
        menu.show_all()
        return menu

    def _build_welcome_card(self, Gtk):
        """v3 §5 — friendly 3-step onboarding card.

        Replaces v2 §10's ``No accounts configured`` label with a numbered
        step list, accent-tinted ``Run \\`custats onboard\\``` prompt, and
        a clickable handler that opens the dashboard.
        """
        from ..core.models import Provider
        from ._glyphs import PROVIDER_ACCENT_HEX

        accent = PROVIDER_ACCENT_HEX.get(Provider.CLAUDE, "#D97757")

        # v3 §9 — wrapper-side attribute ``welcome_card`` is the single
        # source of truth for tests; the visible widget tree is best-effort
        # and may no-op under MagicMock.
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        try:
            body.set_border_width(8)
        except Exception:
            pass

        header = Gtk.Label()
        header.set_markup(f"<big><b>{esc(_WELCOME_HEADER)}</b></big>")
        header.set_xalign(0.0)
        body.pack_start(header, False, False, 0)

        for num, step_text in _WELCOME_STEPS:
            step_lbl = Gtk.Label()
            step_lbl.set_markup(f"{num} {esc(step_text)}")
            step_lbl.set_xalign(0.0)
            body.pack_start(step_lbl, False, False, 0)

        footer_lbl = Gtk.Label()
        footer_lbl.set_markup(
            f'<span foreground="{accent}">{esc(_WELCOME_FOOTER)}</span>'
        )
        footer_lbl.set_xalign(0.0)
        body.pack_start(footer_lbl, False, False, 0)

        menu_item = Gtk.MenuItem()
        try:
            menu_item.add(body)
        except Exception:
            pass
        # Click the welcome card to open the dashboard (v3 §5 mirrors v2's
        # "Open Dashboard" behaviour — the menu item itself stays clickable).
        # Bug fix: this previously connected to a ``_noop`` placeholder, so
        # the card rendered clickable but did nothing when activated.
        menu_item.connect(
            "activate", lambda _w: self._on_open_dashboard()
        )
        return menu_item

    @staticmethod
    def _build_keyboard_hint(Gtk):
        """v3 §8 — muted single-line keyboard hint footer."""
        from ._glyphs import STATUS_COLOURS_HEX_POPUP

        muted = STATUS_COLOURS_HEX_POPUP["UNKNOWN"]
        hint_lbl = Gtk.Label()
        hint_lbl.set_markup(
            f'<span foreground="{muted}"><tt>{esc(_KEYBOARD_HINT_TEXT)}</tt></span>'
        )
        hint_lbl.set_xalign(0.0)
        menu_item = Gtk.MenuItem()
        try:
            menu_item.add(hint_lbl)
        except Exception:
            pass
        menu_item.set_sensitive(False)  # informational only — see v3 §8
        return menu_item


# Re-export the badge lookup so callers (and tests) can audit the dot/label
# pair without reaching into ``_glyphs`` directly.
_STATUS_DOT_VIEW = _STATUS_DOT
_STATUS_LEGEND_VIEW = _STATUS_LEGEND_TEXT


__all__ = [
    "PopupMenu",
    "humanize_reset",
    "_STATUS_DOT_VIEW",
    "_STATUS_LEGEND_VIEW",
    "_KEYBOARD_HINT_TEXT",
    "_KEYBOARD_HINT_MIN_ROWS",
    "_WELCOME_HEADER",
    "_WELCOME_STEPS",
    "_WELCOME_FOOTER",
]