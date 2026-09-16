"""Tests for the Phase 5 AddAccountDialog."""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from custats.core.models import Provider
from custats.ui.add_account_dialog import AddAccountDialog


# ---------------------------------------------------------------------- #
# GTK mock helpers
# ---------------------------------------------------------------------- #


@pytest.fixture(autouse=True)
def _distinct_widget_mocks():
    """Same fixture as test_main_window — every widget ctor returns a fresh
    mock so per-credential fields can be told apart."""
    from gi.repository import Gtk  # mocked by tests/ui/conftest.py

    def _by_label(label: str) -> MagicMock:
        return MagicMock(_label=label)

    def _fresh(*_a, **_kw) -> MagicMock:
        return MagicMock()

    for factory in (Gtk.Button.new_with_label,):
        factory.side_effect = _by_label
    for factory in (
        Gtk.SpinButton, Gtk.Switch, Gtk.ComboBoxText, Gtk.Adjustment,
        Gtk.Label, Gtk.Entry, Gtk.ProgressBar, Gtk.Frame, Gtk.Box,
        Gtk.ScrolledWindow, Gtk.Notebook, Gtk.Window, Gtk.Dialog,
    ):
        factory.side_effect = _fresh
    yield
    for factory in (Gtk.Button.new_with_label,
                    Gtk.SpinButton, Gtk.Switch, Gtk.ComboBoxText,
                    Gtk.Adjustment, Gtk.Label, Gtk.Entry, Gtk.ProgressBar,
                    Gtk.Frame, Gtk.Box, Gtk.ScrolledWindow, Gtk.Notebook,
                    Gtk.Window, Gtk.Dialog):
        factory.side_effect = None


def _make_dialog(*, on_submit=None) -> AddAccountDialog:
    """Construct a dialog with the GTK mocks. ``on_submit`` records dispatches."""
    received: list[dict] = []
    def _record(payload: dict) -> None:
        received.append(payload)
    dlg = AddAccountDialog(parent=None, on_submit=_record if on_submit is None else on_submit)
    return dlg, received


def _drive_ok(dlg: AddAccountDialog) -> None:
    """Find the dialog's ``response("response", handler)`` callback and fire it
    with the OK response type so the dialog collects + dispatches its fields."""
    from gi.repository import Gtk  # mocked by tests/ui/conftest.py
    # The dialog's connect("response", ...) is the last entry — pop it off.
    handler = None
    for call in dlg._dialog.connect.call_args_list:  # type: ignore[attr-defined]
        if call.args and call.args[0] == "response":
            handler = call.args[1]
    assert handler is not None, "Dialog did not register a 'response' handler"
    handler(dlg._dialog, Gtk.ResponseType.OK)  # type: ignore[attr-defined]


def _entries(dlg: AddAccountDialog) -> dict[str, MagicMock]:
    """Return the Entry mocks keyed by ``alias``, ``session_key``, ``cookie``,
    ``auth_json``. The dialog stores them in ``self._fields``."""
    return dlg._fields  # type: ignore[attr-defined]


def _select_provider(dlg: AddAccountDialog, provider: Provider) -> None:
    """Simulate selecting a provider in the dropdown by setting its
    ``active_text`` and triggering the ``changed`` callback the dialog wired up."""
    combo = dlg._provider_combo  # type: ignore[attr-defined]
    combo.get_active_text.return_value = provider.value
    # Find and invoke the "changed" handler.
    handler = None
    for call in combo.connect.call_args_list:
        if call.args and call.args[0] == "changed":
            handler = call.args[1]
            break
    assert handler is not None, "Provider combo did not register a 'changed' handler"
    handler(combo)


# ---------------------------------------------------------------------- #
# construction
# ---------------------------------------------------------------------- #


def test_dialog_constructs_without_crashing():
    dlg, _ = _make_dialog()
    assert dlg._dialog is not None  # type: ignore[attr-defined]


def test_dialog_creates_required_widgets():
    dlg, _ = _make_dialog()
    fields = _entries(dlg)
    # Provider combo + alias + 3 credential fields → 5 entries.
    assert "alias" in fields
    assert "session_key" in fields
    assert "cookie" in fields
    assert "auth_json" in fields


# ---------------------------------------------------------------------- #
# collect & dispatch
# ---------------------------------------------------------------------- #


def test_dialog_collects_inputs():
    """Driving the OK button must invoke ``on_submit`` with the right dict."""
    dlg, received = _make_dialog()
    # Pick a Claude account, type values into the alias and session_key fields.
    _select_provider(dlg, Provider.CLAUDE)
    fields = _entries(dlg)
    fields["alias"].get_text.return_value = "work"
    fields["session_key"].get_text.return_value = "sk-test-1234"
    _drive_ok(dlg)
    assert len(received) == 1
    payload = received[0]
    assert payload["provider"] == "claude"
    assert payload["alias"] == "work"
    assert payload["session_key"] == "sk-test-1234"


def test_dialog_skips_unused_provider_fields():
    """For Claude, only ``session_key`` should appear in the dict (no cookie/auth_json)."""
    dlg, received = _make_dialog()
    _select_provider(dlg, Provider.CLAUDE)
    fields = _entries(dlg)
    fields["alias"].get_text.return_value = "x"
    fields["session_key"].get_text.return_value = "k"
    # Even though cookie / auth_json mocks exist, no values are typed.
    fields["cookie"].get_text.return_value = ""
    fields["auth_json"].get_text.return_value = ""
    _drive_ok(dlg)
    assert len(received) == 1
    payload = received[0]
    assert "cookie" not in payload
    assert "auth_json" not in payload


# ---------------------------------------------------------------------- #
# per-provider field visibility
# ---------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "provider,active_fields",
    [
        (Provider.CLAUDE, {"session_key"}),
        (Provider.CODEX, {"cookie", "auth_json"}),
        (Provider.GROK, {"auth_json"}),
        (Provider.CURSOR, {"cookie"}),
    ],
)
def test_dialog_dispatches_per_provider(provider, active_fields):
    """For each provider, only its credential fields are visible; the rest
    are hidden via ``set_visible(False)``."""
    dlg, _ = _make_dialog()
    fields = _entries(dlg)

    # Snapshot every credential widget's ``set_visible`` calls so far (the
    # initial refresh at construction time).
    initial_calls = {
        name: list(widget.set_visible.call_args_list)
        for name, widget in fields.items()
    }

    # Select the new provider — the dialog fires its ``changed`` handler,
    # which calls ``_refresh`` and updates ``set_visible`` on every field.
    _select_provider(dlg, provider)

    for field_name in ("session_key", "cookie", "auth_json"):
        widget = fields[field_name]
        new_calls = widget.set_visible.call_args_list[len(initial_calls[field_name]):]
        assert new_calls, (
            f"{field_name} was not refreshed after selecting {provider.value}"
        )
        # Last ``set_visible`` argument should match expected visibility.
        last_visible = bool(new_calls[-1].args[0])
        expected = field_name in active_fields
        assert last_visible is expected, (
            f"{field_name} visibility after selecting {provider.value}: "
            f"got {last_visible}, expected {expected}"
        )


# ---------------------------------------------------------------------- #
# response routing
# ---------------------------------------------------------------------- #


def test_dialog_cancel_does_not_dispatch():
    """A CANCEL response must not invoke ``on_submit``."""
    from gi.repository import Gtk  # mocked by tests/ui/conftest.py
    dlg, received = _make_dialog()
    handler = None
    for call in dlg._dialog.connect.call_args_list:  # type: ignore[attr-defined]
        if call.args and call.args[0] == "response":
            handler = call.args[1]
    assert handler is not None
    handler(dlg._dialog, Gtk.ResponseType.CANCEL)  # type: ignore[attr-defined]
    assert received == []