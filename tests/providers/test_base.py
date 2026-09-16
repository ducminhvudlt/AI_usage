"""Tests for the shared provider-adapter helpers."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from custats.core.models import Provider, Usage
from custats.providers.base import (
    AdapterError,
    AuthError,
    ProviderAdapter,
    ProviderUnavailable,
    RateLimitError,
    map_http_error,
    map_transport_error,
)
from custats.providers.base import _require, _to_usage


class TestRequire:
    def test_returns_dict_when_keys_present(self) -> None:
        out = _require({"a": "x", "b": "y"}, "a")
        assert out == {"a": "x", "b": "y"}

    def test_missing_key_raises(self) -> None:
        with pytest.raises(AuthError) as exc_info:
            _require({"a": "x"}, "a", "b")
        assert "'b'" in str(exc_info.value)

    def test_empty_string_raises(self) -> None:
        with pytest.raises(AuthError):
            _require({"a": "   "}, "a")

    def test_none_value_raises(self) -> None:
        with pytest.raises(AuthError):
            _require({"a": None}, "a")

    def test_non_dict_raises(self) -> None:
        with pytest.raises(AuthError):
            _require("not a dict", "a")  # type: ignore[arg-type]


class TestToUsage:
    def test_minimal(self) -> None:
        u = _to_usage(
            "acct",
            Provider.CLAUDE,
            body={"raw": True},
            five_hour_pct=42.0,
        )
        assert isinstance(u, Usage)
        assert u.account_id == "acct"
        assert u.provider is Provider.CLAUDE
        assert u.five_hour is not None
        assert u.five_hour.five_hour_percent == 42.0
        assert u.seven_day is None
        assert u.raw == {"raw": True}

    def test_all_optionals(self) -> None:
        u = _to_usage(
            "acct",
            Provider.GROK,
            body={},
            five_hour_pct=10.0,
            five_hour_resets="2026-09-15T20:00:00Z",
            seven_day_pct=20.0,
            seven_day_resets="2026-09-22T20:00:00Z",
            extra_credits=99.5,
            grok_window="weekly",
            codex_reset_credits=1234,
        )
        assert u.five_hour is not None
        assert u.five_hour.grok_window == "weekly"
        assert u.seven_day is not None
        assert u.seven_day.codex_reset_credits == 1234
        assert u.extra_credits_remaining == 99.5
        assert u.five_hour_resets_at is not None
        assert u.seven_day_resets_at is not None


class FakeAdapter:
    """Concrete adapter used to exercise error mapping."""

    provider = Provider.CLAUDE

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def describe_credential(self) -> str:
        return "fake"

    async def fetch(self, credentials: dict[str, Any], *, client: httpx.AsyncClient) -> Usage:
        raise self._exc

    def __call__(self, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover - unused
        return None


def test_runtime_checkable_protocol() -> None:
    """The Protocol must be usable as a runtime type check."""
    assert isinstance(FakeAdapter(AuthError("x")), ProviderAdapter)


class TestMapHttpError:
    def test_401_maps_to_auth(self) -> None:
        resp = httpx.Response(401, request=httpx.Request("GET", "https://x"))
        err = map_http_error(resp)
        assert isinstance(err, AuthError)

    def test_403_maps_to_auth(self) -> None:
        resp = httpx.Response(403, request=httpx.Request("GET", "https://x"))
        err = map_http_error(resp)
        assert isinstance(err, AuthError)

    def test_429_maps_to_rate_limit_with_default(self) -> None:
        resp = httpx.Response(429, request=httpx.Request("GET", "https://x"))
        err = map_http_error(resp)
        assert isinstance(err, RateLimitError)
        # Default per spec when Retry-After header missing.
        assert err.retry_after_seconds == 60.0

    def test_429_honors_retry_after_header(self) -> None:
        resp = httpx.Response(
            429,
            headers={"Retry-After": "12"},
            request=httpx.Request("GET", "https://x"),
        )
        err = map_http_error(resp)
        assert isinstance(err, RateLimitError)
        assert err.retry_after_seconds == 12.0

    def test_429_accepts_http_date_retry_after(self) -> None:
        """Per RFC 7231, Retry-After may be an HTTP-date instead of delta-seconds."""
        from datetime import datetime, timedelta, timezone

        future = datetime.now(timezone.utc) + timedelta(seconds=120)
        # Format as RFC 1123 (the form RFC 7231 mandates for HTTP-date)
        date_str = future.strftime("%a, %d %b %Y %H:%M:%S GMT")
        resp = httpx.Response(
            429,
            headers={"Retry-After": date_str},
            request=httpx.Request("GET", "https://x"),
        )
        err = map_http_error(resp)
        assert isinstance(err, RateLimitError)
        # ~120s in the future — within a small tolerance for clock skew
        assert 110.0 <= err.retry_after_seconds <= 130.0

    def test_429_http_date_in_past_clamps_to_zero(self) -> None:
        """An HTTP-date that has already passed must not produce a negative wait."""
        from datetime import datetime, timedelta, timezone

        past = datetime.now(timezone.utc) - timedelta(seconds=60)
        date_str = past.strftime("%a, %d %b %Y %H:%M:%S GMT")
        resp = httpx.Response(
            429,
            headers={"Retry-After": date_str},
            request=httpx.Request("GET", "https://x"),
        )
        err = map_http_error(resp)
        assert isinstance(err, RateLimitError)
        assert err.retry_after_seconds == 0.0

    def test_5xx_maps_to_unavailable(self) -> None:
        resp = httpx.Response(503, request=httpx.Request("GET", "https://x"))
        err = map_http_error(resp)
        assert isinstance(err, ProviderUnavailable)

    def test_400_maps_to_adapter_error(self) -> None:
        resp = httpx.Response(400, request=httpx.Request("GET", "https://x"))
        err = map_http_error(resp)
        assert isinstance(err, AdapterError)
        assert not isinstance(err, (AuthError, RateLimitError, ProviderUnavailable))


def test_map_transport_error() -> None:
    err = map_transport_error(httpx.ConnectError("boom"))
    assert isinstance(err, ProviderUnavailable)
    assert "boom" in str(err) or "transport" in str(err)