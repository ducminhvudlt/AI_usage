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
    """Keep the mock suite free of native DBus/Glib calls.

    On hosts where ``dbus-python`` is installed (e.g. CI), a real
    ``Notifier(enabled=True)`` would call
    ``DBusGMainLoop(set_as_default=True)`` — while ``gi`` is mocked by
    the UI suite — corrupting glib state and SIGTRAP-ing the pytest
    process at exit (exit code 133). Forcing the module-level flag off
    makes every Notifier a no-op here; the notifier-specific tests
    re-enable/monkeypatch the flag themselves to cover both branches.
    """
    try:
        from custats import notifier as _notifier_mod

        _notifier_mod._DBUS_AVAILABLE = False
    except Exception:  # pragma: no cover — notifier must exist
        pass