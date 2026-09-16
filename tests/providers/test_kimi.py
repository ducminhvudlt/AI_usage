"""Tests for the Kimi adapter."""

from __future__ import annotations

import httpx
import pytest

from custats.core.models import Provider
from custats.providers.base import AuthError, ProviderUnavailable, RateLimitError
from custats.providers.kimi import API_KEY_KEY, KimiAdapter, USAGE_URL


def _make_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://api.moonshot.cn",
    )


def _body(
    *,
    data_balance: int | float | None = None,
    data_credit: int | float | None = None,
    top_balance: int | float | None = None,
    top_credit: int | float | None = None,
    code: int = 0,
) -> dict:
    """Build a Kimi-style payload.

    All four fields default to ``None`` (i.e. absent). The two helpers
    (``data_*`` / ``top_*``) let tests exercise the wrapped and the
    legacy unwrapped shapes independently.
    """
    data: dict[str, object] = {}
    if data_balance is not None:
        data["balance"] = data_balance
    if data_credit is not None:
        data["credit"] = data_credit
    payload: dict[str, object] = {"code": code}
    if data:
        payload["data"] = data
    if top_balance is not None:
        payload["balance"] = top_balance
    if top_credit is not None:
        payload["credit"] = top_credit
    return payload


async def test_parses_wrapped_data_balance() -> None:
    """``{"code": 0, "data": {"balance": 5000}}`` → $50.00 (the canonical
    happy path that Kimi documents)."""

    seen_headers: dict[str, str] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen_headers.update(req.headers)
        return httpx.Response(200, json=_body(data_balance=5000))

    async with _make_client(handler) as client:
        u = await KimiAdapter().fetch(
            {API_KEY_KEY: "sk-kimi-fake"}, client=client
        )

    assert u.provider is Provider.KIMI
    # No percentage windows — Kimi only exposes a dollar balance.
    assert u.five_hour is None
    assert u.seven_day is None
    assert u.extra_credits_remaining == pytest.approx(50.0)
    assert seen_headers["authorization"] == "Bearer sk-kimi-fake"


async def test_parses_wrapped_data_credit() -> None:
    """When the API surfaces ``credit`` instead of ``balance`` (common
    on the billing endpoint) the adapter still finds the value."""

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_body(data_credit=12345))

    async with _make_client(handler) as client:
        u = await KimiAdapter().fetch({API_KEY_KEY: "k"}, client=client)

    assert u.extra_credits_remaining == pytest.approx(123.45)


async def test_parses_unwrapped_top_level_balance() -> None:
    """Older Kimi responses omit the ``data`` wrapper — the adapter
    falls back to the top-level ``balance`` field."""

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_body(top_balance=5000))

    async with _make_client(handler) as client:
        u = await KimiAdapter().fetch({API_KEY_KEY: "k"}, client=client)

    assert u.extra_credits_remaining == pytest.approx(50.0)


async def test_parses_unwrapped_top_level_credit() -> None:
    """``credit`` at the top level (legacy shape)."""

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_body(top_credit=2500))

    async with _make_client(handler) as client:
        u = await KimiAdapter().fetch({API_KEY_KEY: "k"}, client=client)

    assert u.extra_credits_remaining == pytest.approx(25.0)


async def test_wrapped_wins_over_unwrapped() -> None:
    """If both shapes are present, the documented ``data.balance`` wins."""

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json=_body(data_balance=1000, top_balance=9999)
        )

    async with _make_client(handler) as client:
        u = await KimiAdapter().fetch({API_KEY_KEY: "k"}, client=client)

    # 1000 cents → $10.00, not $99.99.
    assert u.extra_credits_remaining == pytest.approx(10.0)


async def test_zero_balance() -> None:
    """``balance: 0`` → $0.00 (valid edge case)."""

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_body(data_balance=0))

    async with _make_client(handler) as client:
        u = await KimiAdapter().fetch({API_KEY_KEY: "k"}, client=client)

    assert u.extra_credits_remaining == pytest.approx(0.0)


async def test_float_cents() -> None:
    """Fractional cents are accepted and converted."""

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_body(data_balance=99.5))

    async with _make_client(handler) as client:
        u = await KimiAdapter().fetch({API_KEY_KEY: "k"}, client=client)

    assert u.extra_credits_remaining == pytest.approx(0.995)


async def test_both_shapes_missing_balance_returns_none() -> None:
    """Neither ``data.balance`` / ``data.credit`` nor top-level
    ``balance`` / ``credit`` present → ``extra_credits_remaining=None``,
    no exception."""

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_body())  # empty payload

    async with _make_client(handler) as client:
        u = await KimiAdapter().fetch({API_KEY_KEY: "k"}, client=client)

    assert u.extra_credits_remaining is None


async def test_non_numeric_values_are_ignored() -> None:
    """String / dict / list values are skipped — the adapter doesn't crash."""

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": {"balance": "garbage", "credit": [1, 2]},
                "balance": {"nope": 1},
                "credit": "also-garbage",
            },
        )

    async with _make_client(handler) as client:
        u = await KimiAdapter().fetch({API_KEY_KEY: "k"}, client=client)

    assert u.extra_credits_remaining is None


async def test_raw_body_is_preserved() -> None:
    payload = _body(data_balance=5000)

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    async with _make_client(handler) as client:
        u = await KimiAdapter().fetch({API_KEY_KEY: "k"}, client=client)

    assert u.raw == payload


async def test_account_id_falls_back_to_default() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_body(data_balance=1))

    async with _make_client(handler) as client:
        u = await KimiAdapter().fetch({API_KEY_KEY: "k"}, client=client)

    assert u.account_id == "kimi-default"


async def test_account_id_overrides_default() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_body(data_balance=1))

    async with _make_client(handler) as client:
        u = await KimiAdapter().fetch(
            {API_KEY_KEY: "k", "account_id": "work"}, client=client
        )

    assert u.account_id == "work"


async def test_missing_api_key_raises_auth() -> None:
    async with _make_client(lambda req: httpx.Response(200)) as client:
        with pytest.raises(AuthError):
            await KimiAdapter().fetch({}, client=client)


async def test_empty_api_key_raises_auth() -> None:
    async with _make_client(lambda req: httpx.Response(200)) as client:
        with pytest.raises(AuthError):
            await KimiAdapter().fetch({API_KEY_KEY: "  "}, client=client)


async def test_401_raises_auth_error() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="Unauthorized")

    async with _make_client(handler) as client:
        with pytest.raises(AuthError):
            await KimiAdapter().fetch({API_KEY_KEY: "bad"}, client=client)


async def test_403_raises_auth_error() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="Forbidden")

    async with _make_client(handler) as client:
        with pytest.raises(AuthError):
            await KimiAdapter().fetch({API_KEY_KEY: "bad"}, client=client)


async def test_429_maps_to_rate_limit() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "15"})

    async with _make_client(handler) as client:
        with pytest.raises(RateLimitError) as ei:
            await KimiAdapter().fetch({API_KEY_KEY: "k"}, client=client)
    assert ei.value.retry_after_seconds == 15.0


async def test_5xx_maps_to_provider_unavailable() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    async with _make_client(handler) as client:
        with pytest.raises(ProviderUnavailable):
            await KimiAdapter().fetch({API_KEY_KEY: "k"}, client=client)


async def test_transport_error_wraps_in_provider_unavailable() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ReadError("connection reset")

    async with _make_client(handler) as client:
        with pytest.raises(ProviderUnavailable):
            await KimiAdapter().fetch({API_KEY_KEY: "k"}, client=client)


async def test_non_json_object_body_raises_auth() -> None:
    """An array (or other non-dict) JSON response is malformed."""

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=["not", "an", "object"])

    async with _make_client(handler) as client:
        with pytest.raises(AuthError):
            await KimiAdapter().fetch({API_KEY_KEY: "k"}, client=client)


async def test_describe_credential_mentions_api_key() -> None:
    text = KimiAdapter().describe_credential().lower()
    assert "api key" in text
    # "kimi" OR "moonshot" — either brand name is acceptable.
    assert "kimi" in text or "moonshot" in text


async def test_url_constant_matches_documented_endpoint() -> None:
    assert "api.moonshot.cn" in USAGE_URL
    assert "/v1/dashboard/billing/credit" in USAGE_URL