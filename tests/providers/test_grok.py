"""Tests for the Grok adapter."""

from __future__ import annotations

import httpx
import pytest

from custats.core.models import Provider
from custats.providers.grok import (
    ACCESS_TOKEN_KEY,
    GrokAdapter,
    REFRESH_TOKEN_KEY,
    USAGE_URL,
)
from custats.providers.base import AuthError, RateLimitError


def _make_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://api.x.ai"
    )


def _body(
    *,
    util_5h: float = 0.10,
    reset_5h: str = "2026-09-15T20:00:00Z",
    util_7d: float = 0.20,
    reset_7d: str = "2026-09-22T20:00:00Z",
    window: str | None = "weekly",
):
    out: dict[str, object] = {
        "five_hour": {"utilization": util_5h, "resets_at": reset_5h},
        "seven_day": {"utilization": util_7d, "resets_at": reset_7d},
    }
    if window is not None:
        out["window"] = window
    return out


async def test_weekly_window() -> None:
    seen_headers: dict[str, str] = {}
    body = _body(window="weekly")

    def handler(req: httpx.Request) -> httpx.Response:
        seen_headers.update(req.headers)
        return httpx.Response(200, json=body)

    creds = {ACCESS_TOKEN_KEY: "tok-abc", REFRESH_TOKEN_KEY: "rt-old"}
    async with _make_client(handler) as client:
        u = await GrokAdapter().fetch(creds, client=client)
    assert u.provider is Provider.GROK
    assert u.five_hour is not None
    assert u.five_hour.five_hour_percent == pytest.approx(10.0)
    assert u.five_hour.grok_window == "weekly"
    assert seen_headers["authorization"] == "Bearer tok-abc"
    # Adapter must never write back to the credentials dict.
    assert creds[REFRESH_TOKEN_KEY] == "rt-old"


async def test_monthly_window() -> None:
    body = _body(window="monthly")

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    async with _make_client(handler) as client:
        u = await GrokAdapter().fetch({ACCESS_TOKEN_KEY: "tok"}, client=client)
    assert u.five_hour is not None
    assert u.five_hour.grok_window == "monthly"


async def test_unknown_window_becomes_none() -> None:
    """Anything that isn't ``weekly`` or ``monthly`` is normalised to None
    so the Literal type in ``ProviderLimits.grok_window`` doesn't explode.
    """
    body = _body(window="biweekly")

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    async with _make_client(handler) as client:
        u = await GrokAdapter().fetch({ACCESS_TOKEN_KEY: "tok"}, client=client)
    assert u.five_hour is not None
    assert u.five_hour.grok_window is None


async def test_refresh_token_ignored_even_when_server_returns_new_one() -> None:
    """The Grok API may return a refreshed ``refresh_token`` in some
    responses. custats must NOT pick that up; it would silently
    override whatever the user has on disk.

    We verify by ensuring the local credentials dict never gets new
    keys from the adapter path (no internal mutation).
    """
    body = _body(window="weekly")
    body["refresh_token"] = "new-rotated-token"

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    creds = {ACCESS_TOKEN_KEY: "tok", REFRESH_TOKEN_KEY: "old"}
    async with _make_client(handler) as client:
        await GrokAdapter().fetch(creds, client=client)
    assert creds[REFRESH_TOKEN_KEY] == "old"


async def test_missing_access_token_raises_auth() -> None:
    async with _make_client(lambda req: httpx.Response(200)) as client:
        with pytest.raises(AuthError):
            await GrokAdapter().fetch({}, client=client)


async def test_401_raises_auth_error() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(401)

    async with _make_client(handler) as client:
        with pytest.raises(AuthError):
            await GrokAdapter().fetch({ACCESS_TOKEN_KEY: "tok"}, client=client)


async def test_429_maps_to_rate_limit() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "15"})

    async with _make_client(handler) as client:
        with pytest.raises(RateLimitError) as ei:
            await GrokAdapter().fetch({ACCESS_TOKEN_KEY: "tok"}, client=client)
    assert ei.value.retry_after_seconds == 15.0


async def test_describe_credential_mentions_no_rotation() -> None:
    text = GrokAdapter().describe_credential().lower()
    assert "never" in text or "read-only" in text or "rotated" in text


async def test_url_constant_matches_documented_endpoint() -> None:
    assert "x.ai" in USAGE_URL
    assert "/usage" in USAGE_URL