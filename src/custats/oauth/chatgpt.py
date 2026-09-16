"""ChatGPT browser OAuth device-code sign-in.

Same OpenAI endpoint as Codex, but with a broader ``device_code_hint``
so the returned token works for both codex.com and chatgpt.com backends.

The persisted auth.json shape matches Codex's — the existing
:ChatGPTAdapter: reads it via the ``chatgpt_auth_json`` credential key.

Flow (mirrors :mod:`custats.oauth.codex`):

1. ``POST https://auth.openai.com/api/accounts/device/login``
   body: ``{"device_code_hint": ["codex", "chatgpt"]}``
2. Poll the same endpoint until ``status == "ok"`` with
   ``authorization_code`` + ``code_verifier``.

The actual code-for-token exchange is left to the OAuth refresh helper
(:mod:`custats.oauth.refresh`) — the approval dict is persisted
verbatim to ``~/.chatgpt/auth.json`` so the adapter picks it up
unchanged.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

import httpx

from .codex import CODEX_HEADERS, PollCallback
from .device_code import (
    DeviceCodeApproved,
    DeviceCodeRequest,
    poll_device_code,
    request_device_code,
)


REQUEST_URL = "https://auth.openai.com/api/accounts/device/login"
POLL_URL = REQUEST_URL  # OpenAI uses the same endpoint for both phases.

# Both hints are sent so a single browser approval grants tokens for
# Codex + ChatGPT. The user can then ``custats login --provider codex``
# without re-authing because the same token is cached on the device.
_HINT = ("codex", "chatgpt")


async def begin(
    client: httpx.AsyncClient,
    *,
    on_poll: PollCallback | None = None,
) -> tuple[DeviceCodeRequest, DeviceCodeApproved]:
    """Run the full ChatGPT device-code flow. Returns (initial, final).

    Uses ``device_code_hint=["codex", "chatgpt"]`` so a single browser
    approval grants a token that works for both Codex + ChatGPT. The
    token is persisted to ``~/.chatgpt/auth.json`` by the CLI.

    The same Codex-CLI User-Agent + ``Client-Id`` headers are sent on
    every request (imported as :data:`custats.oauth.codex.CODEX_HEADERS`)
    — the OpenAI device-code endpoint is shared with Codex and only
    accepts these headers past Cloudflare's bot-management.
    """
    initial = await request_device_code(
        client=client,
        request_url=REQUEST_URL,
        payload={"device_code_hint": list(_HINT)},
        headers=CODEX_HEADERS,
    )
    final = await poll_device_code(
        client=client,
        poll_url=POLL_URL,
        payload={
            "device_id": initial.device_id,
            "user_code": initial.user_code,
        },
        on_poll=on_poll,
        interval_seconds=initial.interval_seconds,
        expires_in_seconds=initial.expires_in_seconds,
        headers=CODEX_HEADERS,
    )
    return initial, final


__all__ = [
    "POLL_URL",
    "REQUEST_URL",
    "begin",
]


# Re-export the typing alias used by the registry in providers.py.
# Defining it here (instead of in providers.py) keeps the public
# surface close to its single concrete user, mirroring codex.py.
BrowserBegin = Callable[
    [httpx.AsyncClient],
    Awaitable[tuple[DeviceCodeRequest, DeviceCodeApproved]],
]

_ = Any  # silence "Any imported but unused" — Any is used in BrowserBegin above