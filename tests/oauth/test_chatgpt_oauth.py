"""Tests for the ChatGPT-specific OAuth device-code glue (Phase 7d).

Mirrors :mod:`tests.oauth.test_codex` — same OpenAI endpoint, broader
``device_code_hint`` so a single login grants tokens for both Codex and
ChatGPT.
"""

from __future__ import annotations

import json
from typing import Callable

import httpx
import pytest

from custats.oauth.chatgpt import POLL_URL, REQUEST_URL, begin
from custats.oauth.device_code import DeviceCodeCancelled


def _make_client(
    handler: Callable[[httpx.Request], httpx.Response],
) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_chatgpt_begin_happy_path() -> None:
    """begin() returns the approval dict AND the device-code request payload
    advertises both ``codex`` and ``chatgpt`` in ``device_code_hint``."""

    call_count = 0
    seen_payloads: list[dict] = []

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # First call: device-code request — capture the body so we can
            # assert the hint is broad.
            seen_payloads.append(json.loads(req.content.decode("utf-8")))
            assert req.method == "POST"
            assert req.url.path.endswith("/api/accounts/device/login")
            return httpx.Response(
                200,
                json={
                    "device_id": "dev-chatgpt",
                    "user_code": "WXYZ-1234",
                    "verification_url": "https://auth.openai.com/codex/device",
                    "interval": 5,
                    "expires_in": 600,
                },
            )
        if call_count == 2:
            return httpx.Response(200, json={"status": "authorization_pending"})
        if call_count == 3:
            return httpx.Response(
                200,
                json={
                    "status": "ok",
                    "authorization_code": "ac-chatgpt-123",
                    "code_verifier": "cv-chatgpt-456",
                    "access_token": "oa-chatgpt",
                    "refresh_token": "or-chatgpt",
                },
            )
        # Shouldn't be polled again.
        return httpx.Response(200, json={"status": "ok"})

    async with _make_client(handler) as client:
        initial, final = await begin(client)

    # First call: hint must include both codex AND chatgpt so a single
    # browser approval grants tokens for both products.
    assert seen_payloads, "the device-code request was not observed"
    hint = seen_payloads[0].get("device_code_hint")
    assert "codex" in (hint or []), (
        f"chatgpt path must keep 'codex' in the hint for parity; got {hint!r}"
    )
    assert "chatgpt" in (hint or []), (
        f"chatgpt path must include 'chatgpt' in the hint; got {hint!r}"
    )

    # Approval dict carries the OAuth tokens.
    assert initial.user_code == "WXYZ-1234"
    assert initial.device_id == "dev-chatgpt"
    assert initial.verification_url == "https://auth.openai.com/codex/device"
    assert final.raw["authorization_code"] == "ac-chatgpt-123"
    assert final.raw["code_verifier"] == "cv-chatgpt-456"
    assert final.raw["status"] == "ok"
    assert final.raw["access_token"] == "oa-chatgpt"


async def test_chatgpt_begin_propagates_cancellation() -> None:
    """If the on_poll callback cancels, begin() re-raises."""

    call_count = 0

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return httpx.Response(
                200,
                json={
                    "device_id": "d",
                    "user_code": "U",
                    "verification_url": "https://auth.openai.com/codex/device",
                    "interval": 5,
                    "expires_in": 600,
                },
            )
        return httpx.Response(200, json={"status": "pending"})

    def on_poll(_: dict) -> None:
        raise DeviceCodeCancelled("user cancelled chatgpt")

    async with _make_client(handler) as client:
        with pytest.raises(DeviceCodeCancelled):
            await begin(client, on_poll=on_poll)


async def test_chatgpt_endpoint_pinned() -> None:
    """Defensive: the URL constants are exactly the documented endpoints."""
    assert REQUEST_URL == "https://auth.openai.com/api/accounts/device/login"
    assert POLL_URL == REQUEST_URL


async def test_chatgpt_begin_sends_cli_user_agent() -> None:
    """Phase 7e: the chatgpt flow also sends the Codex-CLI headers.

    The chatgpt provider hits the same ``auth.openai.com`` endpoint as
    codex; Cloudflare's bot-management recognises the request as a
    legitimate Codex CLI only when these specific headers are present.
    We re-use :data:`CODEX_HEADERS` rather than defining a parallel
    ``CHATGPT_HEADERS`` dict so the two flows stay in sync — if a
    future refactor needs to diverge them, this test will fail loudly
    rather than silently regress to bare-``httpx`` blocks.
    """
    seen_headers: list[dict[str, str]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen_headers.append(dict(req.headers))
        if len(seen_headers) == 1:
            return httpx.Response(
                200,
                json={
                    "device_id": "d",
                    "user_code": "U",
                    "verification_url": "https://auth.openai.com/codex/device",
                    "interval": 0,
                    "expires_in": 60,
                },
            )
        return httpx.Response(
            200,
            json={
                "status": "ok",
                "authorization_code": "ac",
                "code_verifier": "cv",
            },
        )

    async with _make_client(handler) as client:
        await begin(client)

    # Initial request + at least one poll request were made; both
    # carry the same Cloudflare-friendly fingerprint.
    assert len(seen_headers) >= 2, (
        f"expected initial + poll, got {len(seen_headers)} calls"
    )

    expected_headers = {
        "user-agent": "codex-cli/0.50.0",
        "client-user-agent": "codex-cli/0.50.0",
        "client-id": "app_EMoamEEZ73f0CkXaXp7hrann",
        "origin": "https://chatgpt.com",
        "referer": "https://chatgpt.com/",
    }
    for idx, headers in enumerate(seen_headers):
        for name, value in expected_headers.items():
            assert headers.get(name) == value, (
                f"chatgpt call #{idx + 1} is missing/wrong {name!r}: "
                f"expected {value!r}, got {headers.get(name)!r}"
            )