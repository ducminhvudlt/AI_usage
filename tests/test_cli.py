"""Tests for the CLI surface area.

These tests run the package as ``python -m custats ...`` in a
subprocess with overridden ``XDG_CONFIG_HOME`` /
``XDG_DATA_HOME`` so they don't touch the user's real config.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src"


def _run_cli(
    *args: str,
    xdg: Path,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Invoke ``python -m custats ...`` with isolated XDG dirs."""
    env = os.environ.copy()
    env["XDG_CONFIG_HOME"] = str(xdg / "config")
    env["XDG_DATA_HOME"] = str(xdg / "data")
    env["PYTHONPATH"] = str(SRC) + os.pathsep + env.get("PYTHONPATH", "")
    # Disable color / progress from any future deps.
    env["NO_COLOR"] = "1"
    return subprocess.run(
        [sys.executable, "-m", "custats", *args],
        capture_output=True,
        text=True,
        env=env,
        input=input_text,
        timeout=30,
        check=False,
    )


@pytest.fixture
def isolated_xdg(tmp_path: Path) -> Path:
    return tmp_path


class TestHelp:
    def test_help_exits_zero(self, isolated_xdg: Path) -> None:
        result = _run_cli("--help", xdg=isolated_xdg)
        assert result.returncode == 0
        assert "usage" in result.stdout.lower() or "custats" in result.stdout

    def test_version_exits_zero(self, isolated_xdg: Path) -> None:
        result = _run_cli("--version", xdg=isolated_xdg)
        assert result.returncode == 0
        assert "custats" in result.stdout
        assert "0.1.0" in result.stdout

    def test_no_args_shows_help(self, isolated_xdg: Path) -> None:
        result = _run_cli(xdg=isolated_xdg)
        assert result.returncode == 0
        assert "usage" in result.stdout.lower()


class TestListEmpty:
    def test_list_empty_db(self, isolated_xdg: Path) -> None:
        result = _run_cli("list", xdg=isolated_xdg)
        assert result.returncode == 0
        # Header is printed even when there are no rows.
        assert "ALIAS" in result.stdout
        assert "PROVIDER" in result.stdout


class TestDoctor:
    def test_doctor_runs(self, isolated_xdg: Path) -> None:
        result = _run_cli("doctor", xdg=isolated_xdg)
        # doctor exits 0 if all checks pass; non-zero only on hard failures.
        # On a CI box without GTK we don't fail — we just note it.
        assert "custats doctor" in result.stdout
        assert "config dir writable" in result.stdout

    def test_doctor_json(self, isolated_xdg: Path) -> None:
        result = _run_cli("doctor", "--json", xdg=isolated_xdg)
        assert result.returncode == 0
        assert '"ok"' in result.stdout
        assert '"version"' in result.stdout


class TestAddRemove:
    def test_add_with_session_key_then_list(
        self, isolated_xdg: Path
    ) -> None:
        add = _run_cli(
            "add",
            "--provider",
            "claude",
            "--alias",
            "work",
            "--session-key",
            "sk-test-123",
            xdg=isolated_xdg,
        )
        assert add.returncode == 0, add.stderr
        assert "added" in add.stdout.lower()
        assert "claude" in add.stdout

        listing = _run_cli("list", xdg=isolated_xdg)
        assert listing.returncode == 0
        assert "work" in listing.stdout
        assert "claude" in listing.stdout

    def test_add_with_invalid_provider(self, isolated_xdg: Path) -> None:
        result = _run_cli(
            "add",
            "--provider",
            "bogus",
            "--alias",
            "x",
            "--session-key",
            "y",
            xdg=isolated_xdg,
        )
        assert result.returncode != 0
        assert "unknown provider" in result.stderr.lower()

    def test_remove_unknown_account(self, isolated_xdg: Path) -> None:
        result = _run_cli(
            "remove", "--account-id", "does-not-exist", xdg=isolated_xdg
        )
        assert result.returncode != 0
        assert "no account" in result.stderr.lower()

    def test_remove_added_account(self, isolated_xdg: Path) -> None:
        add = _run_cli(
            "add",
            "--provider",
            "cursor",
            "--alias",
            "throwaway",
            "--session-key",
            "abc",
            xdg=isolated_xdg,
        )
        assert add.returncode == 0
        # Extract the new account id from stdout ("as <id>").
        new_id = add.stdout.rsplit("as ", 1)[-1].strip()
        rm = _run_cli("remove", "--account-id", new_id, xdg=isolated_xdg)
        assert rm.returncode == 0, rm.stderr
        assert "removed" in rm.stdout.lower()

    def test_add_chatgpt_with_cookie_then_list(
        self, isolated_xdg: Path
    ) -> None:
        """Phase 7b: ``custats add --provider chatgpt --alias ... --cookie ...``
        stores the account, ``list`` shows it, and an unknown provider still
        errors with the standard ``unknown provider`` text (regression check)."""
        add = _run_cli(
            "add",
            "--provider",
            "chatgpt",
            "--alias",
            "mychat",
            "--cookie",
            "__Secure-next-auth.session-token=fake",
            xdg=isolated_xdg,
        )
        assert add.returncode == 0, add.stderr
        assert "added" in add.stdout.lower()
        assert "chatgpt" in add.stdout

        listing = _run_cli("list", xdg=isolated_xdg)
        assert listing.returncode == 0
        assert "mychat" in listing.stdout
        assert "chatgpt" in listing.stdout

    # --- Phase 8a-batch1: API-key providers (Gemini / OpenRouter / DeepSeek)

    def test_add_gemini_with_api_key_then_list(
        self, isolated_xdg: Path
    ) -> None:
        """``custats add --provider gemini --alias g --api-key …`` stores the
        account and ``list`` shows it. Regression for Phase 8a-batch1."""
        add = _run_cli(
            "add",
            "--provider",
            "gemini",
            "--alias",
            "mygem",
            "--api-key",
            "AIzaSy-fake-key",
            xdg=isolated_xdg,
        )
        assert add.returncode == 0, add.stderr
        assert "added" in add.stdout.lower()
        assert "gemini" in add.stdout

        listing = _run_cli("list", xdg=isolated_xdg)
        assert listing.returncode == 0
        assert "mygem" in listing.stdout
        assert "gemini" in listing.stdout

    def test_add_openrouter_with_api_key_then_list(
        self, isolated_xdg: Path
    ) -> None:
        """``custats add --provider openrouter --alias o --api-key …`` stores
        the account and ``list`` shows it. Regression for Phase 8a-batch1."""
        add = _run_cli(
            "add",
            "--provider",
            "openrouter",
            "--alias",
            "myor",
            "--api-key",
            "sk-or-v1-fake",
            xdg=isolated_xdg,
        )
        assert add.returncode == 0, add.stderr
        assert "added" in add.stdout.lower()
        assert "openrouter" in add.stdout

        listing = _run_cli("list", xdg=isolated_xdg)
        assert listing.returncode == 0
        assert "myor" in listing.stdout
        assert "openrouter" in listing.stdout

    def test_add_deepseek_with_api_key_then_list(
        self, isolated_xdg: Path
    ) -> None:
        """``custats add --provider deepseek --alias d --api-key …`` stores the
        account and ``list`` shows it. Regression for Phase 8a-batch1."""
        add = _run_cli(
            "add",
            "--provider",
            "deepseek",
            "--alias",
            "myds",
            "--api-key",
            "sk-ds-fake",
            xdg=isolated_xdg,
        )
        assert add.returncode == 0, add.stderr
        assert "added" in add.stdout.lower()
        assert "deepseek" in add.stdout

        listing = _run_cli("list", xdg=isolated_xdg)
        assert listing.returncode == 0
        assert "myds" in listing.stdout
        assert "deepseek" in listing.stdout

    # --- Phase 8a-batch2: API-key providers (Mistral + Kimi)

    def test_add_mistral_with_api_key_then_list(
        self, isolated_xdg: Path
    ) -> None:
        """``custats add --provider mistral --alias m --api-key …`` stores the
        account and ``list`` shows it. Regression for Phase 8a-batch2."""
        add = _run_cli(
            "add",
            "--provider",
            "mistral",
            "--alias",
            "mymis",
            "--api-key",
            "sk-mistral-fake",
            xdg=isolated_xdg,
        )
        assert add.returncode == 0, add.stderr
        assert "added" in add.stdout.lower()
        assert "mistral" in add.stdout

        listing = _run_cli("list", xdg=isolated_xdg)
        assert listing.returncode == 0
        assert "mymis" in listing.stdout
        assert "mistral" in listing.stdout

    def test_add_kimi_with_api_key_then_list(
        self, isolated_xdg: Path
    ) -> None:
        """``custats add --provider kimi --alias k --api-key …`` stores the
        account and ``list`` shows it. Regression for Phase 8a-batch2."""
        add = _run_cli(
            "add",
            "--provider",
            "kimi",
            "--alias",
            "mykimi",
            "--api-key",
            "sk-kimi-fake",
            xdg=isolated_xdg,
        )
        assert add.returncode == 0, add.stderr
        assert "added" in add.stdout.lower()
        assert "kimi" in add.stdout

        listing = _run_cli("list", xdg=isolated_xdg)
        assert listing.returncode == 0
        assert "mykimi" in listing.stdout
        assert "kimi" in listing.stdout


class TestOnboard:
    """Phase 9a: ``custats onboard`` first-run setup wizard."""

    def test_onboard_help_exits_0(self, isolated_xdg: Path) -> None:
        result = _run_cli("onboard", "--help", xdg=isolated_xdg)
        assert result.returncode == 0
        assert "onboard" in result.stdout.lower()
        # argparse's top-level usage line includes the subcommand name.
        assert "usage" in result.stdout.lower()

    def test_onboard_runs_and_exits_0_in_subprocess(
        self, isolated_xdg: Path
    ) -> None:
        """End-to-end via ``python -m custats onboard`` with closed stdin
        (non-interactive). The wizard prints the 'skipped' line and
        exits 0 — no prompts, no crashes.
        """
        env = os.environ.copy()
        env["XDG_CONFIG_HOME"] = str(isolated_xdg / "config")
        env["XDG_DATA_HOME"] = str(isolated_xdg / "data")
        env["PYTHONPATH"] = str(SRC) + os.pathsep + env.get("PYTHONPATH", "")
        env["NO_COLOR"] = "1"

        result = subprocess.run(
            [sys.executable, "-m", "custats", "onboard"],
            capture_output=True,
            text=True,
            env=env,
            stdin=subprocess.DEVNULL,
            timeout=30,
            check=False,
        )

        assert result.returncode == 0, (
            f"onboard failed (rc={result.returncode})\n"
            f"stdout={result.stdout}\nstderr={result.stderr}"
        )
        # The non-TTY summary line OR the interactive 'Done' line must appear.
        combined = result.stdout + result.stderr
        assert "skipped" in combined.lower() or "Done" in combined


class TestServiceCommands:
    """Phase 6: ``install-service`` / ``uninstall-service`` are now real."""

    def test_install_service_copies_unit_file(
        self, isolated_xdg: Path
    ) -> None:
        result = _run_cli("install-service", xdg=isolated_xdg)
        # Exit code may be 0 (systemctl present) or 1 (no systemctl in CI) —
        # either way the file copy must have happened.
        target = isolated_xdg / "config" / "systemd" / "user" / "custats.service"
        assert target.is_file(), (
            f"service file not created at {target}\nstdout={result.stdout}\n"
            f"stderr={result.stderr}"
        )
        assert "[Unit]" in target.read_text(encoding="utf-8")

    def test_uninstall_service_removes_unit_file(
        self, isolated_xdg: Path
    ) -> None:
        # Seed a service file so uninstall has something to delete.
        target = isolated_xdg / "config" / "systemd" / "user" / "custats.service"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("[Unit]\nDescription=seed\n", encoding="utf-8")
        result = _run_cli("uninstall-service", xdg=isolated_xdg)
        assert result.returncode == 0, result.stderr
        assert not target.exists(), f"{target} should have been removed"


class TestRun:
    def test_run_without_accounts_fails(
        self, isolated_xdg: Path
    ) -> None:
        result = _run_cli("run", xdg=isolated_xdg, input_text="\n")
        assert result.returncode == 1
        assert "no accounts" in result.stderr.lower()

    def test_run_with_account_does_not_close_db_early(
        self, isolated_xdg: Path
    ) -> None:
        """Regression for R1: ``custats run`` must keep the DB open while the
        poller is polling. The previous bug closed the SQLite connection
        before :class:`Poller` started — causing
        ``sqlite3.ProgrammingError: Cannot operate on a closed database``
        to be raised inside :func:`_poll_target` and dumped to stderr.

        We add an account, start ``custats run``, let it poll briefly,
        then send SIGTERM. The expected outcome is a clean exit (0 or a
        signal-terminated code) and *no* traceback / closed-DB errors.
        """
        add = _run_cli(
            "add",
            "--provider",
            "claude",
            "--alias",
            "x",
            "--session-key",
            "y",
            xdg=isolated_xdg,
        )
        assert add.returncode == 0, add.stderr

        env = os.environ.copy()
        env["XDG_CONFIG_HOME"] = str(isolated_xdg / "config")
        env["XDG_DATA_HOME"] = str(isolated_xdg / "data")
        env["PYTHONPATH"] = str(SRC) + os.pathsep + env.get("PYTHONPATH", "")
        env["NO_COLOR"] = "1"

        proc = subprocess.Popen(
            [sys.executable, "-m", "custats", "run"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            text=True,
        )
        try:
            # Give the poller time to perform at least one ``db.record_usage``.
            time.sleep(3.0)
            assert proc.poll() is None, (
                f"custats run exited early with code {proc.returncode}"
            )
            proc.send_signal(signal.SIGTERM)
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        finally:
            stdout = proc.stdout.read() if proc.stdout else ""
            stderr = proc.stderr.read() if proc.stderr else ""

        # Signal-terminated exit codes are negative on POSIX.
        assert proc.returncode in (0, -signal.SIGTERM), (
            f"unexpected exit code {proc.returncode}; "
            f"stderr=\n{stderr}\nstdout=\n{stdout}"
        )
        # The whole point of R1: no "closed database" errors anywhere.
        assert "sqlite3.ProgrammingError" not in stderr
        assert "Cannot operate on a closed database" not in stderr
        # No Python tracebacks leaked to stderr — the run loop must
        # surface errors through ``LiveStatus.error`` instead.
        assert "Traceback" not in stderr


class TestShowConfig:
    def test_show_config_prints(self, isolated_xdg: Path) -> None:
        result = _run_cli("show-config", xdg=isolated_xdg)
        assert result.returncode == 0
        assert "refresh_interval_seconds" in result.stdout
        assert "config_file" in result.stdout


class TestLogin:
    """Phase 7a: ``custats login --provider codex`` runs the browser flow."""

    def test_login_other_provider_not_yet_supported(
        self, isolated_xdg: Path
    ) -> None:
        result = _run_cli(
            "login",
            "--provider",
            "claude",
            "--alias",
            "x",
            xdg=isolated_xdg,
        )
        assert result.returncode != 0
        assert "not yet supported" in result.stderr.lower()
        assert "claude" in result.stderr

    def test_login_unknown_provider_via_argparse(
        self, isolated_xdg: Path
    ) -> None:
        """``--provider bogus`` should error with the standard ``unknown`` text."""
        result = _run_cli(
            "login", "--provider", "bogus", "--alias", "x", xdg=isolated_xdg
        )
        assert result.returncode != 0
        assert "unknown provider" in result.stderr.lower()

    def test_login_user_cancels_with_ctrl_c(
        self,
        isolated_xdg: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """When the flow's on_poll raises DeviceCodeCancelled, the CLI exits 1."""
        from argparse import Namespace

        from custats.cli import cmd_login
        from custats.core.models import Provider
        from custats.oauth import providers as providers_module
        from custats.oauth.device_code import DeviceCodeCancelled

        # Redirect ``~`` so the (theoretical) write lands in tmp_path.
        monkeypatch.setenv("HOME", str(tmp_path))

        async def fake_begin(client, *, on_poll=None):  # noqa: D401
            if on_poll is not None:
                on_poll({"status": "authorization_pending"})
            raise DeviceCodeCancelled("cancelled by test")

        monkeypatch.setitem(
            providers_module.SUPPORTED, Provider.CODEX, fake_begin
        )

        # Sandbox HOME and XDG so the test doesn't touch the real user.
        monkeypatch.setenv("XDG_CONFIG_HOME", str(isolated_xdg / "config"))
        monkeypatch.setenv("XDG_DATA_HOME", str(isolated_xdg / "data"))

        args = Namespace(
            provider=Provider.CODEX,
            alias="throwaway",
            config_file=None,
            db_file=None,
            key_file=None,
        )
        rc = cmd_login(args)
        captured = capsys.readouterr()

        assert rc == 1
        assert "cancelled" in captured.err.lower()

    def test_login_codex_happy_path(
        self,
        isolated_xdg: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Mocked flow: auth.json is written AND the account is added to the DB."""
        from argparse import Namespace

        from custats.cli import cmd_login
        from custats.core.models import Provider
        from custats.oauth import providers as providers_module
        from custats.oauth.device_code import (
            DeviceCodeApproved,
            DeviceCodeRequest,
        )

        # Sandbox HOME + XDG so the test doesn't touch the real user.
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("XDG_CONFIG_HOME", str(isolated_xdg / "config"))
        monkeypatch.setenv("XDG_DATA_HOME", str(isolated_xdg / "data"))

        fake_initial = DeviceCodeRequest(
            device_id="dev-123",
            user_code="ABCD-EFGH",
            verification_url="https://auth.openai.com/codex/device",
            interval_seconds=0,
            expires_in_seconds=600,
        )
        fake_final_raw = {
            "status": "ok",
            "authorization_code": "ac-1",
            "code_verifier": "cv-1",
            "access_token": "oa-1",
            "refresh_token": "or-1",
        }

        async def fake_begin(client, *, on_poll=None):  # noqa: D401
            return fake_initial, DeviceCodeApproved(raw=fake_final_raw)

        monkeypatch.setitem(
            providers_module.SUPPORTED, Provider.CODEX, fake_begin
        )

        # Build a Namespace that matches what ``build_parser`` produces
        # for ``login --provider codex --alias work``.
        args = Namespace(
            provider=Provider.CODEX,
            alias="work",
            config_file=None,
            db_file=None,
            key_file=None,
        )
        rc = cmd_login(args)
        captured = capsys.readouterr()

        assert rc == 0, captured.err
        assert "added" in captured.out.lower()
        assert "work" in captured.out

        # Auth.json was written to the sandboxed HOME.
        auth_json = tmp_path / ".codex" / "auth.json"
        assert auth_json.is_file(), f"{auth_json} was not written"
        on_disk = json.loads(auth_json.read_text(encoding="utf-8"))
        assert on_disk["access_token"] == "oa-1"
        assert on_disk["authorization_code"] == "ac-1"

        # The account row should be in the DB — verify by opening it
        # via the same XDG paths the CLI used.
        from custats.cli import _open_db

        key_path = isolated_xdg / "config" / "custats" / "secret.key"
        db_path = isolated_xdg / "data" / "custats" / "state.db"
        with _open_db(Namespace(key_file=key_path, db_file=db_path)) as db:
            rows = db.list_accounts(include_inactive=False)

        assert len(rows) == 1
        account, creds = rows[0]
        assert account.alias == "work"
        assert account.provider is Provider.CODEX
        assert creds["auth_json_path"] == str(auth_json)

    def test_login_chatgpt_happy_path(
        self,
        isolated_xdg: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Phase 7d: ``custats login --provider chatgpt --alias mychat``
        mirrors the Codex happy path: ``~/.chatgpt/auth.json`` is written
        with the approval dict, the account row is in the encrypted DB,
        and the credential blob uses ``chatgpt_auth_json`` (matching the
        ``ChatGPTAdapter``)."""
        from argparse import Namespace

        from custats.cli import _open_db, cmd_login
        from custats.core.models import Provider
        from custats.oauth import providers as providers_module
        from custats.oauth.device_code import (
            DeviceCodeApproved,
            DeviceCodeRequest,
        )

        # Sandbox HOME + XDG so the test doesn't touch the real user.
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("XDG_CONFIG_HOME", str(isolated_xdg / "config"))
        monkeypatch.setenv("XDG_DATA_HOME", str(isolated_xdg / "data"))

        fake_initial = DeviceCodeRequest(
            device_id="dev-chatgpt-123",
            user_code="WXYZ-1234",
            verification_url="https://auth.openai.com/codex/device",
            interval_seconds=0,
            expires_in_seconds=600,
        )
        fake_final_raw = {
            "status": "ok",
            "authorization_code": "ac-chatgpt-1",
            "code_verifier": "cv-chatgpt-1",
            "access_token": "oa-chatgpt",
            "refresh_token": "or-chatgpt",
        }

        async def fake_begin(client, *, on_poll=None):  # noqa: D401
            return fake_initial, DeviceCodeApproved(raw=fake_final_raw)

        monkeypatch.setitem(
            providers_module.SUPPORTED, Provider.CHATGPT, fake_begin
        )

        args = Namespace(
            provider=Provider.CHATGPT,
            alias="mychat",
            config_file=None,
            db_file=None,
            key_file=None,
        )
        rc = cmd_login(args)
        captured = capsys.readouterr()

        assert rc == 0, captured.err
        assert "added" in captured.out.lower()
        assert "mychat" in captured.out
        assert "chatgpt" in captured.out

        # Auth.json was written to the sandboxed HOME at ~/.chatgpt/.
        auth_json = tmp_path / ".chatgpt" / "auth.json"
        assert auth_json.is_file(), f"{auth_json} was not written"
        on_disk = json.loads(auth_json.read_text(encoding="utf-8"))
        assert on_disk["access_token"] == "oa-chatgpt"
        assert on_disk["authorization_code"] == "ac-chatgpt-1"

        # The account row should be in the DB; the credential blob
        # references the written auth.json path under the
        # ``chatgpt_auth_json`` key (matching ``ChatGPTAdapter``).
        key_path = isolated_xdg / "config" / "custats" / "secret.key"
        db_path = isolated_xdg / "data" / "custats" / "state.db"
        with _open_db(Namespace(key_file=key_path, db_file=db_path)) as db:
            rows = db.list_accounts(include_inactive=False)

        assert len(rows) == 1
        account, creds = rows[0]
        assert account.alias == "mychat"
        assert account.provider is Provider.CHATGPT
        assert creds["chatgpt_auth_json"] == str(auth_json)

    def test_login_unknown_provider_message(
        self, isolated_xdg: Path
    ) -> None:
        """Regression for Phase 7d: ``custats login --provider claude``
        still exits non-zero with the ``browser sign-in not yet
        supported`` message. Proves Claude/Grok/Cursor stay on
        cookie-paste — only Codex + ChatGPT advertise the browser
        flow."""
        result = _run_cli(
            "login",
            "--provider",
            "claude",
            "--alias",
            "x",
            xdg=isolated_xdg,
        )
        assert result.returncode != 0
        # The friendly error path mentions "not yet supported" and
        # names the provider so the user knows what to do next.
        stderr = result.stderr.lower()
        assert "not yet supported" in stderr
        assert "browser sign-in not yet supported for " in stderr
        assert "claude" in stderr
        # And it should point at the cookie-paste fallback.
        assert "custats add" in stderr

    def test_login_cloudflare_403_suggests_cookie_fallback(
        self,
        isolated_xdg: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Phase 7f: when the device-code request hits Cloudflare's
        bot-management, ``cmd_login`` surfaces a specific hint that
        points the user at ``custats add --provider codex --cookie``
        (the cookie-paste path bypasses Cloudflare entirely because
        the auth-only ``/backend-api/usage`` endpoint isn't
        Cloudflare-gated).

        Without this hint, users would see a generic HTTP 403 and have
        no way to know that header fiddling won't help — Cloudflare's
        TLS fingerprinting (JA3) is the real block.
        """
        from argparse import Namespace

        from custats.cli import _CLOUDFLARE_ERROR_MARKER, cmd_login
        from custats.core.models import Provider
        from custats.oauth import providers as providers_module

        # Sandbox HOME + XDG so the test doesn't touch the real user.
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("XDG_CONFIG_HOME", str(isolated_xdg / "config"))
        monkeypatch.setenv("XDG_DATA_HOME", str(isolated_xdg / "data"))

        async def fake_begin(client, *, on_poll=None):  # noqa: D401
            # Raise the same exception the real ``request_device_code``
            # raises when Cloudflare's JS-challenge interstitial is
            # returned — the exact marker text the CLI greps for.
            raise DeviceCodeError(  # noqa: F821 — see local import below
                "device-code request blocked by Cloudflare bot-management "
                "(HTTP 403, <title>Just a moment\u2026</title>): "
                "auth.openai.com is rejecting the client fingerprint."
            )

        from custats.oauth.device_code import DeviceCodeError

        monkeypatch.setitem(
            providers_module.SUPPORTED, Provider.CODEX, fake_begin
        )

        args = Namespace(
            provider=Provider.CODEX,
            alias="throwaway",
            config_file=None,
            db_file=None,
            key_file=None,
        )
        rc = cmd_login(args)
        captured = capsys.readouterr()

        assert rc != 0, (
            f"cmd_login returned {rc} (expected non-zero); "
            f"stderr=\n{captured.err}\nstdout=\n{captured.out}"
        )
        # Belt-and-braces: the fake_begin message did carry the marker.
        assert _CLOUDFLARE_ERROR_MARKER in (
            "device-code request blocked by Cloudflare bot-management "
            "(HTTP 403, <title>Just a moment\u2026</title>): "
            "auth.openai.com is rejecting the client fingerprint."
        )

        stderr = captured.err.lower()
        # The cookie-paste workaround is the primary recommended fix.
        assert "cookie" in stderr, (
            f"Cloudflare hint did not mention cookie fallback: {captured.err!r}"
        )
        # And it must name the exact command line the user should run.
        assert "custats add --provider codex --cookie" in stderr, (
            f"Cloudflare hint did not include the cookie-paste command: "
            f"{captured.err!r}"
        )
        # Sanity: the Cloudflare diagnosis is mentioned so the user
        # understands WHY cookie paste is the right fix.
        assert "cloudflare" in stderr

    def test_login_copilot_happy_path(
        self,
        isolated_xdg: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Phase 8c: ``custats login --provider copilot --alias mycopilot``
        mirrors the Codex / ChatGPT happy paths. The token bundle is
        persisted to ``$XDG_CONFIG_HOME/custats/copilot.json`` (NOT a
        ``~/.copilot/`` directory — GitHub doesn't ship a CLI whose
        auth file we'd be aliasing), the account row lands in the
        encrypted DB, and the credential blob uses ``copilot_auth_json``
        (matching :class:`GitHubCopilotAdapter`)."""
        from argparse import Namespace

        from custats.cli import _open_db, cmd_login
        from custats.core.models import Provider
        from custats.oauth import providers as providers_module
        from custats.oauth.device_code import (
            DeviceCodeApproved,
            DeviceCodeRequest,
        )

        # Sandbox HOME + XDG so the test doesn't touch the real user.
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("XDG_CONFIG_HOME", str(isolated_xdg / "config"))
        monkeypatch.setenv("XDG_DATA_HOME", str(isolated_xdg / "data"))

        fake_initial = DeviceCodeRequest(
            device_id="dev-copilot-abc",
            user_code="WXYZ-9876",
            verification_url="https://github.com/login/device",
            interval_seconds=0,
            expires_in_seconds=899,
        )
        fake_final_raw = {
            "access_token": "ghu_copilot",
            "refresh_token": "ghr_copilot",
            "token_type": "bearer",
            "scope": "copilot read:user",
            "expires_in": 28800,
        }

        async def fake_begin(client, *, on_poll=None):  # noqa: D401
            return fake_initial, DeviceCodeApproved(raw=fake_final_raw)

        monkeypatch.setitem(
            providers_module.SUPPORTED, Provider.COPILOT, fake_begin
        )

        args = Namespace(
            provider=Provider.COPILOT,
            alias="mycopilot",
            config_file=None,
            db_file=None,
            key_file=None,
        )
        rc = cmd_login(args)
        captured = capsys.readouterr()

        assert rc == 0, captured.err
        # Friendly output: the user-visible lines must mention the alias
        # and the provider.
        assert "added" in captured.out.lower()
        assert "mycopilot" in captured.out
        assert "copilot" in captured.out

        # The token blob landed in the sandboxed XDG config dir, not
        # a ``~/.copilot/`` directory — Phase 8c explicitly routes
        # Copilot to ``config_dir()/copilot.json`` because GitHub
        # doesn't ship a CLI whose auth.json we'd be aliasing.
        config_dir_path = isolated_xdg / "config" / "custats"
        auth_json = config_dir_path / "copilot.json"
        assert auth_json.is_file(), (
            f"{auth_json} was not written; sandbox XDG_CONFIG_HOME was "
            f"{isolated_xdg / 'config'}"
        )
        on_disk = json.loads(auth_json.read_text(encoding="utf-8"))
        assert on_disk["access_token"] == "ghu_copilot"
        assert on_disk["refresh_token"] == "ghr_copilot"
        assert on_disk["scope"] == "copilot read:user"

        # The account row should be in the DB; the credential blob
        # references the written auth.json path under the
        # ``copilot_auth_json`` key (matching ``GitHubCopilotAdapter``).
        key_path = config_dir_path / "secret.key"
        db_path = isolated_xdg / "data" / "custats" / "state.db"
        with _open_db(Namespace(key_file=key_path, db_file=db_path)) as db:
            rows = db.list_accounts(include_inactive=False)

        assert len(rows) == 1
        account, creds = rows[0]
        assert account.alias == "mycopilot"
        assert account.provider is Provider.COPILOT
        assert creds["copilot_auth_json"] == str(auth_json)

        # And the verification URL printed up-front is GitHub's, not
        # OpenAI's — a regression pin so future URL tweaks stay
        # provider-aware.
        assert "https://github.com/login/device" in captured.out
