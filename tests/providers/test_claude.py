"""Tests for the Claude adapter."""

from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest

from custats.core.models import Provider
from custats.providers import claude as claude_mod
from custats.providers.claude import ClaudeAdapter, USAGE_URL
from custats.providers.base import AuthError, RateLimitError


def _make_client(handler):
    return httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://claude.ai"
    )


def _body(util_5h=0.42, reset_5h="2026-09-15T20:00:00Z", util_7d=0.15, reset_7d="2026-09-22T20:00:00Z"):
    return {
        "five_hour": {"utilization": util_5h, "resets_at": reset_5h},
        "seven_day": {"utilization": util_7d, "resets_at": reset_7d},
    }


async def test_parses_both_windows() -> None:
    body = _body()

    def handler(req: httpx.Request) -> httpx.Response:
        assert "sessionKey=sek" in req.headers["cookie"]
        assert req.url.path == "/api/organizations/me/usage"
        return httpx.Response(200, json=body)

    async with _make_client(handler) as client:
        u = await ClaudeAdapter().fetch({"session_key": "sek"}, client=client)
    assert u.provider is Provider.CLAUDE
    assert u.five_hour is not None
    assert u.five_hour.five_hour_percent == pytest.approx(42.0)
    assert u.seven_day is not None
    assert u.seven_day.seven_day_percent == pytest.approx(15.0)
    assert u.five_hour_resets_at == datetime(2026, 9, 15, 20, 0, tzinfo=timezone.utc)
    assert u.seven_day_resets_at == datetime(2026, 9, 22, 20, 0, tzinfo=timezone.utc)
    assert u.raw == body


async def test_seven_day_missing_is_ok() -> None:
    body = {"five_hour": _body()["five_hour"]}  # no seven_day

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    async with _make_client(handler) as client:
        u = await ClaudeAdapter().fetch({"session_key": "sek"}, client=client)
    assert u.five_hour is not None
    assert u.seven_day is None
    assert u.seven_day_resets_at is None


async def test_missing_credential_raises_auth() -> None:
    async with _make_client(lambda req: httpx.Response(200)) as client:
        with pytest.raises(AuthError):
            await ClaudeAdapter().fetch({}, client=client)


async def test_401_raises_auth_error() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(401)

    async with _make_client(handler) as client:
        with pytest.raises(AuthError):
            await ClaudeAdapter().fetch({"session_key": "sek"}, client=client)


async def test_429_maps_to_rate_limit() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "30"})

    async with _make_client(handler) as client:
        with pytest.raises(RateLimitError) as ei:
            await ClaudeAdapter().fetch({"session_key": "sek"}, client=client)
    assert ei.value.retry_after_seconds == 30.0


async def test_describe_credential() -> None:
    assert "sessionKey" in ClaudeAdapter().describe_credential()


async def test_url_constant_matches_documented_endpoint() -> None:
    """The module-level URL is the one we expect — easy to spot if swapped."""
    assert USAGE_URL == claude_mod.USAGE_URL
    assert "claude.ai" in USAGE_URL
    assert "/usage" in USAGE_URL