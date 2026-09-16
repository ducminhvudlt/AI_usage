"""Tests for the generic OAuth device-code helper."""

from __future__ import annotations

from typing import Callable

import httpx
import pytest

from custats.oauth.device_code import (
    DeviceCodeCancelled,
    DeviceCodeError,
    poll_device_code,
    request_device_code,
)


def _make_client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# ---------------------------------------------------------------------- #
# request_device_code
# ---------------------------------------------------------------------- #


async def test_request_device_code_parses_typical_response() -> None:
    body = {
        "device_id": "dev-abc",
        "user_code": "ABCD-EFGH",
        "verification_url": "https://auth.openai.com/codex/device",
        "interval": 7,
        "expires_in": 900,
    }

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.method == "POST"
        assert req.url.path.endswith("/api/accounts/device/login")
        return httpx.Response(200, json=body)

    async with _make_client(handler) as client:
        req = await request_device_code(
            client=client,
            request_url="https://auth.openai.com/api/accounts/device/login",
            payload={"device_code_hint": ["codex"]},
        )

    assert req.device_id == "dev-abc"
    assert req.user_code == "ABCD-EFGH"
    assert req.verification_url == "https://auth.openai.com/codex/device"
    assert req.interval_seconds == 7
    assert req.expires_in_seconds == 900


async def test_request_device_code_handles_data_wrapper() -> None:
    """Some providers wrap responses in ``{"data": ...}`` — we unwrap."""
    wrapped = {
        "data": {
            "device_id": "dev-xyz",
            "user_code": "WXYZ-1234",
            "verification_url": "https://example.com/device",
            # No interval / expires_in — defaults should kick in.
        }
    }

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=wrapped)

    async with _make_client(handler) as client:
        req = await request_device_code(
            client=client,
            request_url="https://example.com/oauth/device",
            payload={},
        )

    assert req.device_id == "dev-xyz"
    assert req.user_code == "WXYZ-1234"
    assert req.interval_seconds == 5  # default
    assert req.expires_in_seconds == 600  # default


async def test_request_device_code_http_error_raises() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    async with _make_client(handler) as client:
        with pytest.raises(DeviceCodeError) as ei:
            await request_device_code(
                client=client,
                request_url="https://example.com/oauth/device",
                payload={},
            )
    assert "500" in str(ei.value)


async def test_request_device_code_missing_field_raises() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"device_id": "x"})  # no user_code etc.

    async with _make_client(handler) as client:
        with pytest.raises(DeviceCodeError) as ei:
            await request_device_code(
                client=client,
                request_url="https://example.com/oauth/device",
                payload={},
            )
    assert "user_code" in str(ei.value)


async def test_request_device_code_passes_through_caller_headers() -> None:
    """Generic helper: caller-supplied headers reach the request.

    Per-provider code uses this to inject Cloudflare-friendly headers
    (e.g. ``codex-cli/0.50.0`` + ``Client-Id``) without the helper
    knowing anything about a specific provider's UA.
    """
    seen_headers: dict[str, str] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        # Capture all headers the helper actually sent.
        seen_headers.update(dict(req.headers))
        return httpx.Response(
            200,
            json={
                "device_id": "d",
                "user_code": "U",
                "verification_url": "https://example.com/device",
                "interval": 5,
                "expires_in": 600,
            },
        )

    caller_headers = {
        "User-Agent": "codex-cli/0.50.0",
        "Client-User-Agent": "codex-cli/0.50.0",
        "Client-Id": "app_EMoamEEZ73f0CkXaXp7hrann",
        "Origin": "https://chatgpt.com",
        "Referer": "https://chatgpt.com/",
    }

    async with _make_client(handler) as client:
        await request_device_code(
            client=client,
            request_url="https://example.com/oauth/device",
            payload={"device_code_hint": ["codex"]},
            headers=caller_headers,
        )

    # Every caller-supplied header reached the wire. httpx normalises
    # header names to lowercase on the wire, so look up with the same
    # case to read back the values we sent.
    wire_lookup = {
        "user-agent": "User-Agent",
        "client-user-agent": "Client-User-Agent",
        "client-id": "Client-Id",
        "origin": "Origin",
        "referer": "Referer",
    }
    for wire_name, orig_name in wire_lookup.items():
        assert seen_headers.get(wire_name) == caller_headers[orig_name], (
            f"caller header {orig_name!r} was dropped or rewritten: "
            f"sent {caller_headers[orig_name]!r}, "
            f"got {seen_headers.get(wire_name)!r}"
        )
    # Content-Type default is still applied even when the caller
    # supplies its own headers (mirrors the helper's contract).
    assert seen_headers.get("content-type") == "application/json"


# ---------------------------------------------------------------------- #
# Cloudflare interstitial detection (Phase 7f)
# ---------------------------------------------------------------------- #


# Realistic Cloudflare interstitial body — abbreviated but contains the
# canonical DOCTYPE + title so the helper's three-condition check fires.
_CLOUDFLARE_INTERSTITIAL = (
    "<!DOCTYPE html>\n"
    "<html lang=\"en-US\">\n"
    "<head>\n"
    "    <title>Just a moment\u2026</title>\n"
    "    <meta charset=\"utf-8\">\n"
    "</head>\n"
    "<body>\n"
    "    <h1 data-translate=\"challenge_headline\">One more step</h1>\n"
    "</body>\n"
    "</html>\n"
)


async def test_request_device_code_cloudflare_403_raises_with_cloudflare_hint() -> (
    None
):
    """Cloudflare's JS-challenge body must surface a recognisable error.

    The 403 + HTML + ``Just a moment\u2026`` title is Cloudflare's
    bot-management interstitial. The CLI greps the error message for
    a Cloudflare marker so it can offer the cookie-paste workaround;
    if the marker is missing, the user sees a generic HTTP error
    instead of the right hint.
    """
    import re

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text=_CLOUDFLARE_INTERSTITIAL)

    async with _make_client(handler) as client:
        with pytest.raises(DeviceCodeError) as ei:
            await request_device_code(
                client=client,
                request_url="https://auth.openai.com/api/accounts/device/login",
                payload={"device_code_hint": ["codex"]},
            )

    # Case-insensitive marker — the CLI greps for the literal substring.
    msg = str(ei.value)
    assert "Cloudflare" in msg, (
        f"DeviceCodeError message missing Cloudflare marker: {msg!r}"
    )
    # And the canonical title is referenced in the message so the
    # user / on-call engineer can grep for it in the wild.
    assert re.search(r"Just a moment", msg, re.IGNORECASE), (
        f"DeviceCodeError message doesn't reference the Cloudflare title: {msg!r}"
    )


async def test_request_device_code_non_cloudflare_403_raises_plain_error() -> None:
    """A legitimate 403 with a JSON body must NOT be misclassified as Cloudflare.

    Detection requires ALL THREE conditions (status=403, body starts
    with ``<!DOCTYPE html``, body contains the canonical Cloudflare
    title). If any one is missing we fall back to the original plain
    ``HTTP 403 ...`` message so users aren't misled into the
    cookie-paste workaround when the real fix is something else.
    """
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            json={"error": "invalid_request", "error_description": "..."},
        )

    async with _make_client(handler) as client:
        with pytest.raises(DeviceCodeError) as ei:
            await request_device_code(
                client=client,
                request_url="https://example.com/oauth/device",
                payload={},
            )

    msg = str(ei.value)
    assert "Cloudflare" not in msg, (
        f"non-Cloudflare 403 was misclassified as Cloudflare: {msg!r}"
    )
    # The original generic format is preserved.
    assert "403" in msg
    assert "invalid_request" in msg or "{" in msg  # repr(...) of the JSON body


# ---------------------------------------------------------------------- #
# poll_device_code
# ---------------------------------------------------------------------- #


async def test_poll_returns_on_status_ok() -> None:
    seen_calls: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen_calls.append(req.url.path)
        return httpx.Response(
            200,
            json={
                "status": "ok",
                "authorization_code": "ac-123",
                "code_verifier": "cv-456",
            },
        )

    async with _make_client(handler) as client:
        result = await poll_device_code(
            client=client,
            poll_url="https://example.com/poll",
            payload={"device_id": "d", "user_code": "u"},
            interval_seconds=0,
            expires_in_seconds=60,
        )

    assert result.raw["status"] == "ok"
    assert result.raw["authorization_code"] == "ac-123"
    assert len(seen_calls) == 1


async def test_poll_returns_on_status_approved() -> None:
    """Some providers use ``approved`` instead of RFC 8628's ``ok``."""

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"status": "approved", "authorization_code": "ac-1"},
        )

    async with _make_client(handler) as client:
        result = await poll_device_code(
            client=client,
            poll_url="https://example.com/poll",
            payload={},
            interval_seconds=0,
            expires_in_seconds=60,
        )

    assert result.raw["authorization_code"] == "ac-1"


async def test_poll_returns_on_status_success() -> None:
    """``success`` is also accepted as an alias (some OAuth servers)."""

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "success"})

    async with _make_client(handler) as client:
        result = await poll_device_code(
            client=client,
            poll_url="https://example.com/poll",
            payload={},
            interval_seconds=0,
            expires_in_seconds=60,
        )
    assert result.raw["status"] == "success"


async def test_poll_continues_on_pending() -> None:
    responses = [
        {"status": "authorization_pending"},
        {"status": "pending"},  # alias
        {"status": "ok", "authorization_code": "ac-final"},
    ]

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=responses.pop(0))

    async with _make_client(handler) as client:
        result = await poll_device_code(
            client=client,
            poll_url="https://example.com/poll",
            payload={},
            interval_seconds=0,
            expires_in_seconds=60,
        )

    assert result.raw["authorization_code"] == "ac-final"
    assert responses == []


async def test_poll_increases_interval_on_slow_down() -> None:
    """``slow_down`` must add 5 s to the poll interval — verified by
    observing ``asyncio.sleep`` calls via monkeypatch.
    """
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    responses = [
        {"status": "slow_down"},
        {"status": "slow_down"},
        {"status": "ok"},
    ]

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=responses.pop(0))

    import custats.oauth.device_code as dc

    real_sleep = dc.asyncio.sleep
    dc.asyncio.sleep = fake_sleep  # type: ignore[assignment]
    try:
        async with _make_client(handler) as client:
            await poll_device_code(
                client=client,
                poll_url="https://example.com/poll",
                payload={},
                interval_seconds=2,
                expires_in_seconds=60,
            )
    finally:
        dc.asyncio.sleep = real_sleep  # type: ignore[assignment]

    # First slow_down → 2+5=7; second → 7+5=12.
    # Plus the post-approval loop never sleeps (returns immediately).
    assert 7 in sleeps
    assert 12 in sleeps


async def test_poll_raises_on_access_denied() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "access_denied"})

    async with _make_client(handler) as client:
        with pytest.raises(DeviceCodeError) as ei:
            await poll_device_code(
                client=client,
                poll_url="https://example.com/poll",
                payload={},
                interval_seconds=0,
                expires_in_seconds=60,
            )
    assert "access_denied" in str(ei.value)


async def test_poll_raises_on_expired_token() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "expired_token"})

    async with _make_client(handler) as client:
        with pytest.raises(DeviceCodeError) as ei:
            await poll_device_code(
                client=client,
                poll_url="https://example.com/poll",
                payload={},
                interval_seconds=0,
                expires_in_seconds=60,
            )
    assert "expired_token" in str(ei.value)


async def test_poll_raises_on_unrecognised_status() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "wat"})

    async with _make_client(handler) as client:
        with pytest.raises(DeviceCodeError) as ei:
            await poll_device_code(
                client=client,
                poll_url="https://example.com/poll",
                payload={},
                interval_seconds=0,
                expires_in_seconds=60,
            )
    assert "wat" in str(ei.value)


async def test_poll_cancelled_by_callback() -> None:
    """The injected ``on_poll`` callback can abort polling cleanly."""

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "authorization_pending"})

    def on_poll(body: dict) -> None:
        raise DeviceCodeCancelled("user clicked cancel")

    async with _make_client(handler) as client:
        with pytest.raises(DeviceCodeCancelled):
            await poll_device_code(
                client=client,
                poll_url="https://example.com/poll",
                payload={},
                on_poll=on_poll,
                interval_seconds=0,
                expires_in_seconds=60,
            )


async def test_poll_retries_transient_network_error_twice_then_raises() -> None:
    """Two transient ``httpx`` errors should be retried; the third raises."""

    call_count = 0

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        raise httpx.ConnectError(f"simulated network failure #{call_count}")

    async with _make_client(handler) as client:
        with pytest.raises(DeviceCodeError) as ei:
            await poll_device_code(
                client=client,
                poll_url="https://example.com/poll",
                payload={},
                interval_seconds=0,
                expires_in_seconds=60,
            )

    # Three attempts total (2 retries + the failing third).
    assert call_count == 3
    assert "transport error" in str(ei.value).lower()


async def test_poll_recovers_after_transient_network_errors() -> None:
    """Two transient errors followed by an ``ok`` should succeed."""
    call_count = 0

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count <= 2:
            raise httpx.ReadTimeout(f"flaky #{call_count}")
        return httpx.Response(200, json={"status": "ok"})

    async with _make_client(handler) as client:
        result = await poll_device_code(
            client=client,
            poll_url="https://example.com/poll",
            payload={},
            interval_seconds=0,
            expires_in_seconds=60,
        )
    assert result.raw["status"] == "ok"
    assert call_count == 3


async def test_poll_times_out_when_expiry_reached() -> None:
    """If the deadline passes, raise a ``DeviceCodeError`` rather than hang."""

    async def fake_sleep(_: float) -> None:
        # Never actually sleep — simulate instant time passing so the
        # deadline check fires on the first iteration.
        pass

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "authorization_pending"})

    import custats.oauth.device_code as dc

    real_sleep = dc.asyncio.sleep
    dc.asyncio.sleep = fake_sleep  # type: ignore[assignment]
    try:
        async with _make_client(handler) as client:
            with pytest.raises(DeviceCodeError) as ei:
                await poll_device_code(
                    client=client,
                    poll_url="https://example.com/poll",
                    payload={},
                    interval_seconds=0,
                    expires_in_seconds=0,  # expires immediately
                    timeout_seconds=0.0,
                )
        assert "expired" in str(ei.value).lower()
    finally:
        dc.asyncio.sleep = real_sleep  # type: ignore[assignment]
