"""Codex-specific device-code glue.

OpenAI's auth endpoint is the only public OAuth device-code endpoint we
support today. The flow:

1. ``POST https://auth.openai.com/api/accounts/device/login``
   body: ``{"device_code_hint": ["codex"]}``
   → ``{"device_id": ..., "user_code": "ABCD-EFGH",
       "verification_url": "https://auth.openai.com/codex/device",
       "interval": 5, "expires_in": 600}``

2. Poll the same endpoint with body
   ``{"device_id": ..., "user_code": "..."}`` until
   ``{"status": "ok", "authorization_code": "...", "code_verifier": "..."}``
   or pending.

The ``hint`` parameter on :func:`begin` scopes the returned token to a
specific OpenAI product via the ``device_code_hint`` payload. Codex
defaults to ``("codex",)``; the ChatGPT module reuses this same endpoint
with ``("codex", "chatgpt")`` so a single login grants both products.

The returned ``authorization_code`` + ``code_verifier`` are exchanged at
the existing OAuth token endpoint to produce the same JSON blob
``codex login`` stores in ``~/.codex/auth.json``. For Phase 7a we don't
implement the code-for-token exchange (it's the same handler
``codex login`` uses and is out of scope); instead we hand the raw
approval dict back to the caller and document that Phase 7b-or-later
should plug in the token exchange.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Sequence

import httpx

from .device_code import (
    DeviceCodeApproved,
    DeviceCodeRequest,
    poll_device_code,
    request_device_code,
)
from .refresh import OPENAI_REFRESH_CLIENT_ID


REQUEST_URL = "https://auth.openai.com/api/accounts/device/login"
POLL_URL = REQUEST_URL  # OpenAI uses the same endpoint for both phases.

# Match the official ``codex-cli`` (Rust) User-Agent string format. The
# auth endpoint sits behind Cloudflare's bot-management; a bare ``httpx``
# POST gets a JS-challenge interstitial (HTTP 403 + HTML body) instead
# of the JSON we need. Sending these specific headers gets Cloudflare to
# recognise us as the Codex CLI it already whitelists.
#
# Bumping the version is safe — Cloudflare just needs *some* plausible
# Codex version; older versions are not blocked. ``0.50.0`` is a
# current released version string. OpenAI ships codex-cli releases
# roughly monthly; if Cloudflare ever starts rejecting ``0.50.0``,
# bump to whatever is current — check
# https://github.com/openai/codex/releases.
CODEX_CLI_VERSION = "0.50.0"
CODEX_CLI_USER_AGENT = f"codex-cli/{CODEX_CLI_VERSION}"
CODEX_CLIENT_ID = OPENAI_REFRESH_CLIENT_ID  # same value as oauth/refresh.py

# Headers the Codex CLI sends to https://auth.openai.com. Both the
# initial request and every poll must carry them, otherwise Cloudflare
# starts blocking the polls partway through the flow. The Accept /
# Accept-Language / Accept-Encoding trio mimics a real browser — cheap
# to add and sometimes the difference between Cloudflare letting the
# request through or not.
CODEX_HEADERS: dict[str, str] = {
    "User-Agent": CODEX_CLI_USER_AGENT,
    "Client-User-Agent": CODEX_CLI_USER_AGENT,
    "Client-Id": CODEX_CLIENT_ID,
    "Origin": "https://chatgpt.com",
    "Referer": "https://chatgpt.com/",
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
}

# Type alias for the per-provider ``begin`` function: an async coroutine
# taking a client + on_poll callback and returning the (initial, final)
# pair. Mirrored in :mod:`custats.oauth.providers` for the registry.
PollCallback = Callable[[dict[str, Any]], None]


async def begin(
    client: httpx.AsyncClient,
    *,
    hint: Sequence[str] = ("codex",),
    on_poll: PollCallback | None = None,
) -> tuple[DeviceCodeRequest, DeviceCodeApproved]:
    """Run the full Codex device-code flow. Returns (initial, final).

    ``hint`` is forwarded to the OpenAI endpoint as the
    ``device_code_hint`` payload — see
    https://auth.openai.com/api/accounts/device/login. The default
    ``("codex",)`` requests a Codex-only token (Phase 7a behaviour);
    pass ``("codex", "chatgpt")`` to request a token that works for both
    products (used by :mod:`custats.oauth.chatgpt`).

    :data:`CODEX_HEADERS` (the Codex-CLI User-Agent + ``Client-Id``)
    are sent on every request — see the module docstring of
    :mod:`custats.oauth.device_code` for why ``headers`` is plumbed
    through the generic helper rather than hard-coded here.
    """
    initial = await request_device_code(
        client=client,
        request_url=REQUEST_URL,
        payload={"device_code_hint": list(hint)},
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
    "CODEX_CLIENT_ID",
    "CODEX_CLI_USER_AGENT",
    "CODEX_CLI_VERSION",
    "CODEX_HEADERS",
    "POLL_URL",
    "REQUEST_URL",
    "begin",
]


# Re-export the typing alias used by the registry in providers.py.
# Defining it here (instead of in providers.py) keeps the public
# surface close to its single concrete user.
BrowserBegin = Callable[
    [httpx.AsyncClient],
    Awaitable[tuple[DeviceCodeRequest, DeviceCodeApproved]],
]
