"""Phase 6 tests for the installer / packaging artefacts.

Covers:

* ``packaging/custats.service`` — well-formed systemd unit.
* ``packaging/custats.desktop`` — well-formed XDG autostart entry.
* ``install.sh`` — bash syntax + a few sanity checks.
* ``custats.cli.cmd_install_service`` / ``cmd_uninstall_service`` — actually
  copy / remove the service file under a temp ``XDG_CONFIG_HOME``.
"""

from __future__ import annotations

import argparse
import configparser
import os
import subprocess
import sys
from pathlib import Path

import pytest

from custats.cli import cmd_install_service, cmd_uninstall_service

REPO_ROOT = Path(__file__).resolve().parent.parent
SERVICE_FILE = REPO_ROOT / "packaging" / "custats.service"
DESKTOP_FILE = REPO_ROOT / "packaging" / "custats.desktop"
INSTALL_SH = REPO_ROOT / "install.sh"
PKG_DIR = REPO_ROOT / "src" / "custats" / "packaging"


# ---------------------------------------------------------------------- #
# packaging/ contents
# ---------------------------------------------------------------------- #


class TestPackagingFilesExist:
    def test_service_file_exists(self) -> None:
        assert SERVICE_FILE.is_file(), f"missing: {SERVICE_FILE}"
        assert SERVICE_FILE.stat().st_size > 0, "service file is empty"

    def test_desktop_file_exists(self) -> None:
        assert DESKTOP_FILE.is_file(), f"missing: {DESKTOP_FILE}"
        assert DESKTOP_FILE.stat().st_size > 0, "desktop file is empty"

    def test_install_sh_exists(self) -> None:
        assert INSTALL_SH.is_file(), f"missing: {INSTALL_SH}"
        assert INSTALL_SH.stat().st_size > 0, "install.sh is empty"


# ---------------------------------------------------------------------- #
# systemd unit
# ---------------------------------------------------------------------- #


class TestServiceFile:
    def test_is_valid_ini(self) -> None:
        """``configparser`` is happy with systemd's INI dialect."""
        cfg = configparser.RawConfigParser(strict=False)
        read = cfg.read(SERVICE_FILE, encoding="utf-8")
        assert SERVICE_FILE in {Path(p) for p in read} or read, (
            f"failed to parse {SERVICE_FILE}"
        )

    def test_has_required_sections(self) -> None:
        cfg = configparser.RawConfigParser(strict=False)
        cfg.read(SERVICE_FILE, encoding="utf-8")
        for section in ("Unit", "Service", "Install"):
            assert cfg.has_section(section), f"missing [{section}] section"
        # ``configparser`` lower-cases keys — compare lowercased.
        assert "description" in cfg.options("Unit")
        assert "execstart" in cfg.options("Service")
        assert "wantedby" in cfg.options("Install")

    def test_exec_start_non_empty(self) -> None:
        cfg = configparser.RawConfigParser(strict=False)
        cfg.read(SERVICE_FILE, encoding="utf-8")
        exec_start = cfg.get("Service", "ExecStart").strip()
        assert exec_start, "ExecStart is empty"
        assert "custats" in exec_start
        assert "run" in exec_start

    def test_no_hardcoded_username(self) -> None:
        """``%h`` (systemd's home-dir specifier) must be used, not ``/home/...``."""
        body = SERVICE_FILE.read_text(encoding="utf-8")
        assert "%h" in body, "service file should use %h (systemd home specifier)"
        bad = [
            line for line in body.splitlines()
            if "/home/" in line and not line.lstrip().startswith("#")
        ]
        assert not bad, f"service file has hardcoded /home paths: {bad}"

    def test_restart_on_failure(self) -> None:
        cfg = configparser.RawConfigParser(strict=False)
        cfg.read(SERVICE_FILE, encoding="utf-8")
        assert cfg.get("Service", "Restart").strip() == "on-failure"


# ---------------------------------------------------------------------- #
# XDG .desktop
# ---------------------------------------------------------------------- #


class TestDesktopFile:
    def test_is_valid_ini(self) -> None:
        cfg = configparser.RawConfigParser(strict=False)
        read = cfg.read(DESKTOP_FILE, encoding="utf-8")
        assert read, f"failed to parse {DESKTOP_FILE}"

    def test_required_keys(self) -> None:
        cfg = configparser.RawConfigParser(strict=False)
        cfg.read(DESKTOP_FILE, encoding="utf-8")
        assert cfg.get("Desktop Entry", "Type") == "Application"
        # Use an absolute path so the tray icon starts even when `~/.local/bin`
        # isn't on the desktop session's PATH. XDG spec supports `%h` here.
        exec_value = cfg.get("Desktop Entry", "Exec").strip()
        assert exec_value == "%h/.local/bin/custats run", exec_value
        assert cfg.get("Desktop Entry", "Name").strip() == "custats"


# ---------------------------------------------------------------------- #
# install.sh
# ---------------------------------------------------------------------- #


class TestInstallSh:
    def test_executable(self) -> None:
        import stat

        mode = INSTALL_SH.stat().st_mode
        assert mode & stat.S_IXUSR, "install.sh is not user-executable"

    def test_shebang(self) -> None:
        first_line = INSTALL_SH.read_text(encoding="utf-8").splitlines()[0]
        assert first_line == "#!/usr/bin/env bash", (
            f"unexpected shebang: {first_line!r}"
        )

    def test_uses_strict_mode(self) -> None:
        body = INSTALL_SH.read_text(encoding="utf-8")
        assert "set -euo pipefail" in body

    def test_syntax_check(self) -> None:
        """``bash -n`` parses without executing — catches syntax errors."""
        result = subprocess.run(
            ["bash", "-n", str(INSTALL_SH)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, (
            f"bash -n failed: stderr={result.stderr!r}"
        )


# ---------------------------------------------------------------------- #
# CLI integration
# ---------------------------------------------------------------------- #


class TestCliInstallService:
    def test_copies_service_file_into_xdg(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        # ``systemctl`` is unlikely to be available in CI; the CLI is
        # best-effort and must still copy the file.
        args = argparse.Namespace()
        result = cmd_install_service(args)
        # We don't assert on the exit code here: it depends on whether
        # systemctl is present. The file-copy step is what we actually
        # care about for packaging.
        target = tmp_path / "systemd" / "user" / "custats.service"
        assert target.is_file(), f"service file not created at {target}"
        assert target.read_text(encoding="utf-8").startswith("[Unit]")

    def test_uninstall_removes_service_file(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        # Seed the file so uninstall has something to delete.
        target = tmp_path / "systemd" / "user" / "custats.service"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("[Unit]\nDescription=test\n", encoding="utf-8")
        assert target.is_file()

        result = cmd_uninstall_service(argparse.Namespace())
        assert result == 0
        assert not target.exists(), f"{target} should be gone"

    def test_install_service_uses_packaging_dir(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Even when invoked from anywhere, the copy lands in the right place."""
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        cmd_install_service(argparse.Namespace())
        target = tmp_path / "systemd" / "user" / "custats.service"
        assert target.is_file()


# ---------------------------------------------------------------------- #
# smoke
# ---------------------------------------------------------------------- #


def test_cli_imports_cleanly() -> None:
    """Sanity: the new helpers don't blow up on import."""
    from custats.cli import _service_file_path, cmd_install_service  # noqa: F401

    p = _service_file_path()
    assert p.is_file()


class TestPackagingCopiesAreIdentical:
    """The top-level `packaging/` files must stay byte-identical to the
    package-data copies under `src/custats/packaging/`. install.sh reads the
    top-level copies; cmd_install_service reads the in-package copies. Drift
    between them would ship two different service files depending on the
    install path."""

    def test_custats_service_copies_match(self) -> None:
        a = SERVICE_FILE.read_bytes()
        b = (PKG_DIR / "custats.service").read_bytes()
        assert a == b, (
            f"custats.service has drifted between {SERVICE_FILE} and "
            f"{PKG_DIR / 'custats.service'} — keep them byte-identical."
        )

    def test_custats_desktop_copies_match(self) -> None:
        a = DESKTOP_FILE.read_bytes()
        b = (PKG_DIR / "custats.desktop").read_bytes()
        assert a == b, (
            f"custats.desktop has drifted between {DESKTOP_FILE} and "
            f"{PKG_DIR / 'custats.desktop'} — keep them byte-identical."
        )
