"""Marker package for the custats test suite.

Making ``tests`` a real package lets test modules import shared helpers
(``from tests.ui.conftest import install_gtk_mocks``) under both
``python -m pytest`` and bare ``pytest`` invocations — with only the
namespace-package fallback, bare pytest resolves ``tests.ui`` imports
differently and the import fails.
"""
