"""Tiny widget-construction helpers shared by :mod:`main_window` and
:mod:`add_account_dialog`. Kept module-private to the UI package — not
imported by anything outside ``custats.ui``.
"""
from __future__ import annotations


def esc(text: str) -> str:
    """Escape a string for Pango markup."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def vbox(Gtk, *, spacing: int = 0, border: int = 0):
    """Build a vertical ``Gtk.Box``."""
    b = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=spacing)
    if border:
        b.set_border_width(border)
    return b


def hbox(Gtk, *, spacing: int = 0, border: int = 0):
    """Build a horizontal ``Gtk.Box``."""
    b = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=spacing)
    if border:
        b.set_border_width(border)
    return b


def spin(Gtk, value: float, lo: float, hi: float, step: float = 1.0, page: float = 5.0):
    """Build a ``Gtk.SpinButton`` with the given bounds and value."""
    sb = Gtk.SpinButton()
    sb.set_adjustment(Gtk.Adjustment(value=float(value), lower=lo, upper=hi,
                                      step_increment=step, page_increment=page))
    return sb


def labelled(Gtk, text: str, widget, *, lw: int = 200):
    """Pack ``widget`` into a labelled horizontal row."""
    h = hbox(Gtk, spacing=8)
    l = Gtk.Label(label=text)
    l.set_xalign(0.0)
    l.set_size_request(lw, -1)
    h.pack_start(l, False, False, 0)
    h.pack_start(widget, False, False, 0)
    return h


def safe_call(widget, method: str, *args, **kwargs) -> None:
    """Call ``widget.<method>(*args, **kwargs)`` swallowing exceptions.

    Lets us invoke ``widget.show_all()`` etc. without checking whether
    the GTK mock raises."""
    try:
        getattr(widget, method)(*args, **kwargs)
    except Exception:
        pass


__all__ = ["esc", "vbox", "hbox", "spin", "labelled", "safe_call"]