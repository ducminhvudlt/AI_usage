"""Tests for the first-run onboarding wizard (``custats onboard``).

These tests monkeypatch the I/O injection points
(``stdout``/``prompt``/``isatty``) and ``shutil.which`` so we can
exercise the wizard deterministically without a real TTY or shell.
"""
from __future__ import annotations

from typing import Any
from unittest import mock

import pytest

from custats.core.models import Provider
from custats.onboarding import (
    HINTS,
    ProviderHint,
    detect_installed,
    detect_uninstalled,
    onboard,
    recommended_command,
)


# ---------------------------------------------------------------------- #
# HINTS table
# ---------------------------------------------------------------------- #


class TestHintsTable:
    def test_hints_table_has_11_providers(self) -> None:
        """Spec: 11 hint rows — one per supported provider."""
        assert len(HINTS) == 11

    def test_hints_cover_every_supported_provider(self) -> None:
        """Every :class:`Provider` member should appear in :data:`HINTS`."""
        hinted = {h.provider for h in HINTS}
        assert hinted == set(Provider)

    def test_hints_are_frozen(self) -> None:
        """Hint dataclass is frozen — no mutation of the table at runtime."""
        with pytest.raises(Exception):
            HINTS[0].cli_binary = "mutated"  # type: ignore[misc]


# ---------------------------------------------------------------------- #
# detect_installed / detect_uninstalled
# ---------------------------------------------------------------------- #


class TestDetectInstalled:
    def test_detect_installed_finds_known_bins(self) -> None:
        """Three on-PATH binaries → three detected providers (one per binary)."""
        # ``codex`` triggers both Codex + ChatGPT hints; dedup keeps one.
        which_map = {
            "claude": "/usr/local/bin/claude",
            "codex": "/usr/local/bin/codex",
            "gh": "/usr/local/bin/gh",
        }
        with mock.patch("custats.onboarding.shutil.which", side_effect=lambda b: which_map.get(b)):
            found = detect_installed()
        assert len(found) == 3
        binaries = {h.cli_binary for h in found}
        assert binaries == {"claude", "codex", "gh"}

    def test_detect_installed_handles_missing(self) -> None:
        """All binaries missing → empty list."""
        with mock.patch("custats.onboarding.shutil.which", return_value=None):
            assert detect_installed() == []

    def test_detect_installed_dedupes_shared_binaries(self) -> None:
        """``codex`` is shared by Codex + ChatGPT; dedup keeps the first hint."""
        with mock.patch(
            "custats.onboarding.shutil.which",
            side_effect=lambda b: "/usr/bin/codex" if b == "codex" else None,
        ):
            found = detect_installed()
        # CHATGPT appears first in HINTS (above CODEX), so it wins the dedup.
        assert len(found) == 1
        assert found[0].provider is Provider.CHATGPT


class TestDetectUninstalled:
    def test_detect_uninstalled_excludes_installed(self) -> None:
        """``codex`` present → ChatGPT wins the installed slot (first in HINTS);
        Codex is also excluded from uninstalled because both share the
        same ``codex`` binary — we only want one of them to surface in
        each list to avoid asking the user twice about the same setup.
        """
        with mock.patch(
            "custats.onboarding.shutil.which",
            side_effect=lambda b: "/usr/bin/codex" if b == "codex" else None,
        ):
            installed = detect_installed()
            uninstalled = detect_uninstalled()
        # Codex + ChatGPT share `codex`; both are filtered out of uninstalled
        # by the binary-dedup rule.
        assert {h.cli_binary for h in installed} == {"codex"}
        assert all(h.cli_binary != "codex" for h in uninstalled)
        # Installed + uninstalled should partition the unique binaries (HINTS
        # has duplicate-binary entries for Codex/ChatGPT that double-count).
        unique_binaries = {h.cli_binary for h in HINTS}
        assert len(installed) + len(uninstalled) == len(unique_binaries)

    def test_detect_uninstalled_includes_everything_when_path_empty(self) -> None:
        with mock.patch("custats.onboarding.shutil.which", return_value=None):
            uninstalled = detect_uninstalled()
        # One hint per unique binary (Codex and ChatGPT share ``codex``).
        unique_binaries = {h.cli_binary for h in HINTS}
        assert len(uninstalled) == len(unique_binaries)


# ---------------------------------------------------------------------- #
# recommended_command
# ---------------------------------------------------------------------- #


class TestRecommendedCommand:
    def test_recommended_command_for_browser_oauth(self) -> None:
        cmd = recommended_command(ProviderHint(Provider.CHATGPT, "codex", "browser_oauth"))
        assert cmd.startswith("custats login --provider chatgpt")

    def test_recommended_command_for_cookie(self) -> None:
        cmd = recommended_command(ProviderHint(Provider.CLAUDE, "claude", "cookie"))
        assert "--session-key" in cmd
        assert "claude" in cmd

    def test_recommended_command_for_auth_json(self) -> None:
        cmd = recommended_command(ProviderHint(Provider.GROK, "grok", "auth_json"))
        assert "--auth-json" in cmd
        assert "grok" in cmd

    def test_recommended_command_for_api_key(self) -> None:
        cmd = recommended_command(
            ProviderHint(Provider.OPENROUTER, "openrouter", "api_key")
        )
        assert "--api-key" in cmd
        assert "openrouter" in cmd

    def test_recommended_command_for_cursor_cookie(self) -> None:
        """Cursor's recommended command should mention ``WorkosCursor``."""
        cmd = recommended_command(ProviderHint(Provider.CURSOR, "cursor", "cookie"))
        assert "WorkosCursor" in cmd
        assert "cursor" in cmd

    def test_recommended_command_unknown_flow(self) -> None:
        """An unknown ``auth_flow`` value yields a comment, not a crash."""
        cmd = recommended_command(ProviderHint(Provider.CLAUDE, "claude", "???"))
        assert cmd.startswith("#")


# ---------------------------------------------------------------------- #
# onboard()
# ---------------------------------------------------------------------- #


class TestOnboard:
    def test_onboard_full_flow_accepts_all(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """All detected providers accepted → subprocess invoked, summary printed."""
        stdout_lines: list[str] = []
        # Pretend `claude` + `codex` are installed.
        which_map = {
            "claude": "/usr/local/bin/claude",
            "codex": "/usr/local/bin/codex",
        }
        monkeypatch.setattr(
            "custats.onboarding.shutil.which", lambda b: which_map.get(b)
        )
        # Don't actually exec anything.
        run_calls: list[Any] = []
        monkeypatch.setattr(
            "custats.onboarding.subprocess.run",
            lambda args, **_kwargs: run_calls.append(args) or mock.MagicMock(returncode=0),
        )

        rc = onboard(
            stdout=stdout_lines.append,
            prompt=lambda _p: "y",
            isatty=lambda: True,
        )

        assert rc == 0
        # Banner + section labels + per-provider prompt + final line.
        assert any("first-run setup wizard" in line for line in stdout_lines)
        assert any("Done" in line for line in stdout_lines)
        # At least one setup command ran (codex is detected; claude is detected).
        assert len(run_calls) >= 1
        # The first run command should be the claude or codex login/add line.
        assert any("custats" in str(call[0]) for call in run_calls)

    def test_onboard_skipped_when_not_tty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Non-TTY → single 'skipped' line, no prompts, return 0."""
        stdout_lines: list[str] = []
        monkeypatch.setattr("custats.onboarding.shutil.which", lambda b: None)
        prompt_calls: list[str] = []

        def _fake_prompt(p: str) -> str:
            prompt_calls.append(p)
            return "y"

        rc = onboard(
            stdout=stdout_lines.append,
            prompt=_fake_prompt,
            isatty=lambda: False,
        )

        assert rc == 0
        # No prompts in non-TTY mode.
        assert prompt_calls == []
        # Single "skipped" line on stdout.
        skipped = [line for line in stdout_lines if "skipped" in line]
        assert len(skipped) == 1
        assert "no TTY" in skipped[0]

    def test_onboard_user_declines_setup(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """User answers 'n' → no subprocess.run calls, returns 0."""
        stdout_lines: list[str] = []
        which_map = {"codex": "/usr/local/bin/codex"}
        monkeypatch.setattr(
            "custats.onboarding.shutil.which", lambda b: which_map.get(b)
        )
        run_calls: list[Any] = []
        monkeypatch.setattr(
            "custats.onboarding.subprocess.run",
            lambda args, **_kwargs: run_calls.append(args) or mock.MagicMock(returncode=0),
        )

        rc = onboard(
            stdout=stdout_lines.append,
            prompt=lambda _p: "n",
            isatty=lambda: True,
        )

        assert rc == 0
        assert run_calls == []
        # The 'Done' line still printed.
        assert any("Done" in line for line in stdout_lines)

    def test_onboard_partial_accept(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Mixed answers (y for first, n for second) → only first runs."""
        stdout_lines: list[str] = []
        which_map = {"claude": "/usr/local/bin/claude", "codex": "/usr/local/bin/codex"}
        monkeypatch.setattr(
            "custats.onboarding.shutil.which", lambda b: which_map.get(b)
        )
        answers = iter(["y", "n"])
        run_calls: list[Any] = []
        monkeypatch.setattr(
            "custats.onboarding.subprocess.run",
            lambda args, **_kwargs: run_calls.append(args) or mock.MagicMock(returncode=0),
        )

        rc = onboard(
            stdout=stdout_lines.append,
            prompt=lambda _p: next(answers),
            isatty=lambda: True,
        )

        assert rc == 0
        # Only one command ran (we said yes to exactly one provider).
        assert len(run_calls) == 1

    def test_onboard_empty_answer_skips_setup(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Blank prompt answer → no setup, returns 0, 'Done' printed."""
        stdout_lines: list[str] = []
        which_map = {"codex": "/usr/local/bin/codex"}
        monkeypatch.setattr(
            "custats.onboarding.shutil.which", lambda b: which_map.get(b)
        )
        run_calls: list[Any] = []
        monkeypatch.setattr(
            "custats.onboarding.subprocess.run",
            lambda args, **_kwargs: run_calls.append(args) or mock.MagicMock(returncode=0),
        )

        rc = onboard(
            stdout=stdout_lines.append,
            prompt=lambda _p: "",
            isatty=lambda: True,
        )

        assert rc == 0
        assert run_calls == []
        assert any("Done" in line for line in stdout_lines)