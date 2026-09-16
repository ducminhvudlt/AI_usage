"""First-run setup wizard for custats.

Detects installed CLIs (Claude Code, Codex CLI, Kimi CLI, Grok CLI, etc.) and
walks the user through provider setup one step at a time.

This is a CLI-only flow (no GTK). The visual onboarding happens in the GUI
via Phase 5's `MainWindow` once the user runs `custats run` for the first
time.
"""
from __future__ import annotations

import shlex
import shutil
import subprocess
from dataclasses import dataclass
from typing import Callable

from .core.models import Provider


@dataclass(frozen=True)
class ProviderHint:
    """Detection hint for a provider.

    Attributes:
        provider: which :class:`Provider` this hint corresponds to.
        cli_binary: the binary name to look up via :func:`shutil.which`.
        auth_flow: which auth flow we'd recommend for this provider —
            one of ``"browser_oauth"``, ``"cookie"``, ``"api_key"``,
            or ``"auth_json"``.
        env_var_hint: optional name of the environment variable that
            holds an API key (only meaningful for ``api_key`` flows).
    """

    provider: Provider
    cli_binary: str                # the binary name to `which`
    auth_flow: str                  # "browser_oauth" | "cookie" | "api_key" | "auth_json"
    env_var_hint: str | None = None  # e.g. "ANTHROPIC_API_KEY" — only for API-key flows


# Detection table — what to look for in $PATH. Order is the order in
# which providers are offered to the user when multiple CLIs are detected.
HINTS: tuple[ProviderHint, ...] = (
    ProviderHint(Provider.CLAUDE, "claude", "cookie"),  # Claude Code CLI installed
    ProviderHint(Provider.CHATGPT, "codex", "browser_oauth"),  # OpenAI CLI present
    ProviderHint(Provider.CODEX, "codex", "browser_oauth"),
    ProviderHint(Provider.COPILOT, "gh", "browser_oauth"),  # gh CLI is the Copilot OAuth path
    ProviderHint(Provider.GROK, "grok", "auth_json"),  # grok CLI token file
    ProviderHint(Provider.KIMI, "kimi", "api_key"),  # kimi CLI present
    ProviderHint(Provider.MISTRAL, "mistral", "api_key"),  # no public CLI; only API key
    ProviderHint(Provider.OPENROUTER, "openrouter", "api_key"),
    ProviderHint(Provider.DEEPSEEK, "deepseek", "api_key"),
    ProviderHint(Provider.GEMINI, "gemini", "api_key"),
    ProviderHint(Provider.CURSOR, "cursor", "cookie"),
)


def detect_installed() -> list[ProviderHint]:
    """Return the providers whose CLI binary is on ``$PATH``.

    Order follows :data:`HINTS`. Duplicates by binary are deduped —
    ``codex`` (Codex + ChatGPT) and ``gh`` (Copilot) trigger multiple
    hints, but we return only the first match per binary so the wizard
    doesn't ask twice for the same detection.
    """
    found: list[ProviderHint] = []
    seen_binaries: set[str] = set()
    for hint in HINTS:
        if hint.cli_binary in seen_binaries:
            continue
        if shutil.which(hint.cli_binary) is not None:
            found.append(hint)
            seen_binaries.add(hint.cli_binary)
    return found


def detect_uninstalled() -> list[ProviderHint]:
    """Return providers NOT detected (still installable).

    Mirrors :func:`detect_installed`'s binary dedup so providers
    sharing a binary (Codex + ChatGPT) don't both show up here when
    only one binary is present.
    """
    installed_binaries = {hint.cli_binary for hint in detect_installed()}
    seen_binaries: set[str] = set()
    uninstalled: list[ProviderHint] = []
    for hint in HINTS:
        if hint.cli_binary in seen_binaries:
            continue
        if hint.cli_binary not in installed_binaries:
            uninstalled.append(hint)
        seen_binaries.add(hint.cli_binary)
    return uninstalled


def recommended_command(hint: ProviderHint, alias: str = "default") -> str:
    """Render the recommended ``custats add`` / ``custats login`` command.

    The returned string is a single shell line. The wizard hands it to
    :func:`subprocess.run` with ``shell=True`` so the user gets the
    full OAuth browser flow in the same terminal.

    The ``alias`` is shell-quoted via :func:`shlex.quote` to prevent
    injection if a future caller passes an untrusted value (today the
    wizard passes the constant string ``"default"``).
    """
    safe_alias = shlex.quote(alias)
    if hint.auth_flow == "browser_oauth":
        return f"custats login --provider {hint.provider.value} --alias {safe_alias}"
    if hint.auth_flow == "cookie":
        if hint.provider == Provider.CLAUDE:
            return (
                f'custats add --provider claude --alias {safe_alias} '
                f'--session-key "<sessionKey>"'
            )
        if hint.provider == Provider.CURSOR:
            return (
                f'custats add --provider cursor --alias {safe_alias} '
                f'--cookie "WorkosCursor=<value>"'
            )
        # Generic cookie fallback (Codex, ChatGPT, …).
        return (
            f'custats add --provider {hint.provider.value} '
            f'--alias {safe_alias} --cookie "<full Cookie header>"'
        )
    if hint.auth_flow == "auth_json":
        return (
            f"custats add --provider {hint.provider.value} "
            f"--alias {safe_alias} --auth-json ~/.grok/auth.json"
        )
    if hint.auth_flow == "api_key":
        env_note = ""
        if hint.env_var_hint:
            env_note = f"  # set ${hint.env_var_hint} or paste below"
        return (
            f'custats add --provider {hint.provider.value} '
            f'--alias {safe_alias} --api-key "<your-key>"{env_note}'
        )
    return f"# unknown auth_flow for {hint.provider.value}"


def _format_hint_line(hint: ProviderHint, alias: str = "default") -> str:
    """Render a one-line description ``- <provider>: <command>``."""
    return f"  - {hint.provider.value:<10} → {recommended_command(hint, alias)}"


def onboard(
    *,
    stdout: Callable[[str], None] = print,
    prompt: Callable[[str], str] = input,
    isatty: Callable[[], bool] = lambda: True,
) -> int:
    """Run the onboarding wizard interactively.

    ``stdout`` / ``prompt`` / ``isatty`` are injected for testability.
    Returns :data:`~custats.cli.EXIT_OK` (0) on full setup, or the same
    code if the user bails. Non-TTY environments (CI) get a one-line
    summary instead of prompts and still return 0.
    """
    # Lazy import keeps the test-only ``--help`` path light and avoids
    # a hard dependency on cli.py from the wizard's own test surface.
    try:
        from .cli import EXIT_OK
    except Exception:  # noqa: BLE001 — defensive for tests where cli can't be imported
        EXIT_OK = 0

    installed = detect_installed()
    uninstalled = detect_uninstalled()

    stdout("=" * 60)
    stdout("custats — first-run setup wizard")
    stdout("=" * 60)
    stdout("")
    stdout("Detected CLIs (we can wire these up for you):")
    if installed:
        for hint in installed:
            stdout(_format_hint_line(hint))
    else:
        stdout("  (none of the supported CLIs were found on $PATH)")
    stdout("")

    if uninstalled:
        stdout("Other supported providers (no CLI detected — use API keys):")
        for hint in uninstalled:
            stdout(_format_hint_line(hint))
        stdout("")
        stdout("  Tip: see docs/setup-guide.md for the exact command per provider.")
        stdout("")

    if not isatty():
        # Non-interactive: print the summary once and bail cleanly. CI
        # smoke runs land here, so we make the line greppable.
        stdout(
            f"custats onboard: skipped (no TTY); "
            f"{len(installed)} CLI(s) detected, {len(uninstalled)} remaining"
        )
        return EXIT_OK

    stdout("Set up a provider now? You can answer 'y' or 'n' per line.")
    stdout("Leave blank and press Enter to skip everything.")
    stdout("")

    for hint in installed:
        answer = prompt(
            f"  Set up `{hint.provider.value}` now? [y/N] "
        ).strip().lower()
        if answer not in {"y", "yes"}:
            continue
        cmd = recommended_command(hint)
        stdout(f"  → running: {cmd}")
        try:
            subprocess.run([cmd], shell=True, check=False)
        except Exception as exc:  # noqa: BLE001 — don't crash the wizard
            stdout(f"  ! command raised: {exc}")

    stdout("")
    stdout("Done. Run `custats run` to start the menu bar app.")
    return EXIT_OK


__all__ = [
    "HINTS",
    "ProviderHint",
    "detect_installed",
    "detect_uninstalled",
    "onboard",
    "recommended_command",
]