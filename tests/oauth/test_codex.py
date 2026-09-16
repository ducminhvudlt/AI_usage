"""Tests for the Codex-specific OAuth device-code glue."""

from __future__ import annotations

import json
from typing import Callable

import httpx
import pytest

from custats.oauth.codex import (
    CODEX_CLI_USER_AGENT,
    CODEX_HEADERS,
    POLL_URL,
    REQUEST_URL,
    begin,
)
from custats.oauth.device_code import DeviceCodeCancelled


def _make_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_codex_begin_happy_path() -> None:
    """begin() runs the two-step flow and returns the approval dict."""

    call_count = 0

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # First call: device-code request.
            assert req.method == "POST"
            assert req.url.path.endswith("/api/accounts/device/login")
            return httpx.Response(
                200,
                json={
                    "device_id": "dev-codex",
                    "user_code": "ABCD-EFGH",
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
                    "authorization_code": "ac-codex-123",
                    "code_verifier": "cv-codex-456",
                },
            )
        # Shouldn't be polled again.
        return httpx.Response(200, json={"status": "ok"})

    async with _make_client(handler) as client:
        initial, final = await begin(client)

    # First call had the device-code request payload.
    assert initial.user_code == "ABCD-EFGH"
    assert initial.device_id == "dev-codex"
    assert initial.verification_url == "https://auth.openai.com/codex/device"
    # Final approval carries the OAuth tokens.
    assert final.raw["authorization_code"] == "ac-codex-123"
    assert final.raw["code_verifier"] == "cv-codex-456"
    assert final.raw["status"] == "ok"


async def test_codex_begin_propagates_cancellation() -> None:
    """If the on_poll callback cancels, begin() re-raises."""

    called = {"n": 0}

    def real_handler(req: httpx.Request) -> httpx.Response:
        called["n"] += 1
        if called["n"] == 1:
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
        raise DeviceCodeCancelled("user cancelled")

    async with _make_client(real_handler) as client:
        with pytest.raises(DeviceCodeCancelled):
            await begin(client, on_poll=on_poll)


async def test_codex_endpoints_point_to_openai() -> None:
    """Defensive: the URL constants are exactly the documented endpoints."""
    assert REQUEST_URL == "https://auth.openai.com/api/accounts/device/login"
    assert POLL_URL == REQUEST_URL


async def test_codex_begin_does_not_include_chatgpt_hint() -> None:
    """Regression for Phase 7d: the Codex path must NOT broaden its
    ``device_code_hint`` to include ``"chatgpt"``. Otherwise a Codex-only
    login would accidentally widen the granted scope.
    """
    seen_payloads: list[dict] = []
    call_count = 0

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            seen_payloads.append(json.loads(req.content.decode("utf-8")))
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

    assert seen_payloads, "the device-code request was not observed"
    payload = seen_payloads[0]
    hint = payload.get("device_code_hint")
    # Phase 7a strict: exactly ["codex"], nothing more.
    assert hint == ["codex"], (
        f"codex path leaked a broader hint: {hint!r} "
        "(expected ['codex'] only — ChatGPT login uses a broader hint)"
    )
    # Belt-and-braces: also assert no element of the hint is "chatgpt".
    assert "chatgpt" not in (hint or [])


# ---------------------------------------------------------------------- #
# Phase 7e: Cloudflare-friendly Codex-CLI headers
# ---------------------------------------------------------------------- #


def _capture_all_headers() -> tuple[
    Callable[[httpx.Request], httpx.Response],
    list[dict[str, str]],
]:
    """Build a mock handler that records headers from every request.

    Returns ``(handler, seen_header_lists)``. The handler responds
    with a successful device-code request on the first call and a
    successful poll (``status: ok``) on every subsequent call —
    enough to drive ``begin()`` to completion in one poll.
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

    return handler, seen_headers


async def test_codex_begin_sends_cli_user_agent() -> None:
    """The Codex-CLI User-Agent + Client-Id reach the initial request.

    Cloudflare's bot-management on auth.openai.com rejects a bare
    ``httpx`` UA with a JS-challenge (HTTP 403 + HTML). The official
    Codex CLI is whitelisted; we impersonate it via these headers.
    """
    handler, seen_headers = _capture_all_headers()

    async with _make_client(handler) as client:
        await begin(client)

    initial_headers = seen_headers[0]
    # httpx normalises header names to lowercase on the wire; look up
    # with the same case to read back the values we sent.
    assert initial_headers.get("user-agent") == CODEX_CLI_USER_AGENT
    assert initial_headers.get("user-agent") == "codex-cli/0.50.0"
    assert initial_headers.get("client-user-agent") == "codex-cli/0.50.0"
    assert initial_headers.get("client-id") == "app_EMoamEEZ73f0CkXaXp7hrann"
    # Sanity: the exact same dict is exported by the module — if a
    # future refactor drifts, this assertion catches it before the
    # Cloudflare block returns in production.
    assert CODEX_HEADERS["User-Agent"] == CODEX_CLI_USER_AGENT
    assert CODEX_HEADERS["Client-Id"] == "app_EMoamEEZ73f0CkXaXp7hrann"


async def test_codex_begin_sends_origin_and_referer() -> None:
    """Origin/Referer headers are set so Cloudflare sees a same-origin POST.

    The Codex CLI browser flow runs at chatgpt.com; setting these
    headers on the auth.openai.com cross-origin POST is what makes
    Cloudflare treat it as a legitimate first-party request.
    """
    handler, seen_headers = _capture_all_headers()

    async with _make_client(handler) as client:
        await begin(client)

    initial_headers = seen_headers[0]
    assert initial_headers.get("origin") == "https://chatgpt.com"
    assert initial_headers.get("referer", "").startswith("https://chatgpt.com")


async def test_poll_request_also_sends_cli_headers() -> None:
    """Polling POSTs carry the same Cloudflare-friendly headers.

    Cloudflare starts blocking mid-flow if the second request lacks
    the same fingerprint — every poll must include them.
    """
    handler, seen_headers = _capture_all_headers()

    async with _make_client(handler) as client:
        await begin(client)

    # Initial request + at least one poll request were made.
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
                f"call #{idx + 1} is missing/wrong {name!r}: "
                f"expected {value!r}, got {headers.get(name)!r}"
            )


async def test_codex_headers_constant_has_expected_keys() -> None:
    """``CODEX_HEADERS`` is the source of truth — it must contain exactly
    the five header names Cloudflare's bot-management recognises.

    This mirrors the spec's acceptance smoke command:
    ``python -c "from custats.oauth.codex import CODEX_HEADERS, ...;
                  print(sorted(CODEX_HEADERS.keys()))"``
    If a future refactor drops one of these names, Cloudflare silently
    starts blocking the request again and this test fails first.
    """
    expected_keys = {
        "User-Agent",
        "Client-User-Agent",
        "Client-Id",
        "Origin",
        "Referer",
        "Accept",
        "Accept-Language",
        "Accept-Encoding",
    }
    assert set(CODEX_HEADERS.keys()) == expected_keys, (
        f"CODEX_HEADERS keys drifted: "
        f"expected {sorted(expected_keys)}, "
        f"got {sorted(CODEX_HEADERS.keys())}"
    )
    # And the values are still wired to the constants — belt-and-braces.
    assert CODEX_HEADERS["User-Agent"] == CODEX_CLI_USER_AGENT
    assert CODEX_HEADERS["Client-User-Agent"] == CODEX_CLI_USER_AGENT
    # Browser-like Accept headers are the cheap additions Phase 7e made —
    # if a future refactor drops them, Cloudflare starts blocking again.
    assert CODEX_HEADERS["Accept"] == "application/json"
    assert CODEX_HEADERS["Accept-Language"] == "en-US,en;q=0.9"
    assert CODEX_HEADERS["Accept-Encoding"] == "gzip, deflate, br"
