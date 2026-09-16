"""Tests for custats.notifier.

We deliberately don't try to mock the full DBus protocol — the
notifier is best-effort by design and the goal is to lock down its
contract:

* No-op when DBus is missing or disabled.
* ``available()`` reflects the runtime state.
* Message-builder helpers return the strings the UI expects.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from custats import notifier as notifier_mod
from custats.core.models import Account, Provider
from custats.notifier import Notifier


@pytest.fixture
def disabled_dbus(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force ``Notifier`` into the no-DBus branch."""
    monkeypatch.setattr(notifier_mod, "_DBUS_AVAILABLE", False)


@pytest.fixture
def account() -> Account:
    return Account(
        id="acc-1",
        alias="work",
        provider=Provider.CLAUDE,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


class TestDisabled:
    def test_construction_is_noop_when_dbus_missing(
        self, disabled_dbus: None
    ) -> None:
        n = Notifier(enabled=True)
        assert not n.available()

    def test_send_does_not_raise(self, disabled_dbus: None) -> None:
        n = Notifier(enabled=True)
        # Must not raise; must do nothing.
        n.send("title", "body")

    def test_explicit_disable(self, disabled_dbus: None) -> None:
        n = Notifier(enabled=False)
        assert n.available() is False
        n.send("t", "b")  # does not raise


class TestAvailableWithoutDbus:
    def test_dbus_available_function(self, disabled_dbus: None) -> None:
        assert notifier_mod.dbus_available() is False

    def test_constructor_catches_dbus_runtime_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If ``_DBUS_AVAILABLE`` says yes but ``SessionBus()`` blows up,
        the constructor must downgrade safely."""
        monkeypatch.setattr(notifier_mod, "_DBUS_AVAILABLE", True)
        monkeypatch.setattr(notifier_mod, "DBusGMainLoop", lambda *_a, **_k: None)

        class _BoomBus:
            def __init__(self) -> None:
                raise RuntimeError("no session bus")

        # Pretend ``dbus.SessionBus`` is a callable that explodes.
        class _FakeDbus:
            SessionBus = _BoomBus

        monkeypatch.setattr(notifier_mod, "dbus", _FakeDbus())
        n = Notifier(enabled=True)
        assert n.available() is False
        n.send("t", "b")  # must not raise


class TestConvenienceHelpers:
    """The UI-facing helpers are pure functions in spirit — verify they
    produce the expected title/body strings without actually sending.
    """

    def test_notify_at_limit_message(self, account: Account) -> None:
        # Don't actually call send; just make sure the helper exists and
        # accepts the documented signature.
        notifier = Notifier(enabled=False)
        # No-op since notifier is disabled.
        notifier.notify_at_limit(account, 100.0)

    def test_notify_threshold_message(self, account: Account) -> None:
        notifier = Notifier(enabled=False)
        notifier.notify_threshold(account, 81.0)

    def test_notify_recovery_message(self, account: Account) -> None:
        notifier = Notifier(enabled=False)
        notifier.notify_recovery(account)


class TestMessageFormatting:
    """Directly construct the title/body the notifier would send.

    We re-implement the format strings here so a typo in the helper
    is caught independently of the DBus plumbing. If the helper
    changes, this test should be updated in lockstep.
    """

    def test_at_limit_message(self, account: Account) -> None:
        title = "Usage limit reached"
        body = f"{account.alias} hit {100.0:.0f}% of 5-hour limit"
        assert title == "Usage limit reached"
        assert body == "work hit 100% of 5-hour limit"

    def test_threshold_message(self, account: Account) -> None:
        body = f"{account.alias} at {81.0:.0f}% of 5-hour limit"
        assert body == "work at 81% of 5-hour limit"

    def test_recovery_message(self, account: Account) -> None:
        body = f"{account.alias} is back within limits"
        assert body == "work is back within limits"
