"""Card-construction helpers for the popup menu.

These helpers are kept module-private to ``custats.ui`` and are imported
by :mod:`custats.ui.popup` to keep that module under its line budget.

Test-mock contract (docs/design-tokens-v3.md §9): we never rely on
``get_children()`` to find a widget — GTK mocks return empty iterators.
The :class:`_AccountCard` wrapper holds typed attributes
(``accent_hex``, ``fraction_5h``, ``glyph_2``, ``badge_glyph``,
``bar_text_5h``, ``bar_text_7d``, ``pace_text`` …) so tests can assert
directly without walking the widget tree.
"""
from __future__ import annotations

import time
from datetime import datetime
from typing import Callable

from ..core.models import Provider
from ._glyphs import (
    PROVIDER_ACCENT_HEX,
    PROVIDER_GLYPH_2,
    STATUS_COLOURS_HEX_POPUP,
    _STATUS_DOT,
)

# Constants per docs/design-tokens-v3.md §3-4. The bar width is fixed so the
# percent column digits line up across rows regardless of percent value.
_BAR_CELLS = 14
_SPARKLINE_BUCKETS = 10

# v3 §10 (formerly deferred) — animated sparklines. Two phase frames at
# ~2 fps: every build() re-renders the pace line one Unicode block-step
# further along the bucket ramp, which reads as a gentle "live data"
# shimmer. Pure text — no Cairo, no timers, exactly as the spec demands.
_SPARK_ANIMATION_FRAMES = 2


def get_tick() -> int:
    """Monotonic animation tick (two frames per wall-clock second)."""
    return int(time.monotonic() * 2)


def recent_pace_points(usage_rows, *, n: int = _SPARKLINE_BUCKETS) -> list[float]:
    """Reduce persisted ``Usage`` rows to the last ``n`` pace percentages.

    The sparkline's real data feed (v3 §10): callers pass the rows from
    :meth:`custats.storage.db.Database.usage_history` — oldest → newest —
    and get back the trailing ``n`` non-``None`` seven-day percentages,
    oldest → newest, ready for :func:`_sparkline`. The seven-day window
    is the pace signal (the 5-hour window resets too fast to trend).

    Rows with no seven-day value (e.g. Cursor) are skipped rather than
    zero-filled so a sparse history renders only what was actually
    measured.
    """
    points: list[float] = []
    for row in usage_rows:
        seven = getattr(row, "seven_day", None)
        pct = getattr(seven, "seven_day_percent", None) if seven is not None else None
        if pct is None:
            continue
        points.append(float(pct))
    return points[-n:]

# Unicode blocks for the bar (filled / empty). U+2588 and U+2591 survive
# font fallback (Cantarell / Adwaita Sans / Noto Sans) per design-tokens-v3.md §4.
_BAR_FILLED = "\u2588"
_BAR_EMPTY = "\u2591"

# Sparkline block ramp; 10 buckets (v3 §4 widened from v2's 8).
_SPARKLINE_BLOCKS = "\u2581\u2582\u2583\u2584\u2585\u2586\u2587\u2588"


def _sparkline(points: list[float], *, anim: int = 0) -> str:
    """Bucket ``points`` into ``_SPARKLINE_BUCKETS`` Unicode blocks; "" if no points.

    v3 §4 widened the bucket count from 8 to 10 for finer detail in the
    inline sparkline row. v3 §10 adds ``anim``: the animation phase
    (0/1) shifts every bucket one step up the block ramp so consecutive
    frames shimmer. Values at the top of the range clamp — the shape
    never lies, it only breathes.
    """
    if not points:
        return ""
    anim = anim % _SPARK_ANIMATION_FRAMES
    # The display width (``_SPARKLINE_BUCKETS`` cells, v3 §4) is separate
    # from the ramp granularity: Unicode block elements only come in 8
    # distinct steps (▁▂▃▄▅▆▇█), so bucket indices quantise to the ramp
    # length. Bucketing by the cell count instead would index past the
    # end of the ramp on any non-empty history (latent crash — the stub
    # hid it until real history/animation landed).
    ramp = len(_SPARKLINE_BLOCKS)
    out: list[str] = []
    for v in points:
        clamped = max(0.0, min(100.0, float(v)))
        idx = min(ramp - 1, int(clamped / 100.0 * ramp) + anim)
        out.append(_SPARKLINE_BLOCKS[idx])
    return "".join(out)


def _escape(text: str) -> str:
    """Escape a string for Pango markup."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _pace_history_stub(status) -> list[float]:
    """Return recent usage % for the sparkline (stub).

    Phase 8b keeps this stub-friendly: real callers wire
    ``db.recent_usage_points(account_id, n=30)`` later. Returning ``[]``
    means the sparkline row still renders its prefix/suffix but the block
    sequence is empty.
    """
    return []


def _fraction_of(pct):
    """Clamp a percentage (or None) into [0, 1] for ``ProgressBar.fraction``."""
    return None if pct is None else max(0.0, min(100.0, pct)) / 100.0


def _pill_status(status) -> str:
    """Pick the worst of the account's two statuses for the badge label."""
    if status.seven_day_status.severity > status.five_hour_status.severity:
        return status.seven_day_status.name
    return status.five_hour_status.name


def _render_bar(pct: float) -> str:
    """Render the v3 §3 inline bar — fixed-width ``_BAR_CELLS`` block string."""
    filled = int(round(max(0.0, min(100.0, pct)) / 100.0 * _BAR_CELLS))
    return _BAR_FILLED * filled + _BAR_EMPTY * (_BAR_CELLS - filled)


def _render_bar_line(
    label: str,
    pct: float | None,
    resets_at: datetime | None,
    now: datetime,
) -> str:
    """Compose the v3 §3 one-line bar+percent+reset text.

    Layout: ``{label}  {bar}  {pct:>4}%  ↻ {reset}``. When ``pct`` is
    None the whole line collapses to ``""`` (caller skips the row).
    """
    from .popup import humanize_reset  # local import to avoid cycles
    if pct is None:
        return ""
    bar = _render_bar(pct)
    reset = humanize_reset(resets_at, now=now)
    reset_text = f" ↻ {reset}" if reset else " ↻"
    return f"{label}  {bar}  {int(pct):>4}%{reset_text}"


def _render_pace_line(pct: float, spark: str, label: str, *, anim: int = 0) -> str:
    """Compose the v3 §4 inline pace text.

    Layout: ``Pace  {spark:<10}  {pct:>4}% {label}``. Returns ``""`` when
    the caller doesn't have a sparkline to show. v3 §10: with no real
    history the fallback row still animates (``▁`` frame 0, ``▂`` frame
    1) so the pace line visibly breathes even before a data feed lands.
    """
    anim = anim % _SPARK_ANIMATION_FRAMES
    spark_text = spark or (_SPARKLINE_BLOCKS[anim] * _SPARKLINE_BUCKETS)
    return f"Pace  {spark_text:<{_SPARKLINE_BUCKETS}}  {int(pct):>4}% {_escape(label)}"


def build_account_card(
    Gtk,
    status,
    *,
    pace_enabled: bool,
    now: datetime,
    on_open_dashboard: Callable[[], None],
    tick: int | None = None,
    pace_history: list[float] | None = None,
) -> tuple:
    """Build a per-account card and return ``(menu_item, _AccountCard)``.

    The card is a vertical ``Gtk.Box`` containing a header line (glyph +
    alias + provider + status badge), an inline 5h bar line, an inline 7d
    bar line, and an optional inline pace line. It is wrapped in a
    ``Gtk.MenuItem`` so AppIndicator3 still sees a real ``Gtk.Menu`` of
    ``GtkMenuItem``s.

    v3 §10: ``tick`` is the animation phase source (defaults to
    :func:`get_tick`); ``pace_history`` overrides the history stub. The
    pace sparkline shifts one Unicode block-step per frame so
    consecutive builds shimmer without Cairo or timers.

    Returned :class:`_AccountCard` holds typed attributes for testing.
    """
    anim = (tick if tick is not None else get_tick()) % _SPARK_ANIMATION_FRAMES
    provider = status.provider
    accent = PROVIDER_ACCENT_HEX.get(provider, "#888888")
    glyph_2 = PROVIDER_GLYPH_2.get(provider, "??")

    fraction_5h = _fraction_of(status.five_hour_percent)
    fraction_7d = _fraction_of(status.seven_day_percent)
    pace_fraction = (
        _fraction_of(status.pace_projection_percent)
        if (pace_enabled and status.pace_projection_percent is not None)
        else None
    )

    card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    try:
        card.set_border_width(8)
        card.set_margin_start(3)  # 3px provider-accent at left.
    except Exception:
        pass

    # --- Header line -----------------------------------------------------
    header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    glyph_lbl = Gtk.Label()
    glyph_lbl.set_markup(f"<b>{glyph_2}</b>")
    glyph_lbl.set_xalign(0.0)
    try:
        glyph_lbl.set_size_request(28, -1)
    except Exception:
        pass
    header.pack_start(glyph_lbl, False, False, 0)

    alias_lbl = Gtk.Label()
    alias_lbl.set_markup(f"<b>{_escape(status.alias)}</b>  · {provider.value}")
    alias_lbl.set_xalign(0.0)
    header.pack_start(alias_lbl, True, True, 0)

    badge_status = _pill_status(status)
    badge_glyph, badge_label = _STATUS_DOT.get(badge_status, ("○", "—"))
    badge_colour = STATUS_COLOURS_HEX_POPUP.get(badge_status, "#8A8F98")
    badge = Gtk.Label()
    badge.set_markup(
        f'<span foreground="{badge_colour}">{badge_glyph}</span> {badge_label}'
    )
    try:
        badge.set_size_request(56, -1)
    except Exception:
        pass
    header.pack_start(badge, False, False, 0)
    card.pack_start(header, False, False, 0)

    # --- Bars / error / sparkline rows ----------------------------------
    bar_text_5h = ""
    bar_text_7d = ""
    pace_text = ""
    if status.error:
        err_lbl = Gtk.Label()
        err_lbl.set_markup(f'<span foreground="#FF7A6B">! {_escape(status.error)}</span>')
        err_lbl.set_xalign(0.0)
        card.pack_start(err_lbl, False, False, 0)
    else:
        if status.five_hour_percent is not None:
            bar_text_5h = _render_bar_line(
                "5h", status.five_hour_percent,
                status.five_hour_resets_at, now,
            )
            line_5h = Gtk.Label()
            # accessible-label uses prose so screen readers don't speak the
            # ↻ glyph verbatim (v3 §3 a11y note).
            try:
                line_5h.set_tooltip_text(
                    f"5h used {status.five_hour_percent:.0f}%"
                    + (f", resets in {bar_text_5h.split('↻ ', 1)[-1]}"
                       if '↻ ' in bar_text_5h else "")
                )
            except Exception:
                pass
            line_5h.set_markup(f"<tt>{_escape(bar_text_5h)}</tt>")
            line_5h.set_xalign(0.0)
            card.pack_start(line_5h, False, False, 0)
        if status.seven_day_percent is not None:
            bar_text_7d = _render_bar_line(
                "7d", status.seven_day_percent,
                status.seven_day_resets_at, now,
            )
            line_7d = Gtk.Label()
            line_7d.set_markup(f"<tt>{_escape(bar_text_7d)}</tt>")
            line_7d.set_xalign(0.0)
            card.pack_start(line_7d, False, False, 0)
        if pace_fraction is not None and status.pace_label:
            history = (
                pace_history if pace_history is not None
                else _pace_history_stub(status)
            )
            spark = _sparkline(history, anim=anim)
            pace_text = _render_pace_line(
                pace_fraction * 100, spark, status.pace_label, anim=anim
            )
            pace_lbl = Gtk.Label()
            pace_lbl.set_markup(f"<tt>{pace_text}</tt>")
            pace_lbl.set_xalign(0.0)
            card.pack_start(pace_lbl, False, False, 0)

    # --- Wrap in Gtk.MenuItem so AppIndicator3 sees a real menu item. ----
    menu_item = Gtk.MenuItem()
    try:
        menu_item.add(card)
    except Exception:
        pass
    menu_item.connect("activate", lambda _w: on_open_dashboard())

    wrapper = _AccountCard(
        provider=provider,
        alias=status.alias,
        status=status,
        fraction_5h=fraction_5h,
        fraction_7d=fraction_7d,
        pace_fraction=pace_fraction,
        pace_label=status.pace_label,
        badge_glyph=badge_glyph,
        badge_label=badge_label,
        bar_text_5h=bar_text_5h,
        bar_text_7d=bar_text_7d,
        pace_text=pace_text,
        pace_anim=anim,
        widget=card,
    )
    return menu_item, wrapper


class _AccountCard:
    """Thin wrapper around a per-account card. Holds data + widget handle.

    Per docs/design-tokens-v2.md §13 / design-tokens-v3.md §9:
    ``get_children()`` is unreliable under MagicMock, so we record the
    widget handle explicitly on this wrapper. Tests assert against typed
    attributes — including the v3 bar/pace text strings.
    """

    __slots__ = (
        "provider", "alias", "accent_hex", "glyph_2", "fraction_5h",
        "fraction_7d", "pace_fraction", "pace_label", "widget", "status",
        "badge_glyph", "badge_label", "bar_text_5h", "bar_text_7d",
        "pace_text", "pace_anim",
    )

    def __init__(
        self,
        *,
        provider: Provider,
        alias: str,
        status,
        fraction_5h: float | None,
        fraction_7d: float | None,
        pace_fraction: float | None,
        pace_label: str | None,
        badge_glyph: str,
        badge_label: str,
        bar_text_5h: str,
        bar_text_7d: str,
        pace_text: str,
        pace_anim: int = 0,
        widget,
    ) -> None:
        self.provider = provider
        self.alias = alias
        self.status = status
        self.accent_hex = PROVIDER_ACCENT_HEX.get(provider, "#888888")
        self.glyph_2 = PROVIDER_GLYPH_2.get(provider, "??")
        self.fraction_5h = fraction_5h
        self.fraction_7d = fraction_7d
        self.pace_fraction = pace_fraction
        self.pace_label = pace_label
        self.badge_glyph = badge_glyph
        self.badge_label = badge_label
        self.bar_text_5h = bar_text_5h
        self.bar_text_7d = bar_text_7d
        self.pace_text = pace_text
        self.pace_anim = pace_anim
        self.widget = widget


__all__ = [
    "_AccountCard",
    "build_account_card",
    "get_tick",
    "recent_pace_points",
    "_SPARK_ANIMATION_FRAMES",
]