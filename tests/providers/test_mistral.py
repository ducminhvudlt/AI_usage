"""Tests for the Mistral adapter."""

from __future__ import annotations

import httpx
import pytest

from custats.core.models import Provider
from custats.providers.base import AuthError, ProviderUnavailable, RateLimitError
from custats.providers.mistral import API_KEY_KEY, MistralAdapter, USAGE_URL


def _make_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://api.mistral.ai",
    )


def _body(*, balance_cents: int | float | None = 12345) -> dict:
    payload: dict[str, object] = {}
    if balance_cents is not None:
        payload["balance"] = balance_cents
    return payload


async def test_parses_response_with_realistic_balance() -> None:
    """12345 cents → $123.45. The canonical happy path."""
    seen_headers: dict[str, str] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen_headers.update(req.headers)
        return httpx.Response(200, json=_body())

    async with _make_client(handler) as client:
        u = await MistralAdapter().fetch(
            {API_KEY_KEY: "sk-mistral-fake"}, client=client
        )

    assert u.provider is Provider.MISTRAL
    # No percentage windows — Mistral only exposes a dollar balance.
    assert u.five_hour is None
    assert u.seven_day is None
    assert u.extra_credits_remaining == pytest.approx(123.45)
    assert seen_headers["authorization"] == "Bearer sk-mistral-fake"


async def test_zero_balance() -> None:
    """``balance: 0`` → $0.00 (valid edge case, account is exhausted)."""

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_body(balance_cents=0))

    async with _make_client(handler) as client:
        u = await MistralAdapter().fetch({API_KEY_KEY: "k"}, client=client)

    assert u.extra_credits_remaining == pytest.approx(0.0)


async def test_float_balance_cents() -> None:
    """Some accounts report fractional cents — accept and convert."""

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_body(balance_cents=99.5))

    async with _make_client(handler) as client:
        u = await MistralAdapter().fetch({API_KEY_KEY: "k"}, client=client)

    assert u.extra_credits_remaining == pytest.approx(0.995)


async def test_missing_balance_field_returns_none() -> None:
    """A response without ``balance`` should not crash the adapter."""

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    async with _make_client(handler) as client:
        u = await MistralAdapter().fetch({API_KEY_KEY: "k"}, client=client)

    assert u.extra_credits_remaining is None


async def test_non_numeric_balance_returns_none() -> None:
    """A string / dict / list ``balance`` value is ignored, not crashed."""

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"balance": "not-a-number"})

    async with _make_client(handler) as client:
        u = await MistralAdapter().fetch({API_KEY_KEY: "k"}, client=client)

    assert u.extra_credits_remaining is None


async def test_raw_body_is_preserved() -> None:
    """The upstream payload is captured verbatim in ``raw`` for debugging."""

    payload = _body()

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    async with _make_client(handler) as client:
        u = await MistralAdapter().fetch({API_KEY_KEY: "k"}, client=client)

    assert u.raw == payload


async def test_account_id_falls_back_to_default() -> None:
    """When the poller doesn't supply ``account_id`` the snapshot is still
    valid (uses a deterministic default), so unit scripts can call ``fetch``
    directly without breaking the model contract."""

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_body())

    async with _make_client(handler) as client:
        u = await MistralAdapter().fetch({API_KEY_KEY: "k"}, client=client)

    assert u.account_id == "mistral-default"


async def test_account_id_overrides_default() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_body())

    async with _make_client(handler) as client:
        u = await MistralAdapter().fetch(
            {API_KEY_KEY: "k", "account_id": "personal"}, client=client
        )

    assert u.account_id == "personal"


async def test_missing_api_key_raises_auth() -> None:
    async with _make_client(lambda req: httpx.Response(200)) as client:
        with pytest.raises(AuthError):
            await MistralAdapter().fetch({}, client=client)


async def test_empty_api_key_raises_auth() -> None:
    async with _make_client(lambda req: httpx.Response(200)) as client:
        with pytest.raises(AuthError):
            await MistralAdapter().fetch({API_KEY_KEY: "  "}, client=client)


async def test_401_raises_auth_error() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="Unauthorized")

    async with _make_client(handler) as client:
        with pytest.raises(AuthError):
            await MistralAdapter().fetch({API_KEY_KEY: "bad"}, client=client)


async def test_403_raises_auth_error() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="Forbidden")

    async with _make_client(handler) as client:
        with pytest.raises(AuthError):
            await MistralAdapter().fetch({API_KEY_KEY: "bad"}, client=client)


async def test_429_maps_to_rate_limit() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "45"})

    async with _make_client(handler) as client:
        with pytest.raises(RateLimitError) as ei:
            await MistralAdapter().fetch({API_KEY_KEY: "k"}, client=client)
    assert ei.value.retry_after_seconds == 45.0


async def test_5xx_maps_to_provider_unavailable() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="Service Unavailable")

    async with _make_client(handler) as client:
        with pytest.raises(ProviderUnavailable):
            await MistralAdapter().fetch({API_KEY_KEY: "k"}, client=client)


async def test_transport_error_wraps_in_provider_unavailable() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("dns failure")

    async with _make_client(handler) as client:
        with pytest.raises(ProviderUnavailable):
            await MistralAdapter().fetch({API_KEY_KEY: "k"}, client=client)


async def test_non_json_object_body_raises_auth() -> None:
    """An array (or other non-dict) JSON response is malformed."""

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[1, 2, 3])

    async with _make_client(handler) as client:
        with pytest.raises(AuthError):
            await MistralAdapter().fetch({API_KEY_KEY: "k"}, client=client)


async def test_describe_credential_mentions_api_key() -> None:
    text = MistralAdapter().describe_credential().lower()
    assert "api key" in text
    assert "mistral" in text


async def test_url_constant_matches_documented_endpoint() -> None:
    assert "api.mistral.ai" in USAGE_URL
    assert "/v1/users/me/usage/balance" in USAGE_URL