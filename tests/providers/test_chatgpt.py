"""Tests for the ChatGPT adapter (Phase 7b)."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from custats.core.models import Provider
from custats.providers.base import AuthError, RateLimitError
from custats.providers.chatgpt import (
    AUTH_JSON_KEY,
    COOKIE_KEY,
    ChatGPTAdapter,
    USAGE_URL,
)


def _make_client(handler, *, base_url: str = "https://chatgpt.com") -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=base_url)


def _body(
    *,
    util_5h: float = 0.5,
    reset_5h: str = "2026-09-15T20:00:00Z",
    util_7d: float = 0.3,
    reset_7d: str = "2026-09-22T20:00:00Z",
    util_monthly: float | None = 0.65,
    extra_credits: float | None = 12.5,
):
    body: dict = {
        "five_hour": {"utilization": util_5h, "resets_at": reset_5h},
        "seven_day": {"utilization": util_7d, "resets_at": reset_7d},
    }
    if util_monthly is not None:
        body["monthly"] = {"utilization": util_monthly}
    if extra_credits is not None:
        body["extra_usage"] = {"credits_remaining": extra_credits}
    return body


async def test_chatgpt_cookie_auth_path() -> None:
    body = _body()
    seen_headers: dict[str, str] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen_headers.update(req.headers)
        return httpx.Response(200, json=body)

    async with _make_client(handler) as client:
        u = await ChatGPTAdapter().fetch(
            {COOKIE_KEY: "session=abc; Path=/"},
            client=client,
        )
    assert u.provider is Provider.CHATGPT
    assert seen_headers["cookie"] == "session=abc; Path=/"
    assert "authorization" not in {k.lower() for k in seen_headers}
    assert u.five_hour is not None
    assert u.five_hour.five_hour_percent == pytest.approx(50.0)
    assert u.seven_day is not None
    assert u.seven_day.seven_day_percent == pytest.approx(30.0)
    assert u.seven_day.monthly_percent == pytest.approx(65.0)
    assert u.extra_credits_remaining == 12.5


async def test_chatgpt_auth_json_path(tmp_path: Path) -> None:
    auth_path = tmp_path / "auth.json"
    auth_path.write_text(
        json.dumps({"access_token": "oa-chatgpt-123", "refresh_token": "rt-456"}),
        encoding="utf-8",
    )
    body = _body(extra_credits=None, util_monthly=None)

    seen_headers: dict[str, str] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen_headers.update(req.headers)
        return httpx.Response(200, json=body)

    async with _make_client(handler) as client:
        u = await ChatGPTAdapter().fetch(
            {AUTH_JSON_KEY: str(auth_path)},
            client=client,
        )
    assert seen_headers["authorization"] == "Bearer oa-chatgpt-123"
    assert "cookie" not in {k.lower() for k in seen_headers}
    assert u.five_hour is not None
    assert u.extra_credits_remaining is None
    assert u.seven_day is not None
    assert u.seven_day.monthly_percent is None


async def test_chatgpt_no_credentials_raises_auth() -> None:
    async with _make_client(lambda req: httpx.Response(200)) as client:
        with pytest.raises(AuthError):
            await ChatGPTAdapter().fetch({}, client=client)


async def test_chatgpt_auth_json_missing_file_raises_auth(tmp_path: Path) -> None:
    missing = tmp_path / "nope.json"
    async with _make_client(lambda req: httpx.Response(200)) as client:
        with pytest.raises(AuthError) as ei:
            await ChatGPTAdapter().fetch(
                {AUTH_JSON_KEY: str(missing)}, client=client
            )
    assert "not found" in str(ei.value).lower()


async def test_chatgpt_auth_json_missing_token_raises_auth(tmp_path: Path) -> None:
    bad = tmp_path / "auth.json"
    bad.write_text(json.dumps({"refresh_token": "only"}), encoding="utf-8")
    async with _make_client(lambda req: httpx.Response(200)) as client:
        with pytest.raises(AuthError):
            await ChatGPTAdapter().fetch(
                {AUTH_JSON_KEY: str(bad)}, client=client
            )


async def test_chatgpt_parses_five_hour_and_seven_day() -> None:
    """Mapping: ``five_hour`` → 5h slot, ``seven_day`` → 7d slot, ``monthly``
    → carried on the ``seven_day`` :class:`ProviderLimits` via
    ``monthly_percent`` (the primary ChatGPT Plus message cap)."""
    body = _body(util_5h=0.5, util_7d=0.3, util_monthly=0.65)

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    async with _make_client(handler) as client:
        u = await ChatGPTAdapter().fetch({COOKIE_KEY: "x"}, client=client)
    assert u.five_hour is not None
    assert u.five_hour.five_hour_percent == pytest.approx(50.0)
    assert u.seven_day is not None
    assert u.seven_day.seven_day_percent == pytest.approx(30.0)
    assert u.seven_day.monthly_percent == pytest.approx(65.0)


async def test_chatgpt_429_maps_to_rate_limit() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "5"})

    async with _make_client(handler) as client:
        with pytest.raises(RateLimitError) as ei:
            await ChatGPTAdapter().fetch({COOKIE_KEY: "x"}, client=client)
    assert ei.value.retry_after_seconds == 5.0


async def test_chatgpt_401_raises_auth_error() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(401)

    async with _make_client(handler) as client:
        with pytest.raises(AuthError):
            await ChatGPTAdapter().fetch({COOKIE_KEY: "x"}, client=client)


async def test_chatgpt_describe_credential_mentions_both_options() -> None:
    text = ChatGPTAdapter().describe_credential().lower()
    assert "cookie" in text
    assert "auth.json" in text
    assert "chatgpt" in text


async def test_chatgpt_url_constant_matches_documented_endpoint() -> None:
    assert "chatgpt.com" in USAGE_URL
    assert "/usage" in USAGE_URL


async def test_chatgpt_omits_monthly_when_missing_from_body() -> None:
    """Free users may not have a ``monthly`` field — make sure the adapter
    still returns a ``seven_day`` slot when only the basic windows exist."""

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "five_hour": {"utilization": 0.5},
                "seven_day": {"utilization": 0.3},
            },
        )

    async with _make_client(handler) as client:
        u = await ChatGPTAdapter().fetch({COOKIE_KEY: "x"}, client=client)
    assert u.seven_day is not None
    assert u.seven_day.seven_day_percent == pytest.approx(30.0)
    assert u.seven_day.monthly_percent is None


async def test_chatgpt_account_id_defaults_when_missing() -> None:
    """When ``account_id`` isn't pre-seeded in credentials (the normal case
    via the poller), the adapter substitutes a stable placeholder so the
    Usage object is usable. Mirrors codex behaviour."""

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_body())

    async with _make_client(handler) as client:
        u = await ChatGPTAdapter().fetch({COOKIE_KEY: "x"}, client=client)
    assert u.account_id == "chatgpt-default"


async def test_chatgpt_account_id_uses_credential_when_present() -> None:
    """If the poller seeds ``account_id`` in the credentials dict, propagate it."""

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_body())

    async with _make_client(handler) as client:
        u = await ChatGPTAdapter().fetch(
            {COOKIE_KEY: "x", "account_id": "acc-real-123"},
            client=client,
        )
    assert u.account_id == "acc-real-123"


async def test_chatgpt_5xx_maps_to_unavailable() -> None:
    """A 503 from the upstream must surface as ``ProviderUnavailable`` so the
    poller can back off cleanly. Mirrors codex's behaviour under load."""

    from custats.providers.base import ProviderUnavailable

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    async with _make_client(handler) as client:
        with pytest.raises(ProviderUnavailable):
            await ChatGPTAdapter().fetch({COOKIE_KEY: "x"}, client=client)


async def test_chatgpt_monthly_falls_back_to_seven_day_monthly_percent() -> None:
    """Some ChatGPT responses nest the monthly cap under ``seven_day`` instead
    of exposing a top-level ``monthly`` field. The adapter should pick it up
    either way and surface it as ``monthly_percent``."""

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "five_hour": {"utilization": 0.5},
                "seven_day": {
                    "utilization": 0.3,
                    "monthly_percent": 0.72,
                },
            },
        )

    async with _make_client(handler) as client:
        u = await ChatGPTAdapter().fetch({COOKIE_KEY: "x"}, client=client)
    assert u.seven_day is not None
    assert u.seven_day.monthly_percent == pytest.approx(72.0)
