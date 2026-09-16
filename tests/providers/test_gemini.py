"""Tests for the Gemini adapter."""

from __future__ import annotations

import httpx
import pytest

from custats.core.models import Provider
from custats.providers.base import AuthError, ProviderUnavailable, RateLimitError
from custats.providers.gemini import API_KEY_KEY, GeminiAdapter, USAGE_URL


def _make_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://generativelanguage.googleapis.com",
    )


def _body(*, models: list | None = None) -> dict:
    if models is None:
        models = [
            {"name": "models/gemini-1.5-pro"},
            {"name": "models/gemini-1.5-flash"},
        ]
    return {"models": models}


async def test_parses_response() -> None:
    seen_headers: dict[str, str] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen_headers.update(req.headers)
        # Confirm the API key shows up both in query and header.
        assert "key=fake-gemini-key" in str(req.url)
        return httpx.Response(200, json=_body())

    async with _make_client(handler) as client:
        u = await GeminiAdapter().fetch({API_KEY_KEY: "fake-gemini-key"}, client=client)

    assert u.provider is Provider.GEMINI
    # Gemini has no usage windows — both must be None.
    assert u.five_hour is None
    assert u.seven_day is None
    assert u.extra_credits_remaining is None
    # Raw body should carry the model count for sanity-checking.
    assert u.raw is not None
    assert u.raw["authenticated"] is True
    assert u.raw["model_count"] == 2
    # Header was sent.
    assert seen_headers["x-goog-api-key"] == "fake-gemini-key"


async def test_missing_api_key_raises_auth() -> None:
    async with _make_client(lambda req: httpx.Response(200)) as client:
        with pytest.raises(AuthError):
            await GeminiAdapter().fetch({}, client=client)


async def test_empty_api_key_raises_auth() -> None:
    async with _make_client(lambda req: httpx.Response(200)) as client:
        with pytest.raises(AuthError):
            await GeminiAdapter().fetch({API_KEY_KEY: "  "}, client=client)


async def test_401_raises_auth_error() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="API key not valid")

    async with _make_client(handler) as client:
        with pytest.raises(AuthError):
            await GeminiAdapter().fetch({API_KEY_KEY: "bad"}, client=client)


async def test_403_raises_auth_error() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="permission denied")

    async with _make_client(handler) as client:
        with pytest.raises(AuthError):
            await GeminiAdapter().fetch({API_KEY_KEY: "bad"}, client=client)


async def test_429_maps_to_rate_limit() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "12"})

    async with _make_client(handler) as client:
        with pytest.raises(RateLimitError) as ei:
            await GeminiAdapter().fetch({API_KEY_KEY: "k"}, client=client)
    assert ei.value.retry_after_seconds == 12.0


async def test_5xx_maps_to_provider_unavailable() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    async with _make_client(handler) as client:
        with pytest.raises(ProviderUnavailable):
            await GeminiAdapter().fetch({API_KEY_KEY: "k"}, client=client)


async def test_transport_error_wraps_in_provider_unavailable() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    async with _make_client(handler) as client:
        with pytest.raises(ProviderUnavailable):
            await GeminiAdapter().fetch({API_KEY_KEY: "k"}, client=client)


async def test_describe_credential_mentions_api_key() -> None:
    text = GeminiAdapter().describe_credential().lower()
    assert "api key" in text
    assert "google" in text or "gemini" in text


async def test_url_constant_matches_documented_endpoint() -> None:
    assert "generativelanguage.googleapis.com" in USAGE_URL
    assert "/v1beta/models" in USAGE_URL
