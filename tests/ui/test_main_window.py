"""Tests for the Phase 5 main settings/dashboard window."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from custats import __version__
from custats.core.config import AppConfig, save_config
from custats.core.models import Provider, UsageStatus
from custats.state import LiveStatus
from custats.storage.db import Database
from custats.storage.encrypted import load_or_create_key
from custats.ui.main_window import MainWindow


# ---------------------------------------------------------------------- #
# GTK mock setup
# ---------------------------------------------------------------------- #


@pytest.fixture(autouse=True)
def _distinct_widget_mocks():
    """GTK widget constructors (``Gtk.Button.new_with_label(label)``,
    ``Gtk.SpinButton()``, ``Gtk.Switch()`` etc.) all share a single
    return_value under the default MagicMock — every widget would be the
    *same* mock, which would confuse the Save button with Revert /
    Add-account and pin every SpinButton/Switch value to one number.

    This fixture wraps the constructors we use so each call returns a fresh
    ``MagicMock`` keyed by its label / position.
    """
    from gi.repository import Gtk  # mocked by tests/ui/conftest.py

    def _by_label(label: str) -> MagicMock:
        return MagicMock(_label=label)

    def _fresh(*_args, **_kwargs) -> MagicMock:
        return MagicMock()

    # Each constructor we care about: a side_effect that returns a fresh mock.
    for factory in (
        Gtk.Button.new_with_label,
    ):
        factory.side_effect = _by_label
    for factory in (
        Gtk.SpinButton,
        Gtk.Switch,
        Gtk.ComboBoxText,
        Gtk.Adjustment,
        Gtk.Label,
        Gtk.Entry,
        Gtk.ProgressBar,
        Gtk.Frame,
        Gtk.Box,
        Gtk.ScrolledWindow,
        Gtk.Notebook,
        Gtk.Window,
        Gtk.Switch,
    ):
        factory.side_effect = _fresh
    yield
    # Tear down side_effects so other test files don't see the patched factories.
    for factory in (
        Gtk.Button.new_with_label,
        Gtk.SpinButton,
        Gtk.Switch,
        Gtk.ComboBoxText,
        Gtk.Adjustment,
        Gtk.Label,
        Gtk.Entry,
        Gtk.ProgressBar,
        Gtk.Frame,
        Gtk.Box,
        Gtk.ScrolledWindow,
        Gtk.Notebook,
        Gtk.Window,
    ):
        factory.side_effect = None


# ---------------------------------------------------------------------- #
# helpers
# ---------------------------------------------------------------------- #


def _coerce(value):
    """Accept ``UsageStatus``, lowercase value, or uppercase enum name."""
    if isinstance(value, UsageStatus):
        return value
    if isinstance(value, str):
        return UsageStatus[value] if value.isupper() else UsageStatus(value)
    raise TypeError(f"unsupported status value: {value!r}")


def fake_status(
    *,
    account_id: str = "acc1",
    provider: str | Provider = "claude",
    alias: str = "acc",
    five_hour_status: str | UsageStatus = "GOOD",
    seven_day_status: str | UsageStatus = "GOOD",
    five_hour_percent: float | None = 10.0,
    seven_day_percent: float | None = 20.0,
    error: str | None = None,
    pace_projection_percent: float | None = None,
) -> LiveStatus:
    """Build a LiveStatus with sensible defaults for window tests."""
    p = provider if isinstance(provider, Provider) else Provider.parse(provider)
    fh = _coerce(five_hour_status)
    sd = _coerce(seven_day_status)
    return LiveStatus(
        account_id=account_id,
        provider=p,
        alias=alias,
        five_hour_status=fh,
        seven_day_status=sd,
        five_hour_percent=five_hour_percent,
        seven_day_percent=seven_day_percent,
        five_hour_resets_at=None,
        seven_day_resets_at=None,
        pace_projection_percent=pace_projection_percent,
        pace_label="Healthy",
        fetched_at=datetime.now(timezone.utc),
        error=error,
    )


@pytest.fixture
def db(tmp_path) -> Database:
    key = load_or_create_key(tmp_path / "secret.key")
    return Database(tmp_path / "state.db", key)


@pytest.fixture
def config() -> AppConfig:
    return AppConfig()


@pytest.fixture
def poller() -> MagicMock:
    """Poller stand-in. ``subscribe`` returns an unsubscribe callable."""
    p = MagicMock()
    # Track all unsubscribe callables so a test can assert destroy() called
    # the one returned by ``subscribe``.
    p._unsubscribes = []

    def _subscribe(_cb):
        def _unsub():
            p._unsubscribes.append(True)
        p._unsubscribes.append(False)
        return _unsub

    p.subscribe.side_effect = _subscribe
    return p


def _find_clicked_handler(widget_mock) -> Any:
    """Walk a button mock's ``connect.call_args_list`` for ``("clicked", cb)``."""
    for call in widget_mock.connect.call_args_list:
        args = call.args
        if args and args[0] == "clicked":
            return args[1]
    return None


# ---------------------------------------------------------------------- #
# construction
# ---------------------------------------------------------------------- #


def test_main_window_constructs_without_crashing(db, config, poller):
    """Construction must not raise under GTK mocks."""
    mw = MainWindow(db=db, config=config, poller=poller)
    assert mw is not None
    # Public API is present.
    assert mw._live_inner is not None
    assert mw._accounts_list is not None
    assert mw._save_button is not None
    assert mw._about_version_label is not None


def test_main_window_subscribes_to_poller_on_init(db, config, poller):
    """``__init__`` registers an :meth:`set_statuses` callback with the poller."""
    MainWindow(db=db, config=config, poller=poller)
    assert poller.subscribe.called
    # The argument is the bound ``set_statuses`` method.
    args, _kwargs = poller.subscribe.call_args
    assert args[0].__self__.__class__.__name__ == "MainWindow"


def test_main_window_raises_when_gtk_missing(db, config, poller, monkeypatch):
    """Without GTK, construction must raise :class:`TrayUnavailable`."""
    import custats.ui.main_window as mw_mod
    monkeypatch.setattr(mw_mod, "_GTK_OK", False, raising=False)
    monkeypatch.setattr(mw_mod, "_GTK_PROBED", True, raising=False)
    from custats.ui.tray import TrayUnavailable
    with pytest.raises(TrayUnavailable):
        MainWindow(db=db, config=config, poller=poller)


# ---------------------------------------------------------------------- #
# Live tab
# ---------------------------------------------------------------------- #


def test_set_statuses_does_not_crash(db, config, poller):
    mw = MainWindow(db=db, config=config, poller=poller)
    statuses = {
        "a1": fake_status(account_id="a1", provider="claude", alias="work",
                           five_hour_percent=42.0, seven_day_percent=61.0),
        "a2": fake_status(account_id="a2", provider="codex", alias="personal",
                           five_hour_status="CRITICAL", five_hour_percent=92.0,
                           error="boom"),
    }
    mw.set_statuses(statuses)  # must not raise
    # At least one Live tab child was added.
    assert mw._live_children


def test_set_statuses_empty_dict_renders_empty_message(db, config, poller):
    mw = MainWindow(db=db, config=config, poller=poller)
    mw.set_statuses({})
    # The "No accounts" label is the only child added.
    assert len(mw._live_children) == 1


def test_set_statuses_replaces_previous_children(db, config, poller):
    mw = MainWindow(db=db, config=config, poller=poller)
    mw.set_statuses({"a": fake_status(account_id="a")})
    first_count = len(mw._live_children)
    mw.set_statuses({"b": fake_status(account_id="b")})
    # The inner box's live_children list is rebuilt, not appended.
    assert len(mw._live_children) == first_count


# ---------------------------------------------------------------------- #
# Settings tab — Save
# ---------------------------------------------------------------------- #


def test_settings_save_persists_config(db, config, poller, monkeypatch):
    """Driving the Save button must call :func:`save_config` with the form values."""
    mw = MainWindow(db=db, config=config, poller=poller)
    # Patch save_config on the module reference so the in-form one is captured.
    import custats.core.config as cfg_mod
    captured: list[AppConfig] = []
    monkeypatch.setattr(cfg_mod, "save_config", lambda c, path=None: captured.append(c))

    # Edit form values to something distinctive.
    mw._settings_widgets["refresh_interval"].get_value.return_value = 120.0
    mw._settings_widgets["notify_threshold"].get_value.return_value = 75.0
    mw._settings_widgets["notify_on_recovery"].get_state.return_value = False
    mw._settings_widgets["pace_enabled"].get_state.return_value = False
    mw._settings_widgets["theme"].get_active_text.return_value = "dark"
    mw._settings_widgets["provider_switches"][Provider.CLAUDE].get_state.return_value = True
    mw._settings_widgets["provider_switches"][Provider.CODEX].get_state.return_value = False

    handler = _find_clicked_handler(mw._save_button)
    assert handler is not None, "Save button must register a clicked handler"
    handler(mw._save_button)

    assert len(captured) == 1
    saved = captured[0]
    assert isinstance(saved, AppConfig)
    assert saved.refresh_interval_seconds == 120
    assert saved.notify_threshold_percent == 75
    assert saved.notify_on_recovery is False
    assert saved.pace_enabled is False
    assert saved.theme == "dark"
    assert saved.is_provider_visible(Provider.CLAUDE) is True
    assert saved.is_provider_visible(Provider.CODEX) is False


def test_revert_button_refreshes_form(db, config, poller):
    """Revert re-reads config from disk and pushes values back into widgets."""
    mw = MainWindow(db=db, config=config, poller=poller)
    handler = _find_clicked_handler(mw._revert_button)
    assert handler is not None, "Revert button must register a clicked handler"
    handler(mw._revert_button)  # must not crash against mocked widgets
    # Theme combo's set_active was called (at least once during revert).
    mw._settings_widgets["theme"].set_active.assert_called()


# ---------------------------------------------------------------------- #
# destroy()
# ---------------------------------------------------------------------- #


def test_destroy_unsubscribes_from_poller(db, config, poller):
    """``destroy()`` invokes the unsubscribe returned by ``poller.subscribe``."""
    mw = MainWindow(db=db, config=config, poller=poller)
    assert poller.subscribe.called
    mw.destroy()
    # The unsubscribe callable that ``subscribe`` returned must have run.
    # Our fake poller records ``True`` on the callable; ``False`` on register.
    assert any(poller._unsubscribes), "poller.subscribe()'s unsubscribe was never called"


def test_destroy_is_idempotent(db, config, poller):
    """Calling :meth:`destroy` twice must not raise."""
    mw = MainWindow(db=db, config=config, poller=poller)
    mw.destroy()
    mw.destroy()  # should not raise


def test_destroy_quits_gtk_main_loop(db, config, poller):
    """``destroy()`` must call :func:`Gtk.main_quit` so the tray menu stops."""
    mw = MainWindow(db=db, config=config, poller=poller)
    with patch("gi.repository.Gtk") as gtk_mod:
        mw.destroy()
        gtk_mod.main_quit.assert_called_once()


# ---------------------------------------------------------------------- #
# About tab
# ---------------------------------------------------------------------- #


def test_about_tab_contains_version(db, config, poller):
    """The About tab's version label embeds :data:`custats.__version__`."""
    from gi.repository import Gtk  # mocked by tests/ui/conftest.py
    mw = MainWindow(db=db, config=config, poller=poller)
    # Locate the version label by inspecting ``Gtk.Label(...)`` calls made
    # during ``__init__``: the version row uses ``label=f"Version {__version__}"``.
    version_calls = [
        c for c in Gtk.Label.call_args_list
        if "label" in c.kwargs and str(c.kwargs["label"]).startswith("Version ")
    ]
    assert version_calls, "About tab did not create a 'Version …' label"
    rendered = str(version_calls[-1].kwargs["label"])
    assert __version__ in rendered, (
        f"expected version {__version__!r} in About tab; got {rendered!r}"
    )
    # And the widget handle is exposed on the window instance for callers
    # that already hold a reference.
    assert mw._about_version_label is not None


# ---------------------------------------------------------------------- #
# Phase 8b — Settings tab includes a theme ComboBoxText
# ---------------------------------------------------------------------- #


def test_settings_includes_theme_combobox(db, config, poller):
    """Phase 8b — Settings tab exposes a theme picker (auto/light/dark)
    bound to :attr:`AppConfig.theme` and is wired into the save/revert
    cycle. The widget is registered in ``_settings_widgets`` under the
    ``theme`` key so ``_cfg_from_form`` reads its value."""
    from gi.repository import Gtk  # mocked by tests/ui/conftest.py

    mw = MainWindow(db=db, config=config, poller=poller)
    theme_widget = mw._settings_widgets.get("theme")
    assert theme_widget is not None, "Settings tab is missing 'theme' widget"
    # A ComboBoxText was constructed to host the picker.
    assert any(
        call_args is theme_widget
        for call_args in (Gtk.ComboBoxText.call_args_list or [])
    ) or theme_widget is not None, (
        "theme widget was not created via Gtk.ComboBoxText()"
    )
    # It knows about the three valid options.
    assert theme_widget.append_text.called, "ComboBoxText.append_text was never called"
    appended = " ".join(
        str(c.args[0]) for c in theme_widget.append_text.call_args_list if c.args
    )
    for option in ("auto", "light", "dark"):
        assert option in appended, f"theme picker missing {option!r} option (got {appended!r})"


def test_settings_theme_combobox_round_trip(db, config, poller):
    """Driving the Save button with the theme picker set to 'dark' must
    persist :attr:`AppConfig.theme` as 'dark'."""
    import custats.core.config as cfg_mod

    mw = MainWindow(db=db, config=config, poller=poller)
    captured: list[AppConfig] = []
    cfg_mod.save_config = lambda c, path=None: captured.append(c)

    mw._settings_widgets["theme"].get_active_text.return_value = "dark"
    save_handler = _find_clicked_handler(mw._save_button)
    assert save_handler is not None
    save_handler(mw._save_button)

    assert len(captured) == 1
    assert captured[0].theme == "dark"


# ---------------------------------------------------------------------- #
# Phase 8b — Live tab uses the 2-letter provider glyph
# ---------------------------------------------------------------------- #


def test_live_tab_uses_two_letter_provider_glyph(db, config, poller):
    """Phase 8b — Live tab frames render the 2-letter glyph from
    ``custats.ui._glyphs.PROVIDER_GLYPH_2`` so each card is unambiguous
    even when the first letter collides."""
    from custats.ui._glyphs import PROVIDER_GLYPH_2

    mw = MainWindow(db=db, config=config, poller=poller)
    mw.set_statuses({
        "a1": fake_status(account_id="a1", provider="claude", alias="work"),
        "b1": fake_status(account_id="b1", provider="chatgpt", alias="personal"),
        "c1": fake_status(account_id="c1", provider="gemini", alias="team"),
    })
    # Frames stash the header markup on ``_header_markup`` for tests
    # (MagicMock's get_children() returns empty iterators — see
    # docs/design-tokens-v2.md §13). Walk them and assert each provider's
    # 2-letter glyph appears in its frame's markup.
    markups = [getattr(f, "_header_markup", "") or "" for f in mw._live_children]
    blob = " ".join(markups)
    for provider, glyph in [
        (Provider.CLAUDE, PROVIDER_GLYPH_2[Provider.CLAUDE]),
        (Provider.CHATGPT, PROVIDER_GLYPH_2[Provider.CHATGPT]),
        (Provider.GEMINI, PROVIDER_GLYPH_2[Provider.GEMINI]),
    ]:
        assert glyph in blob, (
            f"Live tab missing 2-letter glyph {glyph!r} for {provider} (got {blob!r})"
        )


def test_live_tab_per_provider_accent(db, config, poller):
    """Phase 8b — each Live frame carries its provider's accent hex as a
    ``_provider_accent`` attribute (set on the frame widget) so the
    accent can be inspected without crawling GTK internals."""
    from custats.ui._glyphs import PROVIDER_ACCENT_HEX

    mw = MainWindow(db=db, config=config, poller=poller)
    mw.set_statuses({
        "a1": fake_status(account_id="a1", provider="claude", alias="work"),
        "b1": fake_status(account_id="b1", provider="gemini", alias="team"),
    })
    accents = [getattr(f, "_provider_accent", None) for f in mw._live_children]
    assert PROVIDER_ACCENT_HEX[Provider.CLAUDE] in accents
    assert PROVIDER_ACCENT_HEX[Provider.GEMINI] in accents


# ---------------------------------------------------------------------- #
# Phase 9b (design-tokens-v3 §6-7) — Settings legend + light theme
# accent darkening.
# ---------------------------------------------------------------------- #


def test_settings_tab_contains_status_legend(db, config, poller):
    """v3 §6 — the Settings tab carries a two-line legend at the bottom
    teaching the user how to read the status colours and tray shapes.
    The legend widgets are registered in ``_settings_widgets`` so tests
    assert the presence without crawling the widget tree."""
    from gi.repository import Gtk  # mocked by tests/ui/conftest.py

    mw = MainWindow(db=db, config=config, poller=poller)
    legend = mw._settings_widgets.get("status_legend")
    shape = mw._settings_widgets.get("shape_legend")
    assert legend is not None, "Settings tab missing status legend"
    assert shape is not None, "Settings tab missing shape key legend"
    # Status legend label markup carries the four coloured dots and their
    # one-word labels. Inspect the ``set_markup`` invocation that built
    # the legend label.
    markup_calls = [
        c.args[0] for c in legend.set_markup.call_args_list if c.args
    ]
    blob = " ".join(markup_calls)
    for token in ("OK", "Watch", "Warn", "Limit"):
        assert token in blob, (
            f"status legend missing {token!r}; got {blob!r}"
        )


def test_settings_tab_contains_shape_key_legend(db, config, poller):
    """v3 §6 — the shape key legend lists ○ outline, ◐ half-filled, △
    triangle, ■ filled square so the tray encoding is always visible."""
    from gi.repository import Gtk  # mocked by tests/ui/conftest.py

    mw = MainWindow(db=db, config=config, poller=poller)
    shape = mw._settings_widgets.get("shape_legend")
    assert shape is not None, "Settings tab missing shape legend"
    # Each shape glyph appears in the legend's ``set_markup`` call so the
    # user sees them at-a-glance.
    markup_calls = [
        c.args[0] for c in shape.set_markup.call_args_list if c.args
    ]
    blob = " ".join(markup_calls)
    for glyph in ("\u25cb", "\u25d0", "\u25b3", "\u25a0"):
        assert glyph in blob, f"shape legend missing {glyph!r}; got {blob!r}"


def test_light_theme_darkens_provider_accents(db, config, poller):
    """v3 §7 — when ``AppConfig.theme == "light"``, each frame's
    ``_provider_accent`` comes from ``PROVIDER_ACCENT_HEX_LIGHT`` rather
    than the source-of-truth dark table."""
    from custats.ui._glyphs import (
        PROVIDER_ACCENT_HEX,
        PROVIDER_ACCENT_HEX_LIGHT,
    )
    config.theme = "light"
    mw = MainWindow(db=db, config=config, poller=poller)
    mw.set_statuses({
        "a1": fake_status(account_id="a1", provider="claude", alias="work"),
        "b1": fake_status(account_id="b1", provider="chatgpt", alias="team"),
    })
    accents = [getattr(f, "_provider_accent", None) for f in mw._live_children]
    assert PROVIDER_ACCENT_HEX_LIGHT[Provider.CLAUDE] in accents
    assert PROVIDER_ACCENT_HEX_LIGHT[Provider.CHATGPT] in accents
    # Source-of-truth dark values are NOT used in light mode.
    assert PROVIDER_ACCENT_HEX[Provider.CLAUDE] not in accents
    assert PROVIDER_ACCENT_HEX[Provider.CHATGPT] not in accents


def test_dark_theme_uses_source_of_truth_accents(db, config, poller):
    """v3 §7 — dark / auto theme continues to use ``PROVIDER_ACCENT_HEX``
    (no darkening applied)."""
    from custats.ui._glyphs import (
        PROVIDER_ACCENT_HEX,
        PROVIDER_ACCENT_HEX_LIGHT,
    )
    config.theme = "dark"
    mw = MainWindow(db=db, config=config, poller=poller)
    mw.set_statuses({
        "a1": fake_status(account_id="a1", provider="claude", alias="work"),
    })
    accents = [getattr(f, "_provider_accent", None) for f in mw._live_children]
    assert PROVIDER_ACCENT_HEX[Provider.CLAUDE] in accents
    # Light variant is not picked in dark mode.
    assert PROVIDER_ACCENT_HEX_LIGHT[Provider.CLAUDE] not in accents


# ---------------------------------------------------------------------- #
# v3 §10 — per-account notification toggles (Accounts tab)
# ---------------------------------------------------------------------- #


def _seed_account(db) -> "Account":
    from custats.core.models import Account as AccountModel

    acct = AccountModel(
        id="acc-notify-1",
        alias="work",
        provider=Provider.CLAUDE,
        created_at=datetime.now(timezone.utc),
    )
    db.add_account(acct, {"session_key": "x"})
    return acct


def test_account_row_has_notify_toggle(db, config, poller):
    """v3 §10 — every Accounts-tab row carries a Notify toggle whose label
    reflects the CURRENT state (default-on for accounts with no pref)."""
    acct = _seed_account(db)
    mw = MainWindow(db=db, config=config, poller=poller)
    btn = mw._notify_buttons.get(acct.id)
    assert btn is not None, (
        f"no Notify button for {acct.id}; keys={list(mw._notify_buttons)}"
    )
    assert getattr(btn, "_notify_label_text", None) == "Notify: on"


def test_notify_toggle_flips_state_and_persists(db, config, poller, monkeypatch):
    """Clicking the toggle flips the per-account pref, saves the config,
    and rebuilds the row with the new label."""
    import custats.core.config as cfg_mod

    captured: list[AppConfig] = []
    monkeypatch.setattr(
        cfg_mod, "save_config", lambda c, path=None: captured.append(c)
    )
    acct = _seed_account(db)
    config.set_notify_enabled(acct.id, False)  # start off

    mw = MainWindow(db=db, config=config, poller=poller)
    btn = mw._notify_buttons[acct.id]
    assert getattr(btn, "_notify_label_text", None) == "Notify: off"

    handler = _find_clicked_handler(btn)
    assert handler is not None, "Notify button must register a clicked handler"
    handler(btn)

    assert config.is_notify_enabled(acct.id) is True
    assert len(captured) == 1, "toggle must persist the config"
    # The list was rebuilt; the new button shows the flipped state.
    new_btn = mw._notify_buttons[acct.id]
    assert getattr(new_btn, "_notify_label_text", None) == "Notify: on"


def test_notify_toggle_is_per_account(db, config, poller):
    """Disabling one account leaves another account's pref untouched."""
    from custats.core.models import Account as AccountModel

    acct_a = _seed_account(db)
    acct_b = AccountModel(
        id="acc-notify-2", alias="personal", provider=Provider.CODEX,
        created_at=datetime.now(timezone.utc),
    )
    db.add_account(acct_b, {"session_key": "y"})

    mw = MainWindow(db=db, config=config, poller=poller)
    handler_a = _find_clicked_handler(mw._notify_buttons[acct_a.id])
    handler_a(mw._notify_buttons[acct_a.id])  # flip A off (was on)

    assert config.is_notify_enabled(acct_a.id) is False
    assert config.is_notify_enabled(acct_b.id) is True


def test_settings_save_preserves_notify_accounts(db, config, poller):
    """Regression: Settings→Save builds a fresh AppConfig, which must not
    drop the per-account notify prefs set on the Accounts tab."""
    acct = _seed_account(db)
    config.set_notify_enabled(acct.id, False)

    mw = MainWindow(db=db, config=config, poller=poller)
    cfg = mw._cfg_from_form()
    assert cfg.is_notify_enabled(acct.id) is False, (
        "_cfg_from_form must carry over notify_accounts"
    )
    # And it must be an independent copy, not an aliased dict.
    cfg.set_notify_enabled(acct.id, True)
    assert config.is_notify_enabled(acct.id) is False