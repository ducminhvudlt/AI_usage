"""Tests for custats.core.config."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from custats.core.config import (
    AppConfig,
    config_dir,
    config_file,
    db_file,
    load_config,
    save_config,
    secret_key_file,
    state_dir,
)
from custats.core.models import Provider


@pytest.fixture
def isolated_xdg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point XDG_CONFIG_HOME / XDG_DATA_HOME at a temp dir for the test."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    return tmp_path


class TestConfigDirs:
    def test_config_dir_creates(self, isolated_xdg: Path) -> None:
        path = config_dir()
        assert path.is_dir()
        assert path == isolated_xdg / "config" / "custats"

    def test_state_dir_creates(self, isolated_xdg: Path) -> None:
        path = state_dir()
        assert path.is_dir()
        assert path == isolated_xdg / "data" / "custats"

    def test_secret_key_file(self, isolated_xdg: Path) -> None:
        assert secret_key_file() == isolated_xdg / "config" / "custats" / "secret.key"

    def test_db_file(self, isolated_xdg: Path) -> None:
        assert db_file() == isolated_xdg / "data" / "custats" / "state.db"

    def test_config_file(self, isolated_xdg: Path) -> None:
        assert config_file() == isolated_xdg / "config" / "custats" / "config.toml"

    def test_xdg_relative_resolves_against_home(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        monkeypatch.setenv("HOME", "/tmp/fake-home")
        # Relative default '~/.config' must resolve under $HOME.
        assert config_dir() == Path("/tmp/fake-home/.config/custats")


class TestAppConfig:
    def test_defaults(self) -> None:
        cfg = AppConfig()
        assert cfg.refresh_interval_seconds == 60
        assert cfg.notify_threshold_percent == 80
        assert cfg.notify_on_recovery is True
        assert cfg.pace_enabled is True
        assert cfg.theme == "auto"
        # Every provider should be visible by default.
        for provider in Provider:
            assert cfg.is_provider_visible(provider) is True

    def test_provider_visibility_helpers(self) -> None:
        cfg = AppConfig()
        cfg.set_provider_visible(Provider.CLAUDE, False)
        assert cfg.is_provider_visible(Provider.CLAUDE) is False
        assert cfg.is_provider_visible(Provider.GROK) is True
        # Unknown provider defaults to True.
        assert cfg.is_provider_visible(Provider.CURSOR) is True

    def test_round_trip_mapping(self) -> None:
        cfg = AppConfig(
            refresh_interval_seconds=120,
            notify_threshold_percent=75,
            notify_on_recovery=False,
            pace_enabled=False,
            theme="dark",
        )
        cfg.set_provider_visible(Provider.GROK, False)
        data = cfg.to_mapping()
        assert data["refresh_interval_seconds"] == 120
        assert data["theme"] == "dark"
        assert data["show_in_menu_bar"]["grok"] is False
        # Round-trip.
        rebuilt = AppConfig.from_mapping(data)
        assert rebuilt.refresh_interval_seconds == 120
        assert rebuilt.theme == "dark"
        assert rebuilt.is_provider_visible(Provider.GROK) is False
        # Other providers stay True.
        assert rebuilt.is_provider_visible(Provider.CLAUDE) is True

    def test_from_mapping_unknown_provider_ignored(self) -> None:
        cfg = AppConfig.from_mapping(
            {
                "show_in_menu_bar": {
                    "claude": False,
                    "gibberish-provider": False,  # unknown — ignored
                }
            }
        )
        assert cfg.is_provider_visible(Provider.CLAUDE) is False
        assert cfg.is_provider_visible(Provider.GROK) is True


class TestLoadSave:
    def test_missing_file_returns_defaults(
        self, isolated_xdg: Path
    ) -> None:
        cfg = load_config()
        assert cfg.refresh_interval_seconds == 60
        assert cfg.theme == "auto"

    def test_save_and_load_round_trip(self, isolated_xdg: Path) -> None:
        cfg = AppConfig(refresh_interval_seconds=30, theme="dark")
        cfg.set_provider_visible(Provider.CURSOR, False)
        save_config(cfg)
        assert config_file().is_file()

        loaded = load_config()
        assert loaded.refresh_interval_seconds == 30
        assert loaded.theme == "dark"
        assert loaded.is_provider_visible(Provider.CURSOR) is False

    def test_save_explicit_path(self, isolated_xdg: Path, tmp_path: Path) -> None:
        target = tmp_path / "custom.toml"
        cfg = AppConfig(theme="light")
        save_config(cfg, path=target)
        assert target.is_file()
        # The custom path is used for read-back too.
        loaded = load_config(path=target)
        assert loaded.theme == "light"

    def test_overwrite_existing_file(self, isolated_xdg: Path) -> None:
        save_config(AppConfig(theme="dark"))
        save_config(AppConfig(theme="light"))
        assert load_config().theme == "light"


class TestTomlEscape:
    def test_control_chars_round_trip(self) -> None:
        """Round-trip strings containing \\n, \\t, and other C0 control chars."""
        import tomllib

        from custats.core.config import _dump_value

        # \\t, \\n, \\r, \\b, \\f, plus U+0001 (escaped as \\u0001) and DEL U+007F.
        tricky = "hi\nthere\twith\rbells\band\fform\x01\x7F"
        rendered = _dump_value(tricky)
        parsed = tomllib.loads(f"k = {rendered}")
        assert parsed["k"] == tricky


# ---------------------------------------------------------------------- #
# Phase 8b — Theme field
# ---------------------------------------------------------------------- #


class TestAppConfigTheme:
    """Phase 8b — Settings tab added a ``theme`` field with values
    ``auto`` / ``light`` / ``dark``. Default is ``auto``; must round-trip
    through ``to_mapping`` / ``from_mapping`` unchanged."""

    def test_app_config_theme_default_is_auto(self) -> None:
        cfg = AppConfig()
        assert cfg.theme == "auto"
        # Also covered by ``TestAppConfig::test_defaults``; pinned here so
        # the Phase 8b deliverable has its own regression test.

    def test_app_config_theme_round_trip(self) -> None:
        for value in ("auto", "light", "dark"):
            cfg = AppConfig(theme=value)
            data = cfg.to_mapping()
            assert data["theme"] == value
            rebuilt = AppConfig.from_mapping(data)
            assert rebuilt.theme == value

    def test_app_config_theme_missing_in_mapping_defaults_to_auto(self) -> None:
        """Older config files written before Phase 8b have no ``theme``
        key. Loading them must default to ``auto`` (backward compat)."""
        cfg = AppConfig.from_mapping({"refresh_interval_seconds": 30})
        assert cfg.theme == "auto"
