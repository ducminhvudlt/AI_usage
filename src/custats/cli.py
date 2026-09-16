"""Command-line entry point for custats.

This file owns the user-facing surface area. All subcommands are
implemented as small functions that take parsed ``argparse``
namespaces and return an exit code — easy to drive from tests.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
import signal
import sys
import threading
from argparse import Namespace
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable, Sequence

import httpx

from . import __version__
from .core.config import (
    config_dir,
    config_file,
    db_file,
    load_config,
    save_config,
    secret_key_file,
    state_dir,
)
from .core.models import Account, Provider
from .core.time_utils import now_utc
from .notifier import Notifier
from .oauth.device_code import (
    DeviceCodeApproved,
    DeviceCodeCancelled,
    DeviceCodeError,
    DeviceCodeRequest,
)
from .oauth.providers import SUPPORTED as BROWSER_FLOW_SUPPORTED
from .oauth.refresh import (
    RefreshError,
    read_auth_json,
    refresh_github_token,
    refresh_openai_token,
    write_auth_json,
)
from .poller import PollTarget, Poller
from .storage.db import Database
from .storage.encrypted import load_or_create_key

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_NOT_IMPLEMENTED = 2
EXIT_GTK_UNAVAILABLE = 3


# Phrase used by :mod:`custats.oauth.device_code` to mark errors that
# came from Cloudflare's JS-challenge interstitial (HTTP 403 +
# ``<title>Just a moment…</title>``). ``_run_login_flow`` greps for
# this substring to decide between the generic error and the
# cookie-fallback hint.
_CLOUDFLARE_ERROR_MARKER = "blocked by Cloudflare bot-management"


# ---------------------------------------------------------------------- #
# helpers
# ---------------------------------------------------------------------- #


def _provider_type(value: str) -> Provider:
    """argparse ``type=`` for ``--provider`` that yields a friendlier message.

    argparse's built-in ``choices=`` formatter says ``invalid choice`` —
    we want ``unknown provider`` so users get a direct hint at the cause.
    """
    try:
        return Provider.parse(value)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"unknown provider '{value}' (choose from: "
            f"{', '.join(repr(p.value) for p in Provider)})"
        )


def _open_db(args: Namespace) -> Database:
    """Open a :class:`Database` using the configured paths and key."""
    key_path = Path(args.key_file) if getattr(args, "key_file", None) else secret_key_file()
    key = load_or_create_key(key_path)
    db_path = Path(args.db_file) if getattr(args, "db_file", None) else db_file()
    return Database(db_path, key)


def _read_auth_json(path: str | os.PathLike[str]) -> dict[str, str]:
    """Load a JSON auth file and validate it has the expected shape."""
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"auth-json file not found: {p}")
    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("auth-json must be a JSON object")
    return {str(k): str(v) for k, v in data.items()}


# ---------------------------------------------------------------------- #
# subcommand handlers
# ---------------------------------------------------------------------- #


def cmd_run(args: Namespace) -> int:
    """Start the menu-bar app: tray icon + polling loop.

    Phase 4 wiring: builds a :class:`~custats.ui.tray.TrayIcon` and runs
    ``Gtk.main()``. The async poller runs on a background thread with
    its own asyncio loop so GTK can own the main thread. When GTK is
    unavailable the command falls back to the headless poller loop so
    the same binary still works on servers / CI; only the menu-bar UI
    is skipped (with a friendly stderr message).
    """
    try:
        config = load_config(
            Path(args.config_file) if getattr(args, "config_file", None) else None
        )
    except Exception as exc:  # noqa: BLE001
        print(f"error: failed to load config: {exc}", file=sys.stderr)
        return EXIT_ERROR

    db = _open_db(args)
    try:
        rows = db.list_accounts(include_inactive=False)
    except Exception as exc:  # noqa: BLE001
        print(f"error: failed to open database: {exc}", file=sys.stderr)
        db.close()
        return EXIT_ERROR

    if not rows:
        print(
            "custats: no accounts configured — add one with `custats add` first.",
            file=sys.stderr,
        )
        db.close()
        return EXIT_ERROR

    targets: list[PollTarget] | None = None
    notifier: Notifier | None = None
    poller: Poller | None = None
    try:
        targets = [PollTarget(account=account, credentials=creds) for account, creds in rows]
        notifier = Notifier(enabled=True)
        poller = Poller(targets=targets, config=config, db=db, notifier=notifier)
    except Exception as exc:  # noqa: BLE001
        print(f"error: failed to start poller: {exc}", file=sys.stderr)
        db.close()
        return EXIT_ERROR

    # --- Phase 4+5: try the tray + main window; fall back to headless if GTK missing ---
    from .ui.tray import TrayIcon, TrayUnavailable

    def _refresh_now_coro(p: Poller):
        """Coroutine wrapper that runs one extra poll cycle (errors swallowed)."""
        async def _go() -> None:
            try:
                await p.poll_once()
            except Exception:  # noqa: BLE001 — never let refresh crash the tray
                pass
        return _go()

    def _stop_main_loop() -> None:
        """Quit ``Gtk.main()`` from inside a menu callback."""
        try:
            from gi.repository import Gtk  # type: ignore[import-not-found]

            Gtk.main_quit()
        except Exception:
            pass

    main_window = None
    try:
        from .ui.main_window import MainWindow

        main_window = MainWindow(db=db, config=config, poller=poller)
    except TrayUnavailable as exc:
        # ``main_window`` stays None — the tray menu's "Open Dashboard"
        # becomes a no-op when GTK isn't usable for the main window.
        print(
            f"custats: GTK not available for the main window ({exc}).",
            file=sys.stderr,
        )

    def _open_dashboard() -> None:
        """Tray-menu callback: raise the Phase 5 settings window."""
        if main_window is not None:
            main_window.show()

    tray: TrayIcon | None = None
    try:
        tray = TrayIcon(
            on_open_dashboard=_open_dashboard,
            on_refresh=lambda: poller.submit_from_any_thread(
                _refresh_now_coro(poller)
            ),
            on_quit=_stop_main_loop,
        )
    except TrayUnavailable as exc:
        print(
            "custats: GTK not available, running in headless mode; install "
            "gir1.2-gtk-3.0 and gir1.2-appindicator3-0.1 for the tray UI "
            f"({exc}).",
            file=sys.stderr,
        )

    if tray is not None:
        # Push each poll snapshot into the tray icon.
        def _on_tray_snapshot(snapshot: dict) -> None:
            try:
                tray.update(snapshot)
            except Exception:
                pass

        poller.subscribe(_on_tray_snapshot)

        # Run the async poller on a background thread so Gtk.main() can
        # own the main thread. The thread is a daemon so it dies with the
        # process.
        poller_thread = threading.Thread(
            target=lambda: asyncio.run(_run_poller(poller)),
            name="custats-poller",
            daemon=True,
        )
        poller_thread.start()

        print(
            f"custats: polling {len(targets)} account(s) every "
            f"{config.refresh_interval_seconds}s (Ctrl-C to stop)...",
            file=sys.stderr,
        )

        try:
            import gi  # type: ignore[import-not-found]

            gi.require_version("Gtk", "3.0")
            from gi.repository import Gtk  # type: ignore[import-not-found]

            Gtk.main()
        except (ImportError, ValueError):
            print("custats: Gtk import failed at runtime.", file=sys.stderr)
        finally:
            # ``_run_poller``'s ``finally`` already calls ``poller.stop()``;
            # ``Poller.stop`` is idempotent so we don't double-invoke here.
            try:
                if main_window is not None:
                    main_window.destroy()
            except Exception:
                pass
            try:
                tray.shutdown()
            except Exception:
                pass
            try:
                db.close()
            except Exception:
                pass

        print("custats: stopped.", file=sys.stderr)
        return EXIT_OK

    # --- Headless fallback (no tray): same poller, no Gtk.main() ---
    def _on_snapshot(snapshot: dict) -> None:
        for status in snapshot.values():
            err = f" ({status.error})" if status.error else ""
            print(
                f"[{status.alias}/{status.provider.value}] "
                f"5h={status.five_hour_percent!s} "
                f"7d={status.seven_day_percent!s} "
                f"pace={status.pace_label}{err}"
            )

    poller.subscribe(_on_snapshot)

    print(
        f"custats: polling {len(targets)} account(s) every "
        f"{config.refresh_interval_seconds}s (Ctrl-C to stop)...",
        file=sys.stderr,
    )

    try:
        asyncio.run(_run_poller(poller))
    except KeyboardInterrupt:
        pass
    finally:
        db.close()
    print("custats: stopped.", file=sys.stderr)
    return EXIT_OK


async def _run_poller(poller: Poller) -> None:
    """Wrap ``Poller.start/stop`` in signal-aware cancellation."""
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _request_stop() -> None:
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _request_stop)
        except (NotImplementedError, RuntimeError):
            # Signal handlers may not be available on all platforms
            # (e.g. when running in a non-main thread).
            pass

    await poller.start()
    try:
        await stop_event.wait()
    finally:
        await poller.stop()


def cmd_add(args: Namespace) -> int:
    """Add a new account to the local database."""
    # ``args.provider`` is already a :class:`Provider` instance — argparse's
    # ``type=_provider_type`` did the parsing (and validation) for us.
    provider = args.provider

    alias = (args.alias or "").strip()
    if not alias:
        alias = input("Alias (e.g. 'work'): ").strip()
    if not alias:
        print("error: alias is required", file=sys.stderr)
        return EXIT_ERROR

    credentials = _collect_credentials(provider, args)
    if credentials is None:
        return EXIT_ERROR

    account = Account(
        id=Account.new_id(),
        alias=alias,
        provider=provider,
        created_at=now_utc(),
    )

    try:
        with _open_db(args) as db:
            db.add_account(account, credentials)
    except Exception as exc:  # noqa: BLE001 — surface a friendly message
        print(f"error: failed to save account: {exc}", file=sys.stderr)
        return EXIT_ERROR

    print(f"added {provider.value} account {account.alias!r} as {account.id}")
    return EXIT_OK


def _collect_credentials(
    provider: Provider, args: Namespace
) -> dict[str, str] | None:
    """Resolve credentials from flags + interactive prompts.

    The credentials dict is intentionally minimal — provider-specific
    keys live on top. Phase 2/3 will read these when calling the
    upstream APIs; for Phase 1 we just persist them encrypted.
    """
    creds: dict[str, str] = {}
    if args.session_key:
        creds["session_key"] = args.session_key
    if args.cookie:
        creds["cookie"] = args.cookie
    if args.api_key:
        creds["api_key"] = args.api_key
    if args.auth_json:
        try:
            creds.update(_read_auth_json(args.auth_json))
        except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return None

    # Only Claude uses session_key; other providers (Codex, Grok, Cursor) need
    # either a cookie or an auth-json file — both are already in `creds` from
    # the dialog/CLI flags above. Don't block the GUI flow on `getpass` for
    # providers that wouldn't use the answer.
    if provider == Provider.CLAUDE and not creds.get("session_key"):
        key = getpass.getpass(f"{provider.value} session key (leave blank to skip): ")
        if key.strip():
            creds["session_key"] = key.strip()

    if not creds:
        print(
            "warning: no credentials supplied; the account will be stored "
            "without secrets and cannot fetch usage yet.",
            file=sys.stderr,
        )
    return creds


def cmd_login(args: Namespace) -> int:
    """Run the browser OAuth device-code sign-in for a supported provider.

    Today this supports Codex + ChatGPT (both reuse OpenAI's
    ``auth.openai.com`` device-code endpoint) and GitHub Copilot
    (GitHub's ``github.com/login/device/code`` endpoint). Other
    providers fall back to the cookie-paste path via ``custats add``.

    Steps for Codex / ChatGPT / Copilot:

    1. Print the verification URL + short user code.
    2. Run the device-code flow until approved or cancelled.
    3. Persist the returned token blob to the provider's canonical
       auth.json path so the existing adapter picks it up unchanged.
    4. Insert the account row via :meth:`Database.add_account`.
    """
    provider = args.provider
    if provider not in BROWSER_FLOW_SUPPORTED:
        print(
            f"error: browser sign-in not yet supported for {provider.value!r}; "
            f"use `custats add --provider {provider.value}` to paste a "
            f"cookie / session-key instead.",
            file=sys.stderr,
        )
        return EXIT_ERROR

    alias = (args.alias or "").strip()
    if not alias:
        alias = input("Alias (e.g. 'work'): ").strip()
    if not alias:
        print("error: alias is required", file=sys.stderr)
        return EXIT_ERROR

    # The verification URL is stable (provider-known) — surface it up
    # front so the user can flip to their browser while we open the
    # connection and ask OpenAI for the short code. The actual code
    # ("ABCD-EFGH") is only known once the device-code request returns,
    # so we print it inside ``_run_login_flow``.
    print(f"custats: opening browser sign-in for {provider.value}")
    # Each provider's verification URL is stable + provider-known; surface
    # it up-front so the user can flip to their browser while we open the
    # connection and ask for the short code. Codex + ChatGPT share the
    # same OpenAI endpoint; Copilot uses GitHub's own device-code page.
    if provider == Provider.COPILOT:
        print("Visit: https://github.com/login/device")
        print("(requesting short code from github.com/login/device/code...)")
    else:
        print("Visit: https://auth.openai.com/codex/device")
        print("(requesting short code from auth.openai.com...)")

    begin_flow = BROWSER_FLOW_SUPPORTED[provider]
    if begin_flow is None:  # pragma: no cover — defensive: SUPPORTED filters this
        print(
            f"error: no browser flow registered for {provider.value!r}",
            file=sys.stderr,
        )
        return EXIT_ERROR

    return _run_login_flow(
        args=args,
        provider=provider,
        alias=alias,
        begin_flow=begin_flow,
    )


def _run_login_flow(
    *,
    args: Namespace,
    provider: Provider,
    alias: str,
    begin_flow: Callable[..., Awaitable[tuple[DeviceCodeRequest, DeviceCodeApproved]]],
) -> int:
    """Drive the async browser sign-in flow and persist the result."""
    printed_code = False

    def _on_poll(body: dict) -> None:
        status = str(body.get("status", "")).lower()
        if status in {"ok", "approved", "success"}:
            # Final approval — don't print the "waiting…" line.
            return
        print(
            f"  still waiting... (status={status or 'pending'})",
            file=sys.stderr,
        )

    async def _go() -> tuple[DeviceCodeRequest, DeviceCodeApproved]:
        # Tight 10 s timeout: device-code flows are short-lived and
        # we're talking to a known-public endpoint. We don't want a
        # hung TCP connection to strand the user at a prompt.
        timeout = httpx.Timeout(connect=5.0, read=10.0, write=5.0, pool=5.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            return await begin_flow(client, on_poll=_on_poll)

    try:
        initial, final = asyncio.run(_go())
    except DeviceCodeCancelled:
        print("custats: cancelled.", file=sys.stderr)
        return EXIT_ERROR
    except DeviceCodeError as exc:
        msg = str(exc)
        if _CLOUDFLARE_ERROR_MARKER in msg:
            # Specific 403-with-HTML pattern: tell the user exactly how
            # to work around it. The cookie-paste path bypasses
            # Cloudflare entirely because the auth-only
            # ``/backend-api/usage`` endpoint isn't Cloudflare-gated.
            _print_cloudflare_hint(provider)
        else:
            print(f"custats: browser sign-in failed: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except KeyboardInterrupt:
        # Ctrl-C at the prompt should look like a clean cancel.
        print("\ncustats: cancelled.", file=sys.stderr)
        return EXIT_ERROR

    # Print the short code + expiry line as soon as the request returned.
    if not printed_code:
        minutes, seconds = divmod(initial.expires_in_seconds, 60)
        expiry_label = (
            f"{minutes}:{seconds:02d}" if minutes else f"{seconds}s"
        )
        print(f"Enter code: {initial.user_code}")
        print(
            f"(expires in {expiry_label} — press Ctrl-C to cancel)",
            file=sys.stderr,
        )

    # For Codex/ChatGPT: persist the returned approval dict as
    # ``~/.codex/auth.json`` or ``~/.chatgpt/auth.json`` so the
    # existing adapter reads it. The on-disk shape is identical for
    # both OpenAI products (they share the same OAuth handler) — only
    # the destination path differs. Future providers (if they publish
    # device-code endpoints) may have different on-disk shapes; for
    # those, the per-provider module would expose a
    # ``persist(approval, path)`` hook. Until then, the generic shape
    # is a dict written verbatim.
    auth_json_path, credential_key = _persist_auth_json(provider, final.raw)

    credentials = {credential_key: str(auth_json_path)}

    account = Account(
        id=Account.new_id(),
        alias=alias,
        provider=provider,
        created_at=now_utc(),
    )
    try:
        with _open_db(args) as db:
            db.add_account(account, credentials)
    except Exception as exc:  # noqa: BLE001
        print(f"error: failed to save account: {exc}", file=sys.stderr)
        return EXIT_ERROR

    print(f"custats: account '{alias}' ({provider.value}) added.")
    return EXIT_OK


def _persist_auth_json(provider: Provider, raw: dict) -> tuple[Path, str]:
    """Write ``raw`` to the provider's canonical auth.json location.

    Returns ``(path, credential_key)`` — the on-disk JSON lives at
    ``path``, and the persisted credentials dict should reference it
    under ``credential_key`` (so the matching adapter can find it).

    Today both OpenAI providers use the same auth.json shape; only the
    destination path + the credential key differ. GitHub Copilot lands
    under ``$XDG_CONFIG_HOME/custats/copilot.json`` (custats' own XDG
    config dir — GitHub doesn't ship a CLI whose auth file we'd be
    aliasing, so we don't pretend to). Adding a new browser flow only
    requires extending this function (plus a per-provider ``begin``
    registered in :mod:`custats.oauth.providers`).
    """
    if provider == Provider.CODEX:
        path = Path.home() / ".codex" / "auth.json"
        credential_key = "auth_json_path"
    elif provider == Provider.CHATGPT:
        path = Path.home() / ".chatgpt" / "auth.json"
        credential_key = "chatgpt_auth_json"
    elif provider == Provider.COPILOT:
        # GitHub doesn't ship a CLI whose auth.json we'd be aliasing
        # (the ``gh`` CLI uses its own keyring, not a JSON file), so
        # we land in custats' own XDG config dir. ``config_dir()``
        # honours ``$XDG_CONFIG_HOME`` for sandboxed / CI runs.
        path = config_dir() / "copilot.json"
        credential_key = "copilot_auth_json"
    else:
        # Forward-compat: any future browser-flow provider lands in
        # ``~/.{provider.value}/auth.json`` and uses
        # ``{provider.value}_auth_json`` as the credential key. The
        # matching adapter reads that key directly.
        path = Path.home() / f".{provider.value}" / "auth.json"
        credential_key = f"{provider.value}_auth_json"

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(raw, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return path, credential_key


def _print_cloudflare_hint(provider: Provider) -> None:
    """Print the Cloudflare-specific workaround hint to stderr.

    Triggered when ``auth.openai.com`` returns its JS-challenge
    interstitial (HTTP 403 + ``<title>Just a moment…</title>``) even
    though we sent the Codex-CLI headers. The body is provider-aware:
    both Codex and ChatGPT share the same ``chatgpt.com`` backend, so
    the cookie paste command names the actual provider so users copy
    the right thing.

    The TLS fingerprint check cannot be bypassed from a stock
    ``httpx`` client; we point users at the cookie-paste fallback
    path (which hits the auth-only ``/backend-api/usage`` endpoint and
    is not Cloudflare-gated) rather than letting them chase a fix that
    no header change will resolve.
    """
    cookie_cmd = (
        f"custats add --provider {provider.value} "
        f"--cookie '<full Cookie header from chatgpt.com>'"
    )
    hint = (
        "custats: browser sign-in failed: Cloudflare is blocking "
        "the device-code request.\n\n"
        "This usually means OpenAI's bot-protection is rejecting the "
        "client fingerprint.\n"
        "The browser sign-in flow (custats login) cannot bypass "
        "Cloudflare's TLS\n"
        "fingerprint check from a Python httpx client.\n\n"
        "Workarounds, in order of preference:\n\n"
        "1. Cookie paste (RECOMMENDED — bypasses Cloudflare entirely):\n"
        f"   {cookie_cmd}\n"
        "   # In browser DevTools → Network → any chatgpt.com request "
        "→ Headers → Cookie: <value>\n"
        "   # The /backend-api/usage endpoint is auth-only, no "
        "Cloudflare challenge.\n\n"
        "2. Wait for OpenAI to whitelist a newer codex-cli version, "
        "then bump\n"
        "   CODEX_CLI_VERSION in src/custats/oauth/codex.py.\n\n"
        "3. Open a feature request for curl_cffi-based TLS fingerprint\n"
        "   impersonation (would let us mimic a real browser's JA3).\n"
    )
    print(hint, file=sys.stderr, end="")


def cmd_onboard(args: Namespace) -> int:
    """First-run onboarding wizard.

    Detects installed CLIs (Claude Code, Codex CLI, ``gh``, Grok CLI, …)
    and walks the user through provider setup one step at a time. The
    visual onboarding in :class:`~custats.ui.main_window.MainWindow`
    only fires after the first ``custats run``; this command is the
    terminal-only path that runs on bare installs before the GUI has
    ever been touched.

    All output goes through injected callables so the same flow is
    testable without a real TTY.
    """
    from .onboarding import onboard as _onboard

    return _onboard(
        stdout=print,
        prompt=input,
        isatty=lambda: sys.stdin.isatty(),
    )


def cmd_remove(args: Namespace) -> int:
    """Remove an account by id."""
    account_id = args.account_id.strip()
    if not account_id:
        print("error: --account-id is required", file=sys.stderr)
        return EXIT_ERROR
    try:
        with _open_db(args) as db:
            existing = db.get_account(account_id)
            if existing is None:
                print(f"error: no account with id {account_id}", file=sys.stderr)
                return EXIT_ERROR
            db.delete_account(account_id)
    except Exception as exc:  # noqa: BLE001
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    print(f"removed account {account_id} ({existing[0].alias!r})")
    return EXIT_OK


# Map provider → (auth.json credential key, refresh helper). Only providers
# that issue refresh tokens are listed; others are skipped silently under
# ``--all``. The keys here mirror the constants exported from each adapter
# module — keep them in sync if a provider is renamed.
_REFRESH_SUPPORTED: dict[Provider, tuple[str, Callable[..., Any]]] = {
    Provider.CODEX: ("auth_json_path", refresh_openai_token),
    Provider.CHATGPT: ("chatgpt_auth_json", refresh_openai_token),
    Provider.COPILOT: ("copilot_auth_json", refresh_github_token),
}


def cmd_refresh(args: Namespace) -> int:
    """Rotate the OAuth refresh token for an account.

    ``--account-id`` targets a single account. ``--all`` walks every
    account whose provider supports refresh (Codex + ChatGPT today);
    other providers are skipped silently.

    On success prints
    ``custats: account '<alias>' refreshed; new access_token expires in <n>s``.

    The refresh network call is the same one the poller uses on 401, so
    behaviour is consistent across manual and automatic rotation.
    """
    try:
        db = _open_db(args)
    except Exception as exc:  # noqa: BLE001
        print(f"error: failed to open database: {exc}", file=sys.stderr)
        return EXIT_ERROR

    targets: list[tuple[Account, dict[str, Any]]] = []
    try:
        if getattr(args, "all", False):
            rows = db.list_accounts(include_inactive=False)
            for account, creds in rows:
                if account.provider in _REFRESH_SUPPORTED:
                    targets.append((account, creds))
        else:
            account_id = (args.account_id or "").strip()
            if not account_id:
                print(
                    "error: --account-id is required (or pass --all)",
                    file=sys.stderr,
                )
                db.close()
                return EXIT_ERROR
            row = db.get_account(account_id)
            if row is None:
                print(f"error: no account with id {account_id}", file=sys.stderr)
                db.close()
                return EXIT_ERROR
            if row[0].provider not in _REFRESH_SUPPORTED:
                print(
                    f"error: refresh not supported for provider "
                    f"{row[0].provider.value!r}",
                    file=sys.stderr,
                )
                db.close()
                return EXIT_ERROR
            targets.append(row)
    finally:
        db.close()

    if not targets:
        print(
            "custats: no refreshable accounts — "
            "nothing to do (Codex + ChatGPT + GitHub Copilot only).",
            file=sys.stderr,
        )
        return EXIT_OK

    return _run_refreshes(targets)


def _run_refreshes(
    targets: list[tuple[Account, dict[str, Any]]],
) -> int:
    """Drive the async refresh loop and report per-account outcomes.

    Prints a success line for each account that rotates cleanly, and a
    ``custats: ... FAILED`` line (plus non-zero exit) for any that don't.
    """
    async def _go() -> list[tuple[Account, dict[str, Any] | None, str | None]]:
        # Tight timeout: refresh is one POST against a public endpoint.
        timeout = httpx.Timeout(connect=5.0, read=10.0, write=5.0, pool=5.0)
        results: list[tuple[Account, dict[str, Any] | None, str | None]] = []
        async with httpx.AsyncClient(timeout=timeout) as client:
            for account, creds in targets:
                auth_key, refresh_fn = _REFRESH_SUPPORTED[account.provider]
                path_str = creds.get(auth_key)
                if not isinstance(path_str, str) or not path_str.strip():
                    results.append(
                        (account, None, f"missing {auth_key!r} credential")
                    )
                    continue
                path = Path(path_str)
                try:
                    blob = read_auth_json(path)
                except RefreshError as exc:
                    results.append((account, None, str(exc)))
                    continue
                rt = blob.get("refresh_token")
                if not rt:
                    results.append(
                        (account, None, "auth.json has no refresh_token")
                    )
                    continue
                try:
                    refreshed = await refresh_fn(  # type: ignore[arg-type]
                        client=client, refresh_token=str(rt)
                    )
                except RefreshError as exc:
                    results.append((account, None, f"refresh failed: {exc}"))
                    continue
                try:
                    write_auth_json(path, refreshed)
                except OSError as exc:
                    results.append(
                        (account, None, f"failed to persist auth.json: {exc}")
                    )
                    continue
                results.append((account, refreshed, None))
        return results

    results = asyncio.run(_go())

    failures = 0
    for account, refreshed, error in results:
        if error is not None:
            print(
                f"custats: account {account.alias!r} ({account.provider.value}) "
                f"FAILED: {error}",
                file=sys.stderr,
            )
            failures += 1
            continue
        assert refreshed is not None  # invariant of the success branch
        expires = refreshed.get("expires_in", "?")
        print(
            f"custats: account {account.alias!r} ({account.provider.value}) "
            f"refreshed; new access_token expires in {expires}s"
        )
    return EXIT_ERROR if failures else EXIT_OK


def cmd_list(args: Namespace) -> int:
    """Print a small table of tracked accounts."""
    try:
        with _open_db(args) as db:
            rows = db.list_accounts(include_inactive=args.all)
    except Exception as exc:  # noqa: BLE001
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR

    header: list[str] = ["ALIAS", "PROVIDER", "ID", "ACTIVE", "LAST SEEN"]
    table: list[list[str]] = [header]
    for account, _creds in rows:
        table.append(
            [
                account.alias,
                account.provider.value,
                account.id,
                "yes" if account.is_active else "no",
                _fmt_dt(account.last_seen_at),
            ]
        )
    _print_table(table)
    return EXIT_OK


def cmd_doctor(args: Namespace) -> int:
    """Run the pre-flight environment checks."""
    failures: list[str] = []
    notes: list[str] = []

    # Python version
    if sys.version_info < (3, 10):
        failures.append(f"Python {sys.version.split()[0]} is too old (need >=3.10)")
    else:
        notes.append(f"python {sys.version.split()[0]} OK")

    # config dir writable
    cfg = config_dir()
    probe = cfg / ".doctor-probe"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        notes.append(f"config dir writable: {cfg}")
    except OSError as exc:
        failures.append(f"config dir not writable ({cfg}): {exc}")

    # state dir writable
    st = state_dir()
    try:
        probe = st / ".doctor-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        notes.append(f"state dir writable: {st}")
    except OSError as exc:
        failures.append(f"state dir not writable ({st}): {exc}")

    # secret.key present (or creatable)
    key_path = secret_key_file()
    try:
        load_or_create_key(key_path)
        notes.append(f"secret key present: {key_path}")
    except Exception as exc:  # noqa: BLE001
        failures.append(f"secret key error ({key_path}): {exc}")

    # GTK / AppIndicator availability (best-effort)
    gtk_status = _probe_gtk()
    if gtk_status:
        notes.append(gtk_status)
    else:
        notes.append("GTK / AppIndicator3 not detected (required by Phase 4)")

    print("custats doctor")
    print("--------------")
    for line in notes:
        print(f"  ok    {line}")
    for line in failures:
        print(f"  FAIL  {line}")

    if getattr(args, "json", False):
        print(
            json.dumps(
                {
                    "ok": not failures,
                    "notes": notes,
                    "failures": failures,
                    "version": __version__,
                    "python": sys.version.split()[0],
                    "config_dir": str(cfg),
                    "state_dir": str(st),
                    "config_file": str(config_file()),
                    "db_file": str(db_file()),
                    "secret_key_file": str(secret_key_file()),
                },
                indent=2,
            )
        )

    return EXIT_OK if not failures else EXIT_ERROR


def _probe_gtk() -> str | None:
    """Return a one-line status string if GTK is importable."""
    try:
        import gi  # type: ignore[import-not-found]

        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk  # type: ignore[import-not-found]  # noqa: F401

        return "GTK 3 importable"
    except Exception:
        pass
    try:
        import gi  # type: ignore[import-not-found]

        gi.require_version("AppIndicator3", "0.1")
        from gi.repository import AppIndicator3  # type: ignore[import-not-found]  # noqa: F401

        return "AppIndicator3 importable"
    except Exception:
        return None


def _service_file_path() -> Path:
    """Locate the canonical ``custats.service`` file shipped with the package.

    Tries, in order:

    1. :mod:`importlib.resources` — works when custats is installed as a wheel
       that bundles the ``packaging/`` directory as package data.
    2. Relative to this file: ``<repo_root>/packaging/custats.service`` — works
       during development (``python -m custats ...`` from a source checkout).

    Raises:
        FileNotFoundError: if neither location has the service file.
    """
    # 1. Installed package data.
    try:
        import importlib.resources as ilr

        candidate = ilr.files("custats") / "packaging" / "custats.service"
        if candidate.is_file():
            # ``Traversable`` doesn't always satisfy ``os.PathLike`` across
            # Python versions — stringifying works everywhere and matches
            # what the caller needs (a filesystem path).
            return Path(str(candidate))
    except (ImportError, ModuleNotFoundError, AttributeError):
        pass

    # 2. Source-tree fallback. ``cli.py`` lives at ``<root>/src/custats/cli.py``,
    # so three ``.parent`` hops land at the repo root.
    src_tree = Path(__file__).resolve().parent.parent.parent / "packaging" / "custats.service"
    if src_tree.is_file():
        return src_tree

    raise FileNotFoundError(
        "could not locate packaging/custats.service — neither inside the "
        "installed package nor in the source tree. Run from the repo root "
        "or use ./install.sh."
    )


def _service_user_dir() -> Path:
    """Return ``$XDG_CONFIG_HOME/systemd/user``, creating it if missing."""
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    user_dir = root / "systemd" / "user"
    user_dir.mkdir(parents=True, exist_ok=True)
    return user_dir


def cmd_install_service(_: Namespace) -> int:
    """Install and enable the custats systemd user service.

    Copies ``packaging/custats.service`` to
    ``$XDG_CONFIG_HOME/systemd/user/custats.service`` and runs
    ``systemctl --user daemon-reload`` + ``enable``. The ``enable`` step is
    best effort — if systemd isn't available the copy still succeeds so the
    user can fix the activation later.
    """
    import shutil
    import subprocess

    try:
        src = _service_file_path()
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR

    user_dir = _service_user_dir()
    dst = user_dir / "custats.service"
    shutil.copy(src, dst)

    print(f"copied {src} -> {dst}")

    # Best-effort: refresh systemd's unit cache and enable autostart.
    # Both commands may legitimately fail (no systemd, no user session, etc.).
    subprocess.run(
        ["systemctl", "--user", "daemon-reload"], check=False
    )
    res = subprocess.run(
        ["systemctl", "--user", "enable", "custats.service"],
        check=False,
    )
    if res.returncode == 0:
        print("enabled custats.service (systemd --user).")
        print(
            "    Start it now:  systemctl --user start custats.service",
            file=sys.stderr,
        )
    else:
        print(
            "warning: systemctl --user enable failed; the unit file is "
            "installed but not enabled. Re-run after starting a user "
            "systemd session, or use ./install.sh instead.",
            file=sys.stderr,
        )
        return EXIT_ERROR

    return EXIT_OK


def cmd_uninstall_service(_: Namespace) -> int:
    """Disable and remove the custats systemd user service."""
    import subprocess

    # Best-effort: disable first so we don't leave a dangling symlink in
    # ~/.config/systemd/user/default.target.wants/.
    subprocess.run(
        ["systemctl", "--user", "disable", "--now", "custats.service"],
        check=False,
    )
    subprocess.run(
        ["systemctl", "--user", "daemon-reload"], check=False
    )

    user_dir = _service_user_dir()
    svc = user_dir / "custats.service"
    if svc.exists():
        svc.unlink()
        print(f"removed {svc}")
    else:
        print(f"no service file at {svc} (already removed).")

    return EXIT_OK


def cmd_show_config(_: Namespace) -> int:
    """Print the merged configuration (defaults merged with file)."""
    cfg = load_config()
    print(f"refresh_interval_seconds = {cfg.refresh_interval_seconds}")
    print(f"notify_threshold_percent = {cfg.notify_threshold_percent}")
    print(f"notify_on_recovery       = {cfg.notify_on_recovery}")
    print(f"pace_enabled             = {cfg.pace_enabled}")
    print(f"theme                    = {cfg.theme}")
    print("show_in_menu_bar:")
    for provider in Provider:
        print(f"  {provider.value:<8} = {cfg.is_provider_visible(provider)}")
    print(f"config_file              = {config_file()}")
    print(f"db_file                  = {db_file()}")
    print(f"secret_key_file          = {secret_key_file()}")
    return EXIT_OK


# ---------------------------------------------------------------------- #
# output formatting
# ---------------------------------------------------------------------- #


def _fmt_dt(dt: datetime | None) -> str:
    if dt is None:
        return "—"
    return dt.strftime("%Y-%m-%d %H:%M UTC")


def _print_table(rows: Sequence[Sequence[str]]) -> None:
    """Print a fixed-width table without external deps."""
    if not rows:
        print("(no accounts)")
        return
    rows_list = [list(r) for r in rows]
    widths = [max(len(row[i]) for row in rows_list) for i in range(len(rows_list[0]))]
    sep = "  "
    for idx, row in enumerate(rows_list):
        print(sep.join(cell.ljust(widths[i]) for i, cell in enumerate(row)))
        if idx == 0:
            print(sep.join("-" * w for w in widths))


# ---------------------------------------------------------------------- #
# argument parser
# ---------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    """Construct the top-level parser (also used by tests)."""
    parser = argparse.ArgumentParser(
        prog="custats",
        description=(
            "custats — Linux menu bar tracker for Claude, Codex, ChatGPT, "
            "GitHub Copilot, Gemini, Grok, OpenRouter, DeepSeek, "
            "Mistral, Kimi, and Cursor usage limits."
        ),
    )
    parser.add_argument(
        "--version", action="version", version=f"custats {__version__}"
    )
    parser.add_argument(
        "--config-file",
        type=Path,
        default=None,
        help="Override the path to config.toml.",
    )
    parser.add_argument(
        "--db-file",
        type=Path,
        default=None,
        help="Override the path to the SQLite database.",
    )
    parser.add_argument(
        "--key-file",
        type=Path,
        default=None,
        help="Override the path to the AES-GCM secret key.",
    )

    sub = parser.add_subparsers(dest="command", required=False)

    sub.add_parser("run", help="Start the menu bar app.").set_defaults(func=cmd_run)

    p_add = sub.add_parser("add", help="Add a new provider account.")
    p_add.add_argument(
        "--provider",
        required=True,
        type=_provider_type,
        help="Which provider to add.",
    )
    p_add.add_argument(
        "--alias", default=None, help="Friendly alias (e.g. 'work')."
    )
    p_add.add_argument(
        "--session-key", default=None, help="Provider session key (non-interactive)."
    )
    p_add.add_argument(
        "--cookie", default=None, help="Provider cookie value (non-interactive)."
    )
    p_add.add_argument(
        "--auth-json",
        default=None,
        help="Path to a JSON file containing credential fields.",
    )
    p_add.add_argument(
        "--api-key",
        default=None,
        help="Provider API key (non-interactive). Used by Gemini, "
        "OpenRouter, DeepSeek, and future key-based providers.",
    )
    p_add.set_defaults(func=cmd_add)

    p_login = sub.add_parser(
        "login",
        help=(
            "Sign in to a provider via its browser OAuth device-code flow "
            "(Codex + ChatGPT + GitHub Copilot today; other providers use "
            "`custats add`)."
        ),
    )
    p_login.add_argument(
        "--provider",
        required=True,
        type=_provider_type,
        help=(
            "Provider to sign in to. 'codex', 'chatgpt', and 'copilot' "
            "support the browser flow; other providers stay on `custats add`."
        ),
    )
    p_login.add_argument(
        "--alias", default=None, help="Friendly alias (e.g. 'work')."
    )
    p_login.set_defaults(func=cmd_login)

    p_remove = sub.add_parser("remove", help="Remove an account by id.")
    p_remove.add_argument("--account-id", required=True, help="Account id to delete.")
    p_remove.set_defaults(func=cmd_remove)

    sub.add_parser(
        "onboard",
        help=(
            "First-run wizard: detect installed CLIs and walk through "
            "provider setup."
        ),
    ).set_defaults(func=cmd_onboard)

    p_refresh = sub.add_parser(
        "refresh",
        help=(
            "Rotate OAuth refresh tokens for one or all accounts "
            "(Codex + ChatGPT + GitHub Copilot)."
        ),
    )
    refresh_group = p_refresh.add_mutually_exclusive_group(required=True)
    refresh_group.add_argument(
        "--account-id",
        default=None,
        help="Account id to refresh.",
    )
    refresh_group.add_argument(
        "--all",
        action="store_true",
        help="Refresh every refreshable account "
        "(Codex + ChatGPT + GitHub Copilot).",
    )
    p_refresh.set_defaults(func=cmd_refresh)

    p_list = sub.add_parser("list", help="List tracked accounts.")
    p_list.add_argument(
        "--all",
        action="store_true",
        help="Include inactive accounts in the output.",
    )
    p_list.set_defaults(func=cmd_list)

    p_doc = sub.add_parser("doctor", help="Run preflight checks.")
    p_doc.add_argument(
        "--json", action="store_true", help="Emit results as JSON."
    )
    p_doc.set_defaults(func=cmd_doctor)

    sub.add_parser("install-service", help="Install a systemd user unit.").set_defaults(
        func=cmd_install_service
    )
    sub.add_parser(
        "uninstall-service", help="Remove the systemd user unit."
    ).set_defaults(func=cmd_uninstall_service)

    sub.add_parser("show-config", help="Print the resolved configuration.").set_defaults(
        func=cmd_show_config
    )

    return parser


_HANDLERS: dict[str, Callable[[Namespace], int]] = {
    "run": cmd_run,
    "add": cmd_add,
    "login": cmd_login,
    "remove": cmd_remove,
    "onboard": cmd_onboard,
    "refresh": cmd_refresh,
    "list": cmd_list,
    "doctor": cmd_doctor,
    "install-service": cmd_install_service,
    "uninstall-service": cmd_uninstall_service,
    "show-config": cmd_show_config,
}


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point (registered as ``custats`` console script)."""
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = _HANDLERS.get(args.command or "")
    if handler is None:
        parser.print_help()
        return EXIT_OK
    return handler(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
