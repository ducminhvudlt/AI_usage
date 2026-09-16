"""Phase 5 main settings/dashboard window for custats.

Four-tab ``Gtk.Notebook`` (Live / Accounts / Settings / About) driven by
the poller snapshot stream. GTK is imported lazily so the package still
imports on boxes without ``gir1.2-gtk-3.0``. When GTK is missing
``__init__`` raises :class:`TrayUnavailable`.

Phase 8b: Live tab frames wear per-provider accent (3px left border)
and use the two-letter glyph from ``custats.ui._glyphs``. Settings tab
gained a ``theme`` ComboBoxText bound to ``AppConfig.theme``.
"""
from __future__ import annotations

from argparse import Namespace
from datetime import datetime, timezone
from typing import Callable

from .. import __version__
from ..core import config as _config_mod  # module ref so save_config is patchable
from ..core.config import AppConfig, load_config
from ..core.models import Provider
from ._glyphs import (
    PROVIDER_ACCENT_HEX,
    PROVIDER_GLYPH_2,
    STATUS_COLOURS_HEX_POPUP,
    _SHAPE_LEGEND_TEXT,
    _STATUS_DOT,
    _STATUS_LEGEND_TEXT,
    accent_hex_for,
)
from ._helpers import esc, hbox, labelled, safe_call, spin, vbox
from .popup import humanize_reset
from .tray import TrayUnavailable

_GTK_OK = False
_GTK_PROBED = False

# Theme tokens from docs/design-tokens-v2.md §8. Surfaces flip between
# dark and light; status colours are unchanged. Loaded into a CSS provider
# on demand by :meth:`MainWindow._apply_theme`.
_THEME_DARK = {
    "surface_bg":      "#1e1e1e",
    "surface_card":    "#262626",
    "surface_border":  "#2f2f2f",
    "surface_divider": "#3a3a3a",
    "text_primary":    "#fafafa",
    "text_dim":        "#8A8F98",
    "bar_empty":       "#3a3a3a",
}
_THEME_LIGHT = {
    "surface_bg":      "#fafafa",
    "surface_card":    "#ffffff",
    "surface_border":  "#e0e0e0",
    "surface_divider": "#d8d8d8",
    "text_primary":    "#1e1e1e",
    "text_dim":        "#6b6f76",
    "bar_empty":       "#d8d8d8",
}


def _try_gtk() -> bool:
    global _GTK_OK, _GTK_PROBED
    if _GTK_PROBED: return _GTK_OK
    _GTK_PROBED = True
    try:
        import gi  # type: ignore[import-not-found]
        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk  # noqa: F401
        _GTK_OK = True
    except (ImportError, ValueError): _GTK_OK = False
    return _GTK_OK


def _resolve_theme_tokens(config: AppConfig) -> dict:
    """Pick the surface token set from ``config.theme``.

    "auto" follows ``gtk-application-prefer-dark-theme``; "light"/"dark"
    force the surface. Status colours stay put per design-tokens-v2.md §1.
    """
    name = (config.theme or "auto").lower()
    if name == "dark":
        return _THEME_DARK
    if name == "light":
        return _THEME_LIGHT
    # auto — defer to GTK's setting when readable; default dark otherwise.
    try:
        from gi.repository import Gtk  # type: ignore
        settings = Gtk.Settings.get_default()
        if settings is not None and not settings.get_property("gtk-application-prefer-dark-theme"):
            return _THEME_LIGHT
    except Exception:
        pass
    return _THEME_DARK


def _open_add_dialog(window_self) -> None:
    try:
        from .add_account_dialog import AddAccountDialog
        AddAccountDialog(parent=window_self._window,
                         on_submit=window_self._on_add_submit).show()
    except Exception: pass


def _build_legend_markup() -> str:
    """v3 §6 — Pango markup for the four-colour status legend.

    Each dot is tinted with its popup-status foreground from
    :data:`STATUS_COLOURS_HEX_POPUP` so the legend matches the row dots
    shown in the popup (v3 §2). The line ends with the ``UNKNOWN`` muted
    outline + em-dash, mirroring the row badge.
    """
    rows = []
    for status, (dot, label) in (
        ("GOOD",     _STATUS_DOT["GOOD"]),
        ("CAUTION",  _STATUS_DOT["CAUTION"]),
        ("CRITICAL", _STATUS_DOT["CRITICAL"]),
        ("AT_LIMIT", _STATUS_DOT["AT_LIMIT"]),
    ):
        colour = STATUS_COLOURS_HEX_POPUP[status]
        rows.append(
            f'<span foreground="{colour}">{dot}</span> {label}'
        )
    muted_dot, muted_label = _STATUS_DOT["UNKNOWN"]
    muted_colour = STATUS_COLOURS_HEX_POPUP["UNKNOWN"]
    rows.append(f'<span foreground="{muted_colour}">{muted_dot}</span> {muted_label}')
    return "Status legend: " + "   ".join(rows)


class MainWindow:
    WINDOW_TITLE = "custats"
    DEFAULT_WIDTH = 720
    DEFAULT_HEIGHT = 520
    def __init__(self, *, db, config: AppConfig, poller) -> None:
        if not _try_gtk():
            raise TrayUnavailable("GTK / AppIndicator3 not available.")
        import gi  # type: ignore[import-not-found]
        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk
        self._db, self._config, self._poller = db, config, poller
        self._window = self._live_inner = self._accounts_list = None
        self._save_button = self._revert_button = self._add_account_button = None
        self._about_version_label = None
        self._live_children: list = []
        self._accounts_children: list = []
        self._settings_widgets: dict = {}
        # v3 §10 — per-account notification toggles, keyed by account id
        # and rebuilt by :meth:`_refresh_accounts_list`.
        self._notify_buttons: dict = {}
        self._unsubscribe: Callable[[], None] | None = None
        win = Gtk.Window()
        win.set_title(self.WINDOW_TITLE)
        win.set_default_size(self.DEFAULT_WIDTH, self.DEFAULT_HEIGHT)
        win.connect("destroy", lambda *_: self.destroy())
        nb = Gtk.Notebook(); win.add(nb)

        # Live tab
        scroller = Gtk.ScrolledWindow()
        self._live_inner = vbox(Gtk, spacing=8, border=8)
        scroller.add(self._live_inner)
        nb.append_page(scroller, Gtk.Label(label="Live"))
        # Accounts tab
        self._accounts_list = vbox(Gtk, spacing=4)
        acc_outer = vbox(Gtk, spacing=8, border=8)
        acc_outer.pack_start(self._accounts_list, True, True, 0)
        self._add_account_button = Gtk.Button.new_with_label("+ Add account")
        self._add_account_button.connect("clicked", lambda *_: _open_add_dialog(self))
        acc_outer.pack_start(self._add_account_button, False, False, 0)
        nb.append_page(acc_outer, Gtk.Label(label="Accounts"))
        self._build_settings_tab(Gtk, nb)
        # About tab
        about = vbox(Gtk, spacing=8, border=16)
        t = Gtk.Label(); t.set_markup("<big><b>custats</b></big>")
        about.pack_start(t, False, False, 0)
        self._about_version_label = Gtk.Label(label=f"Version {__version__}")
        about.pack_start(self._about_version_label, False, False, 0)
        d = Gtk.Label(label="Linux menu bar tracker for Claude, Codex, Grok, and Cursor usage limits.")
        d.set_xalign(0.0); about.pack_start(d, False, False, 0)
        u = Gtk.Label(); u.set_markup('<a href="https://custats.info/">https://custats.info/</a>')
        about.pack_start(u, False, False, 0)
        nb.append_page(about, Gtk.Label(label="About"))

        self._window = win
        self._refresh_accounts_list()
        self._unsubscribe = poller.subscribe(self.set_statuses)
        # Apply theme tokens (no-op if GTK is mocked — keeps tests happy).
        self._apply_theme()
    def show(self) -> None:
        if self._window is not None: safe_call(self._window, "show_all")
    def hide(self) -> None:
        if self._window is not None: safe_call(self._window, "hide")
    def destroy(self) -> None:
        unsub = self._unsubscribe; self._unsubscribe = None
        if unsub is not None:
            try: unsub()
            except Exception: pass
        try:
            from gi.repository import Gtk  # type: ignore[import-not-found]
            Gtk.main_quit()
        except Exception: pass
        if self._window is not None:
            safe_call(self._window, "destroy"); self._window = None
    def set_statuses(self, statuses) -> None:
        if not _GTK_OK or self._live_inner is None: return
        try:
            from gi.repository import Gtk  # type: ignore[import-not-found]
        except (ImportError, ValueError): return
        inner = self._live_inner
        for c in list(self._live_children):
            try: inner.remove(c)
            except Exception: pass
        self._live_children = []
        if not statuses:
            e = Gtk.Label(label="No accounts yet — add one in the Accounts tab.")
            e.set_xalign(0.0); inner.pack_start(e, False, False, 0)
            self._live_children.append(e)
        else:
            now = datetime.now(timezone.utc)
            for st in sorted(statuses.values(),
                             key=lambda s: (list(Provider).index(s.provider), s.alias)):
                f = self._live_frame(Gtk, st, now)
                inner.pack_start(f, False, False, 0)
                self._live_children.append(f)
        safe_call(inner, "show_all")
    def _live_frame(self, Gtk, status, now: datetime):
        frame = Gtk.Frame(); outer = vbox(Gtk, spacing=4, border=8)
        # 3px provider-accent: left margin fakes the bar without CSS.
        try:
            outer.set_margin_start(3)
        except Exception:
            pass
        frame.add(outer)
        provider = status.provider
        glyph_2 = PROVIDER_GLYPH_2.get(provider, "??")
        # v3 §7 — light theme darkens each provider accent by ×0.85 (pre-
        # computed in PROVIDER_ACCENT_HEX_LIGHT). ``accent_hex_for`` is
        # the single read-side helper; tests assert against it directly.
        accent = accent_hex_for(provider, getattr(self._config, "theme", "auto"))
        h = Gtk.Label()
        # Two-letter glyph + alias + provider + accent swatch label.
        header_markup = (
            f'<span foreground="{accent}">■</span> '
            f'<b>{esc(status.alias)}</b>  ·  '
            f'<tt>{glyph_2}</tt>  ·  <i>{esc(provider.value)}</i>'
        )
        h.set_markup(header_markup)
        h.set_xalign(0.0); outer.pack_start(h, False, False, 0)
        if status.error:
            e = Gtk.Label(); e.set_markup(f'<span foreground="#FF7A6B">! {esc(status.error)}</span>')
            e.set_xalign(0.0); outer.pack_start(e, False, False, 0)
        outer.pack_start(self._row(Gtk, "5h", status.five_hour_percent,
                                   status.five_hour_resets_at, now), False, False, 0)
        extra = (f"pace {esc(status.pace_label)}"
                 if status.pace_projection_percent is not None else None)
        outer.pack_start(self._row(Gtk, "7d", status.seven_day_percent,
                                   status.seven_day_resets_at, now, extra=extra), False, False, 0)
        # Stash accent + header markup for test inspection without
        # crawling the widget tree (MagicMock's get_children returns empty).
        try:
            setattr(frame, "_provider_accent", accent)
            setattr(frame, "_header_markup", header_markup)
        except Exception:
            pass
        return frame
    def _row(self, Gtk, label, pct, resets_at, now, extra=None):
        box = hbox(Gtk, spacing=8)
        t = Gtk.Label(label=label); t.set_xalign(0.0); t.set_size_request(32, -1)
        box.pack_start(t, False, False, 0)
        bar = Gtk.ProgressBar()
        bar.set_fraction(0.0 if pct is None else max(0.0, min(100.0, pct)) / 100.0)
        bar.set_size_request(180, -1); box.pack_start(bar, True, True, 0)
        p = Gtk.Label(label="—" if pct is None else f"{pct:.0f}%")
        p.set_size_request(48, -1); box.pack_start(p, False, False, 0)
        reset = humanize_reset(resets_at, now=now)
        meta = (f"resets in {reset}" if reset else "")
        if extra: meta = (f"{extra}   {meta}").strip()
        m = Gtk.Label(label=meta); m.set_xalign(0.0); box.pack_start(m, False, False, 0)
        return box
    def _on_add_submit(self, fields: dict) -> None:
        try:
            from ..cli import cmd_add
            cmd_add(Namespace(
                provider=Provider.parse(str(fields.get("provider", ""))),
                alias=str(fields.get("alias", "")),
                session_key=fields.get("session_key"),
                cookie=fields.get("cookie"),
                auth_json=fields.get("auth_json"),
                config_file=None, db_file=None, key_file=None))
        except Exception: pass
        self._refresh_accounts_list()
    def _refresh_accounts_list(self) -> None:
        if self._accounts_list is None or not _GTK_OK: return
        try:
            from gi.repository import Gtk  # type: ignore[import-not-found]
        except (ImportError, ValueError): return
        self._notify_buttons = {}
        lb = self._accounts_list
        for c in list(self._accounts_children):
            try: lb.remove(c)
            except Exception: pass
        self._accounts_children = []
        try: rows = self._db.list_accounts(include_inactive=True)
        except Exception: rows = []
        if not rows:
            e = Gtk.Label(label="No accounts yet. Click '+ Add account' below.")
            e.set_xalign(0.0); lb.pack_start(e, False, False, 0)
            self._accounts_children.append(e)
        else:
            for acc, _ in rows:
                row = self._account_row(Gtk, acc)
                lb.pack_start(row, False, False, 0); self._accounts_children.append(row)
        safe_call(lb, "show_all")
    def _account_row(self, Gtk, account):
        box = hbox(Gtk, spacing=8)
        lbl = Gtk.Label(label=f"{account.alias}  ·  {account.provider.value}")
        lbl.set_xalign(0.0); box.pack_start(lbl, True, True, 0)
        # v3 §10 — per-account notification opt-out toggle. Label shows
        # the CURRENT state; clicking flips it (and persists via
        # :meth:`_toggle_notify`).
        notify_on = self._config.is_notify_enabled(account.id)
        notify_label = f"Notify: {'on' if notify_on else 'off'}"
        notify_btn = Gtk.Button.new_with_label(notify_label)
        try:
            setattr(notify_btn, "_notify_label_text", notify_label)
        except Exception:
            pass
        notify_btn.connect("clicked", lambda _b, a=account: self._toggle_notify(a))
        self._notify_buttons[account.id] = notify_btn
        box.pack_start(notify_btn, False, False, 0)
        for label, handler in (("Test", self._on_test), ("Remove", self._on_remove)):
            b = Gtk.Button.new_with_label(label)
            b.connect("clicked", lambda _b, a=account, h=handler: h(a.id))
            box.pack_start(b, False, False, 0)
        return box

    def _toggle_notify(self, account) -> None:
        """v3 §10 — flip the per-account notification preference.

        Persists through :func:`custats.core.config.save_config` (module
        ref so tests can patch it), then rebuilds the Accounts list so
        the button label reflects the new state.
        """
        try:
            enabled = not self._config.is_notify_enabled(account.id)
            self._config.set_notify_enabled(account.id, enabled)
            _config_mod.save_config(self._config)
        except Exception:  # noqa: BLE001 — the toggle must never crash the UI
            pass
        self._refresh_accounts_list()
    def _on_test(self, account_id: str) -> None:
        if self._poller is None: return
        try:
            fut = self._poller.submit_from_any_thread(self._poller.poll_once())
            try: fut.result(timeout=2.0)  # type: ignore[union-attr]
            except Exception: pass
        except Exception: pass
    def _on_remove(self, account_id: str) -> None:
        try:
            from ..cli import cmd_remove
            cmd_remove(Namespace(account_id=account_id,
                                 config_file=None, db_file=None, key_file=None))
        except Exception: pass
        self._refresh_accounts_list()
    def _build_settings_tab(self, Gtk, nb) -> None:
        outer = vbox(Gtk, spacing=8, border=8)
        self._settings_widgets = {}
        cfg = self._config
        for key, label, widget in (
            ("refresh_interval", "Refresh interval (s)",
             spin(Gtk, cfg.refresh_interval_seconds, 10, 600, 5, 30)),
            ("notify_threshold", "Notify threshold (%)",
             spin(Gtk, cfg.notify_threshold_percent, 50, 99, 1, 5)),
            ("notify_on_recovery", "notify on recovery", self._switch(cfg.notify_on_recovery)),
            ("pace_enabled", "pace enabled", self._switch(cfg.pace_enabled)),
        ):
            outer.pack_start(labelled(Gtk, label, widget), False, False, 0)
            self._settings_widgets[key] = widget

        # Phase 8b — Theme picker (auto / light / dark). Bound to AppConfig.theme.
        theme_combo = Gtk.ComboBoxText()
        valid = ("auto", "light", "dark")
        for v in valid:
            theme_combo.append_text(v)
        theme_combo.set_active(
            valid.index(cfg.theme) if cfg.theme in valid else 0
        )
        outer.pack_start(labelled(Gtk, "Theme", theme_combo), False, False, 0)
        self._settings_widgets["theme"] = theme_combo

        pl = Gtk.Label(label="Show in menu bar"); pl.set_xalign(0.0)
        outer.pack_start(pl, False, False, 0)
        pb = vbox(Gtk, spacing=2, border=4)
        provider_switches: dict = {}
        for p in Provider:
            sw = self._switch(cfg.is_provider_visible(p))
            h = hbox(Gtk, spacing=8)
            h.pack_start(Gtk.Label(label=p.value), True, True, 0)
            h.pack_start(sw, False, False, 0)
            pb.pack_start(h, False, False, 0)
            provider_switches[p] = sw
        outer.pack_start(pb, False, False, 0)
        self._settings_widgets["provider_switches"] = provider_switches

        self._save_button = Gtk.Button.new_with_label("Save")
        self._save_button.connect("clicked", lambda *_: self._save())
        self._revert_button = Gtk.Button.new_with_label("Revert")
        self._revert_button.connect("clicked", lambda *_: self._revert())
        br = hbox(Gtk, spacing=8)
        br.pack_start(self._save_button, False, False, 0)
        br.pack_start(self._revert_button, False, False, 0)
        outer.pack_start(br, False, False, 0)

        # v3 §6 — Status legend + shape key at the bottom of the Settings
        # tab. 10pt monospace, ``--text-dim`` foreground so it doesn't
        # compete with the form. Tests inspect ``_settings_widgets`` keys
        # ``status_legend`` and ``shape_legend`` for assertion.
        legend_dots_markup = _build_legend_markup()
        shape_markup = f'<tt>{esc(_SHAPE_LEGEND_TEXT)}</tt>'

        legend_dots_lbl = Gtk.Label()
        legend_dots_lbl.set_markup(legend_dots_markup)
        legend_dots_lbl.set_xalign(0.0)
        outer.pack_start(legend_dots_lbl, False, False, 0)
        self._settings_widgets["status_legend"] = legend_dots_lbl

        shape_lbl = Gtk.Label()
        shape_lbl.set_markup(shape_markup)
        shape_lbl.set_xalign(0.0)
        outer.pack_start(shape_lbl, False, False, 0)
        self._settings_widgets["shape_legend"] = shape_lbl

        nb.append_page(outer, Gtk.Label(label="Settings"))
    def _switch(self, on):
        from gi.repository import Gtk  # mocked; ensures fresh mock per call
        sw = Gtk.Switch(); sw.set_state(bool(on)); return sw
    def _save(self) -> None:
        try:
            cfg = self._cfg_from_form(); _config_mod.save_config(cfg); self._config = cfg
        except Exception: pass
    def _revert(self) -> None:
        try:
            self._config = load_config(); self._populate_settings_form()
        except Exception: pass
    def _cfg_from_form(self) -> AppConfig:
        w = self._settings_widgets
        return AppConfig(
            refresh_interval_seconds=int(w["refresh_interval"].get_value()),
            notify_threshold_percent=int(w["notify_threshold"].get_value()),
            notify_on_recovery=bool(w["notify_on_recovery"].get_state()),
            pace_enabled=bool(w["pace_enabled"].get_state()),
            theme=str(w["theme"].get_active_text() or "auto"),
            show_in_menu_bar={p: bool(sw.get_state())
                              for p, sw in w.get("provider_switches", {}).items()},
            # Preserve per-account notify prefs (v3 §10) — the form has no
            # widget for them (they live on the Accounts tab), and a fresh
            # AppConfig would otherwise default the dict to empty and
            # wipe them on Save.
            notify_accounts=dict(self._config.notify_accounts))
    def _populate_settings_form(self) -> None:
        w = self._settings_widgets
        if not w: return
        cfg = self._config
        valid = ("auto", "light", "dark")
        try:
            w["refresh_interval"].set_value(float(cfg.refresh_interval_seconds))
            w["notify_threshold"].set_value(float(cfg.notify_threshold_percent))
            w["notify_on_recovery"].set_state(bool(cfg.notify_on_recovery))
            w["pace_enabled"].set_state(bool(cfg.pace_enabled))
            w["theme"].set_active(valid.index(cfg.theme) if cfg.theme in valid else 0)
            for p, sw in w.get("provider_switches", {}).items():
                sw.set_state(bool(cfg.is_provider_visible(p)))
        except Exception: pass
    def _apply_theme(self) -> None:
        """Load theme tokens into a CSS provider. No-op under GTK mocks.

        Per docs/design-tokens-v2.md §8: a single ``Gtk.CssProvider`` carries
        the tokens; widgets reference CSS classes (``card``, ``pill``,
        ``bar``) instead of hard-coded hex. Loading is best-effort.
        """
        t = _resolve_theme_tokens(self._config)
        css = (
            f".card {{ background: {t['surface_card']}; border: 1px solid {t['surface_border']}; border-radius: 6px; }}"
            f" .bar {{ color: {t['bar_empty']}; }}"
            f" .pill {{ border-radius: 9px; }}"
        )
        try:
            from gi.repository import Gtk, Gdk  # type: ignore[import-not-found]
            screen = Gdk.Screen.get_default()
            provider = Gtk.CssProvider.new()
            provider.load_from_data(css.encode("utf-8"))
            Gtk.StyleContext.add_provider_for_screen(
                screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        except Exception:
            pass
__all__ = ["MainWindow"]