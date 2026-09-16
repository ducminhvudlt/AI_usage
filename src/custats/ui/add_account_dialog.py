"""Phase 5 add-account dialog.

Thin wrapper that collects ``provider / alias / credentials`` from a
``Gtk.Dialog`` and dispatches the result via ``on_submit``. Validation
and DB insertion live in :func:`custats.cli.cmd_add` — the dialog
deliberately does not duplicate them.
"""
from __future__ import annotations

from typing import Callable

from ..core.models import Provider


_GTK_OK = False
_GTK_PROBED = False


def _try_gtk() -> bool:
    global _GTK_OK, _GTK_PROBED
    if _GTK_PROBED:
        return _GTK_OK
    _GTK_PROBED = True
    try:
        import gi  # type: ignore[import-not-found]

        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk  # noqa: F401

        _GTK_OK = True
    except (ImportError, ValueError):
        _GTK_OK = False
    return _GTK_OK


# Per-provider credential fields. ``cmd_add`` already knows how to map
# these onto its argparse flags, so we keep them as strings here.
_CREDENTIAL_FIELDS: dict[Provider, tuple[str, ...]] = {
    Provider.CLAUDE: ("session_key",),
    Provider.CODEX: ("cookie", "auth_json"),
    Provider.GROK: ("auth_json",),
    Provider.CURSOR: ("cookie",),
}

_FIELD_LABEL = {"session_key": "Session key", "cookie": "Cookie value", "auth_json": "Auth JSON path"}


class AddAccountDialog:
    """Modal dialog: collect provider + alias + credentials."""

    def __init__(self, parent, *, on_submit: Callable[[dict], None]) -> None:
        if not _try_gtk():
            raise RuntimeError("GTK not available — cannot build AddAccountDialog")
        import gi  # type: ignore[import-not-found]

        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk

        self._on_submit = on_submit
        self._fields: dict[str, object] = {}
        self._provider_combo = None
        self._dialog = None

        dlg = Gtk.Dialog(title="Add account", transient_for=parent, flags=0)
        dlg.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dlg.add_button("Add", Gtk.ResponseType.OK)
        dlg.set_default_response(Gtk.ResponseType.OK)
        dlg.set_border_width(8)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        outer.set_border_width(8)
        dlg.get_content_area().add(outer)

        combo = Gtk.ComboBoxText()
        for p in Provider:
            combo.append_text(p.value)
        combo.set_active(0)
        combo.connect("changed", lambda *_: self._refresh(Gtk))
        outer.pack_start(self._row(Gtk, "Provider", combo), False, False, 0)
        self._provider_combo = combo

        alias = Gtk.Entry()
        alias.set_placeholder_text("Friendly alias (e.g. 'work')")
        outer.pack_start(self._row(Gtk, "Alias", alias), False, False, 0)
        self._fields["alias"] = alias

        for field_name in ("session_key", "cookie", "auth_json"):
            e = Gtk.Entry()
            if field_name == "session_key":
                e.set_visibility(False)
                e.set_placeholder_text("sk-…")
            elif field_name == "cookie":
                e.set_placeholder_text("Cookie value")
            else:
                e.set_placeholder_text("/path/to/auth.json")
            outer.pack_start(self._row(Gtk, _FIELD_LABEL[field_name], e), False, False, 0)
            self._fields[field_name] = e

        self._refresh(Gtk)
        dlg.show_all()
        self._dialog = dlg
        dlg.connect("response", self._on_response)

    def show(self) -> None:
        if self._dialog is None:
            return
        try:
            self._dialog.run()
        except Exception:
            pass

    def _row(self, Gtk, label_text, widget):
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        lbl = Gtk.Label(label=label_text)
        lbl.set_xalign(0.0)
        lbl.set_size_request(120, -1)
        box.pack_start(lbl, False, False, 0)
        box.pack_start(widget, True, True, 0)
        return box

    def _provider(self) -> Provider:
        if self._provider_combo is None:
            return Provider.CLAUDE
        try:
            return Provider.parse(str(self._provider_combo.get_active_text() or "claude"))
        except Exception:
            return Provider.CLAUDE

    def _refresh(self, Gtk) -> None:
        active = set(_CREDENTIAL_FIELDS.get(self._provider(), ()))
        for field_name, widget in self._fields.items():
            if field_name == "alias":
                continue
            try:
                widget.set_visible(field_name in active)
            except Exception:
                pass

    def _on_response(self, dialog, response_id: int) -> None:
        import gi  # type: ignore[import-not-found]

        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk as _Gtk

        try:
            if response_id == _Gtk.ResponseType.OK:
                try:
                    self._on_submit(self._collect())
                except Exception:
                    pass
        finally:
            try:
                dialog.destroy()
            except Exception:
                pass
            self._dialog = None

    def _collect(self) -> dict:
        provider = self._provider()
        out: dict = {"provider": provider.value}
        alias_w = self._fields.get("alias")
        if alias_w is not None:
            try:
                out["alias"] = str(alias_w.get_text()).strip()
            except Exception:
                out["alias"] = ""
        for field_name in _CREDENTIAL_FIELDS.get(provider, ()):
            widget = self._fields.get(field_name)
            if widget is None:
                continue
            try:
                value = str(widget.get_text()).strip()
            except Exception:
                value = ""
            if value:
                out[field_name] = value
        return out


__all__ = ["AddAccountDialog"]