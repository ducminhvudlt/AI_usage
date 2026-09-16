"""Tests for the OpenRouter adapter."""

from __future__ import annotations

import httpx
import pytest

from custats.core.models import Provider
from custats.providers.base import AuthError, ProviderUnavailable, RateLimitError
from custats.providers.openrouter import API_KEY_KEY, OpenRouterAdapter, USAGE_URL


def _make_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://openrouter.ai"
    )


def _body(
    *,
    usage_cents: float = 250,
    limit_cents: float = 1000,
    data_wrapper: bool = True,
) -> dict:
    payload = {"usage": usage_cents, "limit": limit_cents}
    return {"data": payload} if data_wrapper else payload


async def test_parses_response() -> None:
    seen_headers: dict[str, str] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen_headers.update(req.headers)
        return httpx.Response(200, json=_body())

    async with _make_client(handler) as client:
        u = await OpenRouterAdapter().fetch(
            {API_KEY_KEY: "sk-or-v1-fake"}, client=client
        )

    assert u.provider is Provider.OPENROUTER
    # Usage / limit are in cents; 250 / 1000 = 25%.
    assert u.five_hour is not None
    assert u.five_hour.five_hour_percent == pytest.approx(25.0)
    # 750 cents remaining → $7.50.
    assert u.extra_credits_remaining == pytest.approx(7.5)
    # No seven-day window.
    assert u.seven_day is None
    assert seen_headers["authorization"] == "Bearer sk-or-v1-fake"


async def test_unwrapped_data_fallback() -> None:
    """Older OpenRouter responses omit the ``data`` wrapper; the adapter
    should still parse them correctly."""

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_body(data_wrapper=False, usage_cents=500, limit_cents=2000))

    async with _make_client(handler) as client:
        u = await OpenRouterAdapter().fetch(
            {API_KEY_KEY: "k"}, client=client
        )
    assert u.five_hour is not None
    assert u.five_hour.five_hour_percent == pytest.approx(25.0)
    assert u.extra_credits_remaining == pytest.approx(15.0)


async def test_no_limit_returns_none() -> None:
    """When ``limit`` is null/0, percent is None (don't divide by zero)."""

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_body(limit_cents=0))

    async with _make_client(handler) as client:
        u = await OpenRouterAdapter().fetch(
            {API_KEY_KEY: "k"}, client=client
        )
    assert u.five_hour is None
    assert u.extra_credits_remaining is None


async def test_fully_used_caps_at_100_percent() -> None:
    """usage > limit must clamp to 100%, never overflow."""

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json=_body(usage_cents=2000, limit_cents=1000)
        )

    async with _make_client(handler) as client:
        u = await OpenRouterAdapter().fetch(
            {API_KEY_KEY: "k"}, client=client
        )
    assert u.five_hour is not None
    assert u.five_hour.five_hour_percent == pytest.approx(100.0)
    # Remaining credit clamps at 0.
    assert u.extra_credits_remaining == pytest.approx(0.0)


async def test_missing_api_key_raises_auth() -> None:
    async with _make_client(lambda req: httpx.Response(200)) as client:
        with pytest.raises(AuthError):
            await OpenRouterAdapter().fetch({}, client=client)


async def test_empty_api_key_raises_auth() -> None:
    async with _make_client(lambda req: httpx.Response(200)) as client:
        with pytest.raises(AuthError):
            await OpenRouterAdapter().fetch({API_KEY_KEY: " "}, client=client)


async def test_401_raises_auth_error() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(401)

    async with _make_client(handler) as client:
        with pytest.raises(AuthError):
            await OpenRouterAdapter().fetch(
                {API_KEY_KEY: "bad"}, client=client
            )


async def test_429_maps_to_rate_limit() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "30"})

    async with _make_client(handler) as client:
        with pytest.raises(RateLimitError) as ei:
            await OpenRouterAdapter().fetch(
                {API_KEY_KEY: "k"}, client=client
            )
    assert ei.value.retry_after_seconds == 30.0


async def test_5xx_maps_to_provider_unavailable() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(502)

    async with _make_client(handler) as client:
        with pytest.raises(ProviderUnavailable):
            await OpenRouterAdapter().fetch(
                {API_KEY_KEY: "k"}, client=client
            )


async def test_transport_error_wraps_in_provider_unavailable() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ReadError("connection reset")

    async with _make_client(handler) as client:
        with pytest.raises(ProviderUnavailable):
            await OpenRouterAdapter().fetch(
                {API_KEY_KEY: "k"}, client=client
            )


async def test_describe_credential_mentions_api_key() -> None:
    text = OpenRouterAdapter().describe_credential().lower()
    assert "api key" in text or "key" in text
    assert "openrouter" in text


async def test_url_constant_matches_documented_endpoint() -> None:
    assert "openrouter.ai" in USAGE_URL
    assert "/auth/key" in USAGE_URL
