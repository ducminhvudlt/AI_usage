"""GitHub Copilot browser OAuth device-code sign-in (Phase 8c).

Uses GitHub's public RFC 8628 device-flow endpoint at
``https://github.com/login/device/code``. The flow is similar to OpenAI's
Codex device-code flow:

1. ``POST https://github.com/login/device/code`` with
   ``client_id=Iv23li5gtHhsd3iHGyqx`` (GitHub's public OAuth App ID for
   Copilot integrations; same value the official ``gh`` CLI ships) and
   ``scope="copilot read:user"`` → returns a short user code + a
   ``device_code`` for polling.

2. Poll ``https://github.com/login/oauth/access_token`` with the
   ``device_code`` until GitHub returns an ``access_token`` /
   ``refresh_token`` bundle, or until the code expires.

GitHub requires a ``User-Agent`` header on every OAuth call — unlike
OpenAI the user-agent gate is the only thing GitHub checks, so we set
a single ``custats-cli/0.1.0`` UA. No Cloudflare bypass is required.

The returned ``access_token`` is persisted to ``~/.config/custats/copilot.json``
(by the CLI's :func:`custats.cli._persist_auth_json`) and consumed by the
``GitHubCopilotAdapter`` via the ``copilot_auth_json`` credential key.
"""
from __future__ import annotations

from typing import Awaitable, Callable

import httpx

from .device_code import (
    DeviceCodeApproved,
    DeviceCodeRequest,
    poll_device_code,
    request_device_code,
)


# GitHub's public OAuth App ID for Copilot integrations. Documented in
# GitHub's CLI samples (https://github.com/cli/cli) and used by ``gh``
# itself for Copilot. It is *not* a secret — it's the same client_id any
# OAuth-app integration uses. We do not invent our own.
GITHUB_CLIENT_ID = "Iv23li5gtHhsd3iHGyqx"

GITHUB_DEVICE_REQUEST_URL = "https://github.com/login/device/code"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"

# GitHub requires a User-Agent header on every OAuth/device-code call
# (their docs explicitly call this out). Accept is application/json so
# we never get the HTML consent page back as the parsed body.
GITHUB_HEADERS: dict[str, str] = {
    "Accept": "application/json",
    "User-Agent": "custats-cli/0.1.0",
}

# Type alias for ``on_poll`` — keeps the module's public surface
# readable and matches the Codex / ChatGPT glue modules.
PollCallback = Callable[[dict], None]


async def begin(
    client: httpx.AsyncClient,
    *,
    on_poll: PollCallback | None = None,
) -> tuple[DeviceCodeRequest, DeviceCodeApproved]:
    """Run the full GitHub Copilot device-code flow. Returns (initial, final).

    The returned ``DeviceCodeApproved.raw`` is the standard OAuth2 token
    bundle (``access_token``, ``refresh_token``, ``token_type``, ``scope``,
    ``expires_in``). The CLI persists it verbatim to ``~/.config/custats/copilot.json``.

    GitHub's device-code endpoint distinguishes "pending" from "approved"
    by response shape: pending → ``{"error": "authorization_pending"}``
    (no ``status`` key), approved → ``{"access_token": ...}`` with HTTP
    200. The generic :func:`poll_device_code` helper handles both via its
    "no status key = keep polling" branch.
    """
    initial = await request_device_code(
        client=client,
        request_url=GITHUB_DEVICE_REQUEST_URL,
        payload={"client_id": GITHUB_CLIENT_ID, "scope": "copilot read:user"},
        headers=GITHUB_HEADERS,
    )
    final = await poll_device_code(
        client=client,
        poll_url=GITHUB_TOKEN_URL,
        payload={
            "client_id": GITHUB_CLIENT_ID,
            "device_code": initial.device_id,
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        },
        headers=GITHUB_HEADERS,
        on_poll=on_poll,
        interval_seconds=initial.interval_seconds,
        expires_in_seconds=initial.expires_in_seconds,
    )
    return initial, final


__all__ = [
    "GITHUB_CLIENT_ID",
    "GITHUB_DEVICE_REQUEST_URL",
    "GITHUB_HEADERS",
    "GITHUB_TOKEN_URL",
    "begin",
]


# Re-export the typing alias used by the registry in providers.py.
# Defining it here (instead of in providers.py) keeps the public
# surface close to its single concrete user, mirroring codex.py.
BrowserBegin = Callable[
    [httpx.AsyncClient],
    Awaitable[tuple[DeviceCodeRequest, DeviceCodeApproved]],
]
