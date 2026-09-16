"""Opt-in real-GTK smoke tests — closes the "no visual GTK verification" gap.

These tests run **only** when:
  1. ``CUSTATS_GTK_TESTS=1`` in the environment, AND
  2. ``gi.require_version("Gtk", "3.0")`` actually succeeds (real GTK 3
     introspection typelibs present, not the mocks from ``conftest.py``).

The default unit suite stays mock-only (the dev box lacks
``girepository-2.0``), matching the documented limitation in README and
design-tokens-v3 §10. On a GTK-capable box run:

    CUSTATS_GTK_TESTS=1 pytest tests/ui/test_gtk_integration.py -v

CI can add ``gir1.2-gtk-3.0 gir1.2-appindicator3-0.1`` and flip the env
var to get coverage of what the mocks approximate.
"""
from __future__ import annotations

import os

import pytest

from custats.core.config import AppConfig
from custats.ui.main_window import MainWindow
from custats.ui.tray import TrayIcon, TrayUnavailable

_REASON = (
    "set CUSTATS_GTK_TESTS=1 and install gir1.2-gtk-3.0 + "
    "gir1.2-appindicator3-0.1 (no girepository typelibs on this box)"
)


def _real_gtk_available() -> bool:
    """True only when the env gate is on AND real Gtk 3 typelibs import."""
    if os.environ.get("CUSTATS_GTK_TESTS") != "1":
        return False
    try:
        import gi  # type: ignore[import-not-found]

        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk  # noqa: F401

        return True
    except (ImportError, ValueError):
        return False


_GTK_AVAILABLE = _real_gtk_available()

pytestmark = pytest.mark.skipif(not _GTK_AVAILABLE, reason=_REASON)


def _build_status(
    *,
    provider: str = "claude",
    alias: str = "work",
    account_id: str = "a1",
    five_hour_status: str = "GOOD",
    seven_day_status: str = "GOOD",
    five_hour_percent: float | None = 10.0,
    seven_day_percent: float | None = 20.0,
):
    from custats.core.models import Provider, UsageStatus
    from custats.state import LiveStatus

    return LiveStatus(
        account_id=account_id,
        provider=Provider.parse(provider),
        alias=alias,
        five_hour_status=UsageStatus[five_hour_status.upper()],
        seven_day_status=UsageStatus[seven_day_status.upper()],
        five_hour_percent=five_hour_percent,
        seven_day_percent=seven_day_percent,
        five_hour_resets_at=None,
        seven_day_resets_at=None,
        pace_projection_percent=None,
        pace_label="ok",
        fetched_at=None,
    )


def test_real_gtk_main_window_builds() -> None:
    """MainWindow builds a real four-tab Gtk.Notebook end to end."""
    win = MainWindow(db=None, config=AppConfig(), poller=None)
    try:
        assert win._window is not None
        assert win._live_inner is not None
        assert win._accounts_list is not None
        assert "theme" in win._settings_widgets
        assert "status_legend" in win._settings_widgets
        assert "shape_legend" in win._settings_widgets
    finally:
        win.destroy()


def test_real_gtk_tray_icon_constructs() -> None:
    """TrayIcon constructs against real AppIndicator3. On headless boxes
    without a StatusNotifierWatcher the indicator may fail at runtime but
    construction itself must succeed (or skip cleanly)."""
    try:
        tray = TrayIcon(
            on_open_dashboard=lambda: None,
            on_refresh=lambda: None,
            on_quit=lambda: None,
            statuses={},
        )
    except TrayUnavailable as exc:
        pytest.skip(f"AppIndicator3 unusable on this box: {exc}")
    try:
        # Idle snapshot → UNKNOWN outline shape + muted colour.
        assert tray._last_shape is not None
    finally:
        tray.shutdown()


def test_real_gtk_tray_shape_transition() -> None:
    """A snapshot transition updates ``_last_shape`` with the v3 shape
    glyph — the visual state the tray actually renders."""
    tray = TrayIcon(
        on_open_dashboard=lambda: None,
        on_refresh=lambda: None,
        on_quit=lambda: None,
        statuses={},
    )
    try:
        idle_shape = tray._last_shape
        tray.update(
            {"a1": _build_status(provider="codex", five_hour_status="AT_LIMIT")}
        )
        limit_shape = tray._last_shape
        assert idle_shape != limit_shape
        assert limit_shape == "\u25a0"  # filled square per design-tokens-v3 §1
    finally:
        tray.shutdown()


def test_real_gtk_popup_menu_builds_with_rows() -> None:
    """PopupMenu.build() produces a real Gtk.Menu with one MenuItem per
    account card + the footer items, using the same code path as the tray."""
    from custats.ui.popup import PopupMenu

    opened: list[str] = []
    menu = PopupMenu(
        statuses={
            "a1": _build_status(provider="claude", alias="work"),
            "a2": _build_status(provider="codex", alias="side"),
        },
        on_open_dashboard=lambda: opened.append("dashboard"),
        on_open_data_folder=lambda: opened.append("data"),
        on_refresh=lambda: None,
        on_quit=lambda: None,
    ).build()
    assert menu is not None
    assert len(menu.get_children()) == 2 + 1 + 4  # cards + separator + footer


def test_real_gtk_welcome_card_click_opens_dashboard() -> None:
    """v3 §5 — activating the welcome card fires on_open_dashboard."""
    from custats.ui.popup import PopupMenu

    opened: list[str] = []
    popup = PopupMenu(
        statuses={},
        on_open_dashboard=lambda: opened.append("dashboard"),
        on_open_data_folder=lambda: None,
        on_refresh=lambda: None,
        on_quit=lambda: None,
    )
    menu = popup.build()
    assert popup.welcome_card is True
    # First child is the welcome card's MenuItem; activate it like a click.
    first = menu.get_children()[0]
    first.activate()
    assert opened == ["dashboard"]
