"""Tests for the GitHub Copilot OAuth device-code glue (Phase 8c).

Mirrors :mod:`tests.oauth.test_codex` and :mod:`tests.oauth.test_chatgpt_oauth`
— verifies the two-endpoint device-flow against ``github.com``, that the
public OAuth App ID is sent, that the response shapes GitHub uses
(``{"error": "authorization_pending"}`` for pending, full token bundle
on approval) are handled, and that the ``User-Agent`` header reaches
every request — GitHub requires it on every OAuth call.
"""

from __future__ import annotations

import json
from typing import Callable

import httpx
import pytest

from custats.oauth.copilot import (
    GITHUB_CLIENT_ID,
    GITHUB_DEVICE_REQUEST_URL,
    GITHUB_HEADERS,
    GITHUB_TOKEN_URL,
    begin,
)
from custats.oauth.device_code import DeviceCodeCancelled


def _make_client(
    handler: Callable[[httpx.Request], httpx.Response],
) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# ---------------------------------------------------------------------- #
# Constants / shape pinning
# ---------------------------------------------------------------------- #


def test_github_client_id_is_public_copilot_app() -> None:
    """Phase 8c: the client_id MUST be GitHub's public Copilot OAuth App ID.

    This value is the same one the official ``gh`` CLI uses for Copilot
    integrations. We hard-code it (rather than invent our own) because
    GitHub whitelists only known OAuth App IDs at the device-code
    endpoint — a random UUID would be rejected with HTTP 4xx.
    """
    assert GITHUB_CLIENT_ID == "Iv23li5gtHhsd3iHGyqx"


def test_github_endpoints_pin_to_github_com() -> None:
    """Defensive: the URL constants are exactly the documented endpoints."""
    assert GITHUB_DEVICE_REQUEST_URL == "https://github.com/login/device/code"
    assert GITHUB_TOKEN_URL == "https://github.com/login/oauth/access_token"


def test_github_headers_include_user_agent() -> None:
    """GitHub requires ``User-Agent`` on every OAuth call (their docs).

    Without it, GitHub returns HTTP 403 even for a valid client_id.
    We pin the header set so a future refactor can't silently drop it.
    """
    assert "User-Agent" in GITHUB_HEADERS
    assert GITHUB_HEADERS["User-Agent"] == "custats-cli/0.1.0"
    # And Accept is application/json so we never get HTML back as parsed JSON.
    assert GITHUB_HEADERS["Accept"] == "application/json"


# ---------------------------------------------------------------------- #
# Happy path
# ---------------------------------------------------------------------- #


async def test_copilot_begin_happy_path() -> None:
    """``begin()`` runs the two-step flow and returns the token bundle.

    GitHub's device-code flow distinguishes pending from approved by
    response shape (no ``status`` key, vs full token bundle). The generic
    :func:`poll_device_code` handles this via its "no status key = keep
    polling" branch — this test exercises that branch end-to-end.
    """
    call_count = 0
    seen_payloads: list[dict] = []

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # First call: device-code request — capture the body so we
            # can assert client_id + scope.
            seen_payloads.append(json.loads(req.content.decode("utf-8")))
            assert req.method == "POST"
            assert req.url.path == "/login/device/code"
            return httpx.Response(
                200,
                json={
                    "device_code": "dev-copilot-abc",
                    "user_code": "WXYZ-9876",
                    "verification_uri": "https://github.com/login/device",
                    "interval": 0,  # avoid sleeping between polls in tests
                    "expires_in": 60,
                },
            )
        if call_count == 2:
            return httpx.Response(
                200,
                json={"error": "authorization_pending"},
            )
        if call_count == 3:
            # Final approval: GitHub returns the full token bundle
            # (no ``status`` key — the absence-of-status IS the approval).
            return httpx.Response(
                200,
                json={
                    "access_token": "ghu_copilot_tok",
                    "refresh_token": "ghr_copilot_rt",
                    "token_type": "bearer",
                    "scope": "copilot read:user",
                    "expires_in": 28800,
                },
            )
        # Shouldn't be polled again.
        return httpx.Response(200, json={"error": "authorization_pending"})

    async with _make_client(handler) as client:
        initial, final = await begin(client)

    # Initial request: the captured payload must include the public
    # client_id AND the right scope.
    assert seen_payloads, "the device-code request was not observed"
    payload = seen_payloads[0]
    assert payload.get("client_id") == "Iv23li5gtHhsd3iHGyqx", (
        f"client_id drifted from GitHub's public Copilot App ID: "
        f"got {payload.get('client_id')!r}"
    )
    assert payload.get("scope") == "copilot read:user", (
        f"scope must be 'copilot read:user' (matches gh CLI); "
        f"got {payload.get('scope')!r}"
    )

    # Initial response values forwarded unchanged (interval=0 is
    # clamped to 1 by the helper — at least 1s between polls).
    assert initial.device_id == "dev-copilot-abc"
    assert initial.user_code == "WXYZ-9876"
    assert initial.verification_url == "https://github.com/login/device"
    assert initial.interval_seconds == 1
    assert initial.expires_in_seconds == 60

    # Final approval: token bundle carried through.
    assert final.raw["access_token"] == "ghu_copilot_tok"
    assert final.raw["refresh_token"] == "ghr_copilot_rt"
    assert final.raw["token_type"] == "bearer"
    assert final.raw["scope"] == "copilot read:user"


async def test_copilot_begin_propagates_cancellation() -> None:
    """If the ``on_poll`` callback cancels, ``begin()`` re-raises."""
    called = {"n": 0}

    def real_handler(req: httpx.Request) -> httpx.Response:
        called["n"] += 1
        if called["n"] == 1:
            return httpx.Response(
                200,
                json={
                    "device_code": "d",
                    "user_code": "U",
                    "verification_uri": "https://github.com/login/device",
                    "interval": 0,
                    "expires_in": 60,
                },
            )
        # Return pending on every subsequent call so the callback has
        # a chance to fire.
        return httpx.Response(200, json={"error": "authorization_pending"})

    def on_poll(_: dict) -> None:
        raise DeviceCodeCancelled("user cancelled copilot login")

    async with _make_client(real_handler) as client:
        with pytest.raises(DeviceCodeCancelled):
            await begin(client, on_poll=on_poll)


# ---------------------------------------------------------------------- #
# Header + payload verification
# ---------------------------------------------------------------------- #


async def test_copilot_begin_sends_user_agent_on_initial_request() -> None:
    """The initial request carries GitHub's required User-Agent header."""
    seen_headers: list[dict[str, str]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen_headers.append(dict(req.headers))
        if len(seen_headers) == 1:
            return httpx.Response(
                200,
                json={
                    "device_code": "d",
                    "user_code": "U",
                    "verification_uri": "https://github.com/login/device",
                    "interval": 0,
                    "expires_in": 60,
                },
            )
        return httpx.Response(
            200,
            json={
                "access_token": "ghu_x",
                "refresh_token": "ghr_x",
                "token_type": "bearer",
                "scope": "copilot read:user",
                "expires_in": 3600,
            },
        )

    async with _make_client(handler) as client:
        await begin(client)

    assert seen_headers[0].get("user-agent") == "custats-cli/0.1.0", (
        f"missing User-Agent on initial request: {seen_headers[0]!r}"
    )


async def test_copilot_poll_request_also_sends_user_agent() -> None:
    """Polling POSTs also carry the User-Agent header GitHub requires.

    GitHub's docs require User-Agent on every OAuth call — including
    the polling phase. A bare-httpx UA would be rejected mid-flow.
    """
    seen_headers: list[dict[str, str]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen_headers.append(dict(req.headers))
        if len(seen_headers) == 1:
            return httpx.Response(
                200,
                json={
                    "device_code": "d",
                    "user_code": "U",
                    "verification_uri": "https://github.com/login/device",
                    "interval": 0,
                    "expires_in": 60,
                },
            )
        return httpx.Response(
            200,
            json={
                "access_token": "ghu_x",
                "refresh_token": "ghr_x",
                "token_type": "bearer",
                "scope": "copilot read:user",
                "expires_in": 3600,
            },
        )

    async with _make_client(handler) as client:
        await begin(client)

    # Initial request + at least one poll request.
    assert len(seen_headers) >= 2, (
        f"expected initial + poll, got {len(seen_headers)} calls"
    )
    for idx, headers in enumerate(seen_headers):
        assert headers.get("user-agent") == "custats-cli/0.1.0", (
            f"call #{idx + 1} missing User-Agent: {headers!r}"
        )


async def test_copilot_poll_payload_includes_device_code_and_grant_type() -> None:
    """The polling body carries the canonical RFC 8628 grant-type URI.

    GitHub's token endpoint specifically requires the
    ``urn:ietf:params:oauth:grant-type:device_code`` grant type —
    using ``refresh_token`` here would be rejected even with a valid
    refresh_token in the body.
    """
    seen_payloads: list[dict] = []
    call_count = 0

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return httpx.Response(
                200,
                json={
                    "device_code": "dev-poll-123",
                    "user_code": "ABCD-1234",
                    "verification_uri": "https://github.com/login/device",
                    "interval": 0,
                    "expires_in": 60,
                },
            )
        seen_payloads.append(json.loads(req.content.decode("utf-8")))
        return httpx.Response(
            200,
            json={
                "access_token": "ghu_x",
                "refresh_token": "ghr_x",
            },
        )

    async with _make_client(handler) as client:
        await begin(client)

    assert seen_payloads, "the poll request was not observed"
    payload = seen_payloads[0]
    assert payload.get("grant_type") == (
        "urn:ietf:params:oauth:grant-type:device_code"
    ), f"wrong grant_type on poll: {payload!r}"
    assert payload.get("device_code") == "dev-poll-123"
    assert payload.get("client_id") == "Iv23li5gtHhsd3iHGyqx"
