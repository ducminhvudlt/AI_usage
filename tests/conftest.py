"""Conftest for the custats test suite."""

from __future__ import annotations

import sys
from pathlib import Path

# ``httpx`` is used by the provider-adapter tests via ``MockTransport``.
# Importing it here makes ``httpx`` available to every test module
# without each test having to remember the import, and surfaces any
# httpx-related environment problems at collection time.
import httpx  # noqa: F401

# Make sure ``import custats`` works when pytest is invoked directly
# (e.g. ``pytest tests/`` from the repo root) without installing the
# package. The ``[tool.pytest.ini_options]`` pythonpath also handles
# this, but adding it here makes the test files robust to being run
# by other means (tox, IDE runners, etc.).
SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def pytest_configure(config):
    """Keep the mock suite free of native GTK/DBus calls.

    Two guards, both needed on hosts where the real bindings ARE
    installed (e.g. CI runners):

    1. Install the mock ``gi``/``cairo`` modules suite-wide. Otherwise
       ``cmd_run``-level tests really construct GTK objects and make
       native Gdk calls without a display, which aborts the process
       (SIGTRAP, exit 133). The opt-in real-GTK suite purges these
       mocks itself before importing real bindings.
    2. Force the notifier off. A real ``DBusGMainLoop(set_as_default=True)``
       inside the pytest process corrupts glib state while ``gi`` is
       mocked. The notifier tests re-enable/monkeypatch the flag
       themselves to cover both branches.
    """
    try:
        from custats import notifier as _notifier_mod

        _notifier_mod._DBUS_AVAILABLE = False
    except Exception:  # pragma: no cover — notifier must exist
        pass
    try:
        from tests.ui.conftest import install_gtk_mocks

        install_gtk_mocks()
    except Exception:  # pragma: no cover — mocks must exist
        pass