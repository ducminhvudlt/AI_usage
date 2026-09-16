"""Tests for the Cursor adapter."""

from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest

from custats.core.models import Provider
from custats.providers.cursor import COOKIE_KEY, CursorAdapter, USAGE_URL
from custats.providers.base import AuthError, ProviderUnavailable, RateLimitError


def _make_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://www.cursor.com"
    )


def _body(*, util_7d: float = 0.55, reset_7d: str = "2026-09-22T20:00:00Z"):
    return {"seven_day": {"utilization": util_7d, "resets_at": reset_7d}}


async def test_seven_day_only_no_five_hour() -> None:
    body = _body()
    seen_headers: dict[str, str] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen_headers.update(req.headers)
        return httpx.Response(200, json=body)

    async with _make_client(handler) as client:
        u = await CursorAdapter().fetch({COOKIE_KEY: "wo-cookie"}, client=client)
    assert u.provider is Provider.CURSOR
    # Cursor has no 5-hour window — must be None.
    assert u.five_hour is None
    assert u.five_hour_resets_at is None
    assert u.seven_day is not None
    assert u.seven_day.seven_day_percent == pytest.approx(55.0)
    assert u.seven_day_resets_at == datetime(2026, 9, 22, 20, 0, tzinfo=timezone.utc)
    assert seen_headers["cookie"] == "WorkosCursor=wo-cookie"


async def test_401_helpful_message() -> None:
    """Cursor cookies can't be refreshed programmatically; the error
    message must tell the user to re-paste, not to refresh.
    """
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(401)

    async with _make_client(handler) as client:
        with pytest.raises(AuthError) as ei:
            await CursorAdapter().fetch({COOKIE_KEY: "wo-cookie"}, client=client)
    msg = str(ei.value).lower()
    assert "re-paste" in msg or "do not refresh" in msg


async def test_missing_cookie_raises_auth() -> None:
    async with _make_client(lambda req: httpx.Response(200)) as client:
        with pytest.raises(AuthError):
            await CursorAdapter().fetch({}, client=client)


async def test_empty_cookie_raises_auth() -> None:
    async with _make_client(lambda req: httpx.Response(200)) as client:
        with pytest.raises(AuthError):
            await CursorAdapter().fetch({COOKIE_KEY: "  "}, client=client)


async def test_429_maps_to_rate_limit() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "45"})

    async with _make_client(handler) as client:
        with pytest.raises(RateLimitError) as ei:
            await CursorAdapter().fetch({COOKIE_KEY: "x"}, client=client)
    assert ei.value.retry_after_seconds == 45.0


async def test_5xx_maps_to_unavailable() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(502)

    async with _make_client(handler) as client:
        with pytest.raises(ProviderUnavailable):
            await CursorAdapter().fetch({COOKIE_KEY: "x"}, client=client)


async def test_describe_credential_mentions_no_refresh() -> None:
    text = CursorAdapter().describe_credential().lower()
    assert "workoscursor" in text
    # The key fact: cookies don't refresh.
    assert "refresh" in text or "re-paste" in text


async def test_url_constant_matches_documented_endpoint() -> None:
    assert "cursor.com" in USAGE_URL
    assert "/usage" in USAGE_URL