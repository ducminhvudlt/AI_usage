"""Mock GTK for unit tests.

Known limitation (documented in README + design-tokens-v3 §10): the dev
box lacks ``girepository-2.0`` introspection typelibs, so there is no
visual GTK verification here — the whole UI suite runs against these
MagicMocks. :mod:`tests.ui.test_gtk_integration` is the opt-in escape
hatch for GTK-capable boxes (``CUSTATS_GTK_TESTS=1``).

Mock upgrades (v3 §9 companion contract): production code records its
own children (``_live_children``, ``account_rows``) and never relies on
``get_children()``; on the mock side we make menu composition directly
observable too:

- ``Gtk.MenuItem.new_with_label(label)`` returns a **memoized** mock per
  label, so tests can locate a footer item and inspect its ``connect``
  registrations.
- Bare ``Gtk.MenuItem()`` calls return a **fresh** mock each time, so
  per-card widgets (account rows, welcome card) don't alias each other.
- ``Gtk.Menu().append(...)`` calls are recorded on the Menu mock itself
  (``Gtk.Menu.return_value.append.call_args_list``) — that list IS the
  build-order child tracker when a test needs it.
"""
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

    # Label-keyed menu items: the same mock for repeated calls with the
    # same label, so a test can find the "Open Dashboard" item and check
    # which handler its ``connect("activate", ...)`` received.
    _menu_items: dict[str, MagicMock] = {}

    def _make_menu_item(label: str) -> MagicMock:
        return _menu_items.setdefault(label, MagicMock(_label=label))

    repo.Gtk.MenuItem.new_with_label.side_effect = _make_menu_item
    # Bare MenuItem() calls get a fresh mock per call so per-card widgets
    # (account rows, the welcome card) remain distinguishable.
    repo.Gtk.MenuItem.side_effect = lambda *a, **kw: MagicMock()
    repo.Gtk.SeparatorMenuItem.new.side_effect = lambda: MagicMock()
    return repo.Gtk, repo.AppIndicator3


def pytest_configure(config):
    """Auto-install GTK mocks at collection time so tests don't have to."""
    install_gtk_mocks()
