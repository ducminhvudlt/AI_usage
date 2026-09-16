"""Tests for the DeepSeek adapter."""

from __future__ import annotations

import httpx
import pytest

from custats.core.models import Provider
from custats.providers.base import AuthError, ProviderUnavailable, RateLimitError
from custats.providers.deepseek import API_KEY_KEY, DeepSeekAdapter, USAGE_URL


def _make_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://api.deepseek.com"
    )


def _body(*, balances: list[str] | None = None, is_available: bool = True) -> dict:
    if balances is None:
        balances = ["10.00 USDT"]
    return {"is_available": is_available, "balance": balances}


async def test_parses_response() -> None:
    seen_headers: dict[str, str] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen_headers.update(req.headers)
        return httpx.Response(200, json=_body())

    async with _make_client(handler) as client:
        u = await DeepSeekAdapter().fetch(
            {API_KEY_KEY: "sk-ds-fake"}, client=client
        )

    assert u.provider is Provider.DEEPSEEK
    # No percentage windows — DeepSeek only exposes a dollar balance.
    assert u.five_hour is None
    assert u.seven_day is None
    assert u.extra_credits_remaining == pytest.approx(10.0)
    assert seen_headers["authorization"] == "Bearer sk-ds-fake"


async def test_sums_multiple_balance_entries() -> None:
    """Some accounts hold both USDT and a promotional currency."""

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json=_body(balances=["10.00 USDT", "5.50 USDT", "2.25 USDT"])
        )

    async with _make_client(handler) as client:
        u = await DeepSeekAdapter().fetch(
            {API_KEY_KEY: "k"}, client=client
        )
    assert u.extra_credits_remaining == pytest.approx(17.75)


async def test_empty_balance_returns_none() -> None:
    """``balance: []`` is a valid "no credit" signal; treat as ``None``."""

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_body(balances=[]))

    async with _make_client(handler) as client:
        u = await DeepSeekAdapter().fetch(
            {API_KEY_KEY: "k"}, client=client
        )
    assert u.extra_credits_remaining is None


async def test_missing_balance_field_returns_none() -> None:
    """A response without a ``balance`` key should not crash the adapter."""

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"is_available": True})

    async with _make_client(handler) as client:
        u = await DeepSeekAdapter().fetch(
            {API_KEY_KEY: "k"}, client=client
        )
    assert u.extra_credits_remaining is None


async def test_garbage_balance_entries_are_skipped() -> None:
    """Non-numeric entries are dropped without crashing the sum."""

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_body(balances=["7.50 USDT", "not-a-number", "USDT", "3.00 EUR"]),
        )

    async with _make_client(handler) as client:
        u = await DeepSeekAdapter().fetch(
            {API_KEY_KEY: "k"}, client=client
        )
    assert u.extra_credits_remaining == pytest.approx(10.5)


async def test_missing_api_key_raises_auth() -> None:
    async with _make_client(lambda req: httpx.Response(200)) as client:
        with pytest.raises(AuthError):
            await DeepSeekAdapter().fetch({}, client=client)


async def test_empty_api_key_raises_auth() -> None:
    async with _make_client(lambda req: httpx.Response(200)) as client:
        with pytest.raises(AuthError):
            await DeepSeekAdapter().fetch({API_KEY_KEY: "  "}, client=client)


async def test_401_raises_auth_error() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="Authentication FAILED")

    async with _make_client(handler) as client:
        with pytest.raises(AuthError):
            await DeepSeekAdapter().fetch({API_KEY_KEY: "bad"}, client=client)


async def test_403_raises_auth_error() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(403)

    async with _make_client(handler) as client:
        with pytest.raises(AuthError):
            await DeepSeekAdapter().fetch({API_KEY_KEY: "bad"}, client=client)


async def test_429_maps_to_rate_limit() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "20"})

    async with _make_client(handler) as client:
        with pytest.raises(RateLimitError) as ei:
            await DeepSeekAdapter().fetch({API_KEY_KEY: "k"}, client=client)
    assert ei.value.retry_after_seconds == 20.0


async def test_5xx_maps_to_provider_unavailable() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    async with _make_client(handler) as client:
        with pytest.raises(ProviderUnavailable):
            await DeepSeekAdapter().fetch({API_KEY_KEY: "k"}, client=client)


async def test_transport_error_wraps_in_provider_unavailable() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no network")

    async with _make_client(handler) as client:
        with pytest.raises(ProviderUnavailable):
            await DeepSeekAdapter().fetch({API_KEY_KEY: "k"}, client=client)


async def test_describe_credential_mentions_api_key() -> None:
    text = DeepSeekAdapter().describe_credential().lower()
    assert "api key" in text
    assert "deepseek" in text


async def test_url_constant_matches_documented_endpoint() -> None:
    assert "api.deepseek.com" in USAGE_URL
    assert "/user/balance" in USAGE_URL
