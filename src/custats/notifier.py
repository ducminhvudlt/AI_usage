"""Desktop notifications via the freedesktop.org DBus spec.

Best-effort: if ``dbus-python`` isn't installed, every method becomes
a silent no-op so the polling loop keeps running on dev boxes.
"""
from __future__ import annotations

from .core.models import Account

try:
    import dbus  # type: ignore[import-not-found]
    from dbus.mainloop.glib import DBusGMainLoop  # type: ignore[import-not-found]

    _DBUS_AVAILABLE = True
except ImportError:  # exercised via test monkeypatch
    dbus = None  # type: ignore[assignment]
    DBusGMainLoop = None  # type: ignore[assignment]
    _DBUS_AVAILABLE = False

# ``DBusGMainLoop(set_as_default=True)`` must run at most once per
# process — calling it again trips a native glib assertion (SIGTRAP,
# exit 133) on boxes where dbus-python is installed. Multiple
# ``Notifier(enabled=True)`` constructions (e.g. one per ``cmd_run``
# test) previously re-ran it every time.
_MAINLOOP_INITIALIZED = False


def _init_mainloop_once() -> bool:
    """Initialize the DBus-Glib main loop once; ``False`` on failure."""
    global _MAINLOOP_INITIALIZED
    if _MAINLOOP_INITIALIZED:
        return True
    try:
        assert DBusGMainLoop is not None
        DBusGMainLoop(set_as_default=True)
        _MAINLOOP_INITIALIZED = True
        return True
    except Exception:
        return False


class Notifier:
    """Sends desktop notifications via freedesktop.org Notifications spec.

    Methods never raise — a misbehaving notification daemon must not
    take down the polling loop.
    """
    APP_NAME = "custats"

    def __init__(self, enabled: bool = True) -> None:
        self._enabled = bool(enabled) and _DBUS_AVAILABLE
        self._notify = None
        if self._enabled:
            try:
                if not _init_mainloop_once():
                    self._enabled = False
                else:
                    bus = dbus.SessionBus()  # type: ignore[union-attr]
                    proxy = bus.get_object(  # type: ignore[union-attr]
                        "org.freedesktop.Notifications",
                        "/org/freedesktop/Notifications",
                    )
                    self._notify = proxy.get_dbus_method(
                        "Notify", "org.freedesktop.Notifications"
                    )
            except Exception:
                self._enabled = False

    def send(
        self,
        title: str,
        body: str,
        *,
        urgency: int = 1,
        icon: str = "",
    ) -> None:
        """Send a notification. ``urgency``: 0=low, 1=normal, 2=critical."""
        if not self._enabled or self._notify is None:
            return
        try:
            self._notify(self.APP_NAME, 0, icon, str(title), str(body), [], {}, int(urgency))
        except Exception:
            pass

    def available(self) -> bool:
        return self._enabled and self._notify is not None

    def notify_at_limit(self, account: Account, percent: float) -> None:
        self.send(
            "Usage limit reached",
            f"{account.alias} hit {percent:.0f}% of 5-hour limit",
            urgency=2,
        )

    def notify_threshold(self, account: Account, percent: float) -> None:
        self.send(
            "Usage approaching limit",
            f"{account.alias} at {percent:.0f}% of 5-hour limit",
            urgency=1,
        )

    def notify_recovery(self, account: Account) -> None:
        self.send(
            "Usage recovered",
            f"{account.alias} is back within limits",
            urgency=0,
        )


def dbus_available() -> bool:
    """Return ``True`` if the optional ``dbus-python`` dependency is importable."""
    return _DBUS_AVAILABLE


__all__ = ["Notifier", "dbus_available"]
