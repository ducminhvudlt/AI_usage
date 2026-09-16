"""System-tray icon via AppIndicator3. Degrades gracefully if GTK is missing.

Phase 9b (design-tokens-v3): the tray icon now encodes severity via a
distinct outline glyph (○/◐/△/■) plus colour. Single-account snapshots use
the shape system directly; multi-account snapshots fall back to v2's
provider-letter+colour logic so users can still see *which* account is
the worst at a glance (see docs/design-tokens-v3.md §1).
"""
from __future__ import annotations

from typing import Any, Callable

from ..core.models import Provider
from ._glyphs import (
    PROVIDER_GLYPH,
    STATUS_COLOURS_HEX_POPUP,
    _STATUS_SHAPE,
)

# Lazy GTK imports — must NOT happen at module import time so the rest of
# the package still works on boxes without GTK dev headers installed.
_GTK_OK = False
_GTK_PROBED = False


def _try_gtk() -> bool:
    """Best-effort import of GTK 3 + AppIndicator3. Caches the result."""
    global _GTK_OK, _GTK_PROBED
    if _GTK_PROBED:
        return _GTK_OK
    _GTK_PROBED = True
    try:
        import gi  # type: ignore[import-not-found]
        gi.require_version("Gtk", "3.0")
        gi.require_version("AppIndicator3", "0.1")
        from gi.repository import Gtk, AppIndicator3, GdkPixbuf  # noqa: F401
        from gi.repository import Pango, PangoCairo  # noqa: F401
        import cairo  # noqa: F401
        _GTK_OK = True
    except (ImportError, ValueError):
        _GTK_OK = False
    return _GTK_OK


class TrayUnavailable(RuntimeError):
    """Raised when GTK / AppIndicator3 isn't available on this system."""


# Status colours for the TRAY surface (docs/design-tokens-v3.md §1). The
# GOOD / CAUTION foregrounds are slightly darker than v2 §1 so the tray
# glyph separates from sibling icons at 16px. Popup row pills keep the
# lighter v2 foregrounds (imported as ``STATUS_COLOURS_HEX_POPUP``) for AA
# contrast on card surfaces.
_STATUS_COLOURS_HEX = {
    "GOOD":     "#3a9d5d",
    "CAUTION":  "#d4a72c",
    "CRITICAL": "#FF7A6B",
    "AT_LIMIT": "#FF4D4D",
    "UNKNOWN":  "#8A8F98",
}

# Back-compat alias — older tests and code import the popup-palette dict
# from this module. v3 makes the dict tray-specific, but we keep the name
# so existing callers (and the test in ``test_colour_table_*``) don't
# regress on import paths.
_POPUP_STATUS_COLOURS_HEX = STATUS_COLOURS_HEX_POPUP

# Re-export the canonical single-letter glyph map by value (str key).
_PROVIDER_GLYPH = {p.value: glyph for p, glyph in PROVIDER_GLYPH.items()}

# Idle / "all clear" glyph is the UNKNOWN outline (v3 §1 replaces v2's `…`).
_IDLE_GLYPH = _STATUS_SHAPE["UNKNOWN"]

# Higher = worse; used for picking the dominant account.
_SEVERITY_ORDER = {"UNKNOWN": 0, "GOOD": 0, "CAUTION": 1, "CRITICAL": 2, "AT_LIMIT": 3}


def _worst_status(statuses) -> str:
    """Worst UsageStatus name across all accounts and both windows, or 'UNKNOWN'."""
    worst_name, worst_sev = "UNKNOWN", -1
    for status in statuses.values():
        for s in (status.five_hour_status, status.seven_day_status):
            sev = _SEVERITY_ORDER.get(s.name, 0)
            if sev > worst_sev:
                worst_sev, worst_name = sev, s.name
    return worst_name


def _account_severity(status) -> int:
    """Severity of one account = max of its two windows."""
    return max(
        _SEVERITY_ORDER.get(status.five_hour_status.name, 0),
        _SEVERITY_ORDER.get(status.seven_day_status.name, 0),
    )


def _worst_account(statuses: dict):
    """Pick the worst-scoring account, breaking ties deterministically.

    Tiebreak: provider enum declaration order (CLAUDE < CHATGPT < CODEX <
    ... < CURSOR) then alias. Without this, ``max()`` falls back to dict
    insertion order, which can flicker between equal-severity accounts
    as their callbacks race on different polls. (R8.)
    """
    return max(
        statuses.values(),
        key=lambda s: (_account_severity(s), list(Provider).index(s.provider), s.alias),
    )


def _use_shape(statuses: dict) -> bool:
    """True when the tray should render the v3 shape system.

    v3 §1: single-account (or empty) snapshots use the shape+colour
    system so severity reads at a glance. Multi-account snapshots fall
    back to v2's provider-letter+colour logic so the worst account is
    still identifiable, not just its severity.
    """
    return len(statuses) <= 1


class TrayIcon:
    """Wraps AppIndicator3. Renders either a shape glyph (single account /
    idle) or the worst-account's provider letter (multi-account), coloured
    by severity. The right-click menu is built fresh on every
    :meth:`update` via :class:`~custats.ui.popup.PopupMenu`."""

    INDICATOR_ID = "custats-tray"
    INITIAL_ICON = "custats-idle"

    def __init__(
        self,
        *,
        on_open_dashboard: Callable[[], Any],
        on_refresh: Callable[[], Any],
        on_quit: Callable[[], Any],
        statuses: dict | None = None,
    ) -> None:
        if not _try_gtk():
            raise TrayUnavailable(
                "GTK / AppIndicator3 not available. Install "
                "gir1.2-gtk-3.0 and gir1.2-appindicator3-0.1 on Ubuntu 22.04."
            )
        import gi  # type: ignore[import-not-found]
        gi.require_version("Gtk", "3.0")
        gi.require_version("AppIndicator3", "0.1")
        from gi.repository import AppIndicator3  # noqa: F401

        self._on_open_dashboard = on_open_dashboard
        self._on_refresh = on_refresh
        self._on_quit = on_quit
        self._statuses: dict = statuses or {}
        self._indicator = AppIndicator3.Indicator.new(
            self.INDICATOR_ID,
            self.INITIAL_ICON,
            AppIndicator3.IndicatorCategory.APPLICATION_STATUS,
        )
        self._indicator.set_status(AppIndicator3.IndicatorStatus.ACTIVE)
        # Per docs/design-tokens-v3.md §9: tests assert against
        # ``_last_shape`` (shape glyph rendered) and ``_last_glyph_key``
        # (icon-name string). Both populated by :meth:`update`.
        self._last_glyph_key: str | None = None
        self._last_shape: str | None = None
        self.update(self._statuses)  # prime icon + menu so no placeholder shows.

    def update(self, statuses: dict) -> None:
        """Re-render the tray glyph + popup from the latest status snapshot."""
        self._statuses = statuses
        if not _GTK_OK:
            return
        from gi.repository import AppIndicator3  # type: ignore[import-not-found]
        from .popup import PopupMenu  # local import to avoid cycles

        worst = _worst_status(statuses)
        use_shape = _use_shape(statuses)
        if statuses:
            worst_provider = _worst_account(statuses).provider
            letter = PROVIDER_GLYPH.get(worst_provider, "?")
        else:
            worst_provider = None
            letter = "?"
        if use_shape:
            glyph = _STATUS_SHAPE.get(worst, _STATUS_SHAPE["UNKNOWN"])
            shape = glyph
        else:
            # Multi-account path; ``statuses`` is non-empty so
            # ``worst_provider`` is always a Provider here.
            assert worst_provider is not None
            glyph = PROVIDER_GLYPH.get(worst_provider, "?")
            shape = None
        colour = _STATUS_COLOURS_HEX.get(worst, _STATUS_COLOURS_HEX["UNKNOWN"])
        key = f"custats-{glyph}-{colour}"
        shape_text = shape if shape is not None else letter
        label = f"{shape_text} {letter} {worst.lower()}"
        if key != self._last_glyph_key:
            self._indicator.set_icon_full(key, label)
            self._last_glyph_key = key
            self._last_shape = shape
            self._last_label = label
        elif label != getattr(self, "_last_label", None):
            # Refresh label even when the icon name hasn't changed — e.g.
            # the user reopened the menu at a different severity but the
            # same shape+colour combination (rare, but possible when only
            # the status name differs). Keeps ``accessible-label`` honest.
            self._indicator.set_icon_full(key, label)
            self._last_label = label

        menu = PopupMenu(
            statuses=statuses,
            on_open_dashboard=self._on_open_dashboard,
            on_refresh=self._on_refresh,
            on_quit=self._on_quit,
        ).build()
        self._indicator.set_menu(menu)

    def shutdown(self) -> None:
        """Tear down the indicator. Idempotent and best-effort."""
        indicator = getattr(self, "_indicator", None)
        if indicator is None:
            return
        try:
            from gi.repository import AppIndicator3  # type: ignore[import-not-found]
            indicator.set_status(AppIndicator3.IndicatorStatus.PASSIVE)
        except Exception:
            pass


__all__ = [
    "TrayIcon",
    "TrayUnavailable",
    "_PROVIDER_GLYPH",
    "_STATUS_COLOURS_HEX",
    "_POPUP_STATUS_COLOURS_HEX",
    "_STATUS_SHAPE",
    "_worst_status",
    "_worst_account",
    "_use_shape",
]