"""Tests for the Codex adapter."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from custats.core.models import Provider
from custats.providers.codex import AUTH_JSON_KEY, COOKIE_KEY, CodexAdapter, USAGE_URL
from custats.providers.base import AuthError, RateLimitError


def _make_client(handler, *, base_url: str = "https://chatgpt.com") -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=base_url)


def _body(
    *,
    util_5h: float = 0.5,
    reset_5h: str = "2026-09-15T20:00:00Z",
    util_7d: float = 0.3,
    reset_7d: str = "2026-09-22T20:00:00Z",
    extra_credits: float | None = 12.5,
    reset_credits: int | None = 100,
):
    return {
        "five_hour": {"utilization": util_5h, "resets_at": reset_5h},
        "seven_day": {"utilization": util_7d, "resets_at": reset_7d},
        "extra_usage": {"credits_remaining": extra_credits},
        "codex_credits": {"reset_credits": reset_credits},
    }


async def test_cookie_auth_path() -> None:
    body = _body()
    seen_headers: dict[str, str] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen_headers.update(req.headers)
        return httpx.Response(200, json=body)

    async with _make_client(handler) as client:
        u = await CodexAdapter().fetch(
            {COOKIE_KEY: "session=abc; Path=/"},
            client=client,
        )
    assert u.provider is Provider.CODEX
    assert seen_headers["cookie"] == "session=abc; Path=/"
    assert "authorization" not in {k.lower() for k in seen_headers}
    assert u.five_hour is not None
    assert u.five_hour.five_hour_percent == pytest.approx(50.0)
    assert u.seven_day is not None
    assert u.seven_day.seven_day_percent == pytest.approx(30.0)
    assert u.extra_credits_remaining == 12.5
    assert u.seven_day.codex_reset_credits == 100


async def test_auth_json_path(tmp_path: Path) -> None:
    auth_path = tmp_path / "auth.json"
    auth_path.write_text(
        json.dumps({"access_token": "oa-tok-123", "refresh_token": "rt-456"}),
        encoding="utf-8",
    )
    body = _body(extra_credits=None, reset_credits=None)

    seen_headers: dict[str, str] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen_headers.update(req.headers)
        return httpx.Response(200, json=body)

    async with _make_client(handler) as client:
        u = await CodexAdapter().fetch(
            {AUTH_JSON_KEY: str(auth_path)},
            client=client,
        )
    assert seen_headers["authorization"] == "Bearer oa-tok-123"
    assert u.five_hour is not None
    assert u.extra_credits_remaining is None
    assert u.seven_day is not None
    assert u.seven_day.codex_reset_credits is None


async def test_no_credentials_raises_auth() -> None:
    async with _make_client(lambda req: httpx.Response(200)) as client:
        with pytest.raises(AuthError):
            await CodexAdapter().fetch({}, client=client)


async def test_auth_json_missing_token_raises_auth(tmp_path: Path) -> None:
    bad = tmp_path / "auth.json"
    bad.write_text(json.dumps({"refresh_token": "only"}), encoding="utf-8")
    async with _make_client(lambda req: httpx.Response(200)) as client:
        with pytest.raises(AuthError):
            await CodexAdapter().fetch({AUTH_JSON_KEY: str(bad)}, client=client)


async def test_auth_json_missing_file_raises_auth(tmp_path: Path) -> None:
    missing = tmp_path / "nope.json"
    async with _make_client(lambda req: httpx.Response(200)) as client:
        with pytest.raises(AuthError) as ei:
            await CodexAdapter().fetch({AUTH_JSON_KEY: str(missing)}, client=client)
    assert "not found" in str(ei.value)


async def test_429_maps_to_rate_limit() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "5"})

    async with _make_client(handler) as client:
        with pytest.raises(RateLimitError) as ei:
            await CodexAdapter().fetch({COOKIE_KEY: "x"}, client=client)
    assert ei.value.retry_after_seconds == 5.0


async def test_401_raises_auth_error() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(401)

    async with _make_client(handler) as client:
        with pytest.raises(AuthError):
            await CodexAdapter().fetch({COOKIE_KEY: "x"}, client=client)


async def test_describe_credential_mentions_both() -> None:
    text = CodexAdapter().describe_credential().lower()
    assert "cookie" in text
    assert "auth.json" in text


async def test_url_constant_matches_documented_endpoint() -> None:
    assert "chatgpt.com" in USAGE_URL
    assert "/usage" in USAGE_URL