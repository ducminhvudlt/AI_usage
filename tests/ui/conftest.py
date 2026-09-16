"""Mock GTK for unit tests."""
from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock


def install_gtk_mocks():
    """Install fake ``gi``, ``gi.repository``, and ``cairo`` modules.

    Returns the ``(Gtk, AppIndicator3)`` mocks so individual tests can
    configure behaviour further. Safe to call repeatedly; subsequent
    calls return fresh mocks.
    """
    gi = types.ModuleType("gi")
    gi.require_version = MagicMock()
    repo = types.ModuleType("gi.repository")
    repo.Gtk = MagicMock()
    repo.AppIndicator3 = MagicMock()
    repo.GdkPixbuf = MagicMock()
    repo.Pango = MagicMock()
    repo.PangoCairo = MagicMock()
    repo.IndicatorCategory = MagicMock()
    repo.IndicatorStatus = MagicMock()
    sys.modules["gi"] = gi
    sys.modules["gi.repository"] = repo
    sys.modules["cairo"] = MagicMock()
    return repo.Gtk, repo.AppIndicator3


def pytest_configure(config):
    """Auto-install GTK mocks at collection time so tests don't have to."""
    install_gtk_mocks()
