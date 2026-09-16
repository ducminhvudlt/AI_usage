"""Headless import smoke test for the UI subpackage."""
from __future__ import annotations


def test_ui_import_does_not_require_gtk():
    """Importing the UI subpackage must succeed on a GTK-less host."""
    import custats.ui  # noqa: F401
    from custats.ui.popup import PopupMenu  # noqa: F401
    from custats.ui.tray import TrayIcon, TrayUnavailable  # noqa: F401


def test_tray_unavailable_is_a_runtime_error():
    """``TrayUnavailable`` must be catchable as ``RuntimeError``."""
    from custats.ui.tray import TrayUnavailable

    assert issubclass(TrayUnavailable, RuntimeError)
