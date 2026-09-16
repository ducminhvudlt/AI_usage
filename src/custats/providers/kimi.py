"""Kimi (Moonshot AI) usage adapter.

Auth: Moonshot API key (https://platform.moonshot.cn console — same
key the Kimi CLI uses after ``kimi login``).

Primary endpoint:
  GET https://api.moonshot.cn/v1/dashboard/billing/credit
  Response shape varies; the canonical fields are::
      {"code": 0, "data": {"balance": <cents>, "credit": <cents>}, ...}

Kimi doesn't publish a rolling time-window quota via the public API,
so both percentage windows are ``None``. The adapter tolerates two
shapes: a wrapped form (``data.balance``) and an unwrapped legacy
form (``balance`` at the top level). Cents → USD.
"""
from __future__ import annotations

from typing import Any

import httpx

from ..core.models import Provider, Usage
from .base import (
    AuthError,
    _require,
    _to_usage,
    map_http_error,
    map_transport_error,
)

API_KEY_KEY = "api_key"

USAGE_URL = "https://api.moonshot.cn/v1/dashboard/billing/credit"


class KimiAdapter:
    """Adapter for Kimi / Moonshot AI credit balance."""

    provider = Provider.KIMI

    def describe_credential(self) -> str:
        return (
            "Moonshot / Kimi API key (platform.moonshot.cn — same key "
            "as `kimi login`)"
        )

    async def fetch(
        self,
        credentials: dict[str, Any],
        *,
        client: httpx.AsyncClient,
    ) -> Usage:
        api_key = _require(credentials, API_KEY_KEY)[API_KEY_KEY].strip()
        headers = {"Authorization": f"Bearer {api_key}"}
        try:
            response = await client.get(USAGE_URL, headers=headers)
        except httpx.HTTPError as exc:
            raise map_transport_error(exc) from exc
        if response.status_code != 200:
            raise map_http_error(
                response, default_message="Kimi usage fetch failed"
            )

        body = response.json()
        if not isinstance(body, dict):
            raise AuthError("Kimi usage response is not a JSON object")

        # Account id is provided by the poller; fall back to a
        # deterministic default when called directly so the snapshot
        # is still valid.
        account_id = str(credentials.get("account_id") or "kimi-default")

        # Cents → USD. Try the documented ``data.balance`` /
        # ``data.credit`` first, then the unwrapped legacy shape.
        cents: float | None = None
        data_obj = body.get("data")
        if isinstance(data_obj, dict):
            for key in ("balance", "credit"):
                value = data_obj.get(key)
                if isinstance(value, (int, float)) and not isinstance(
                    value, bool
                ):
                    cents = float(value)
                    break
        if cents is None:
            for key in ("balance", "credit"):
                value = body.get(key)
                if isinstance(value, (int, float)) and not isinstance(
                    value, bool
                ):
                    cents = float(value)
                    break
        usd = cents / 100.0 if cents is not None else None

        return _to_usage(
            account_id,
            Provider.KIMI,
            body=body,
            five_hour_pct=None,
            five_hour_resets=None,
            seven_day_pct=None,
            seven_day_resets=None,
            extra_credits=usd,
        )


__all__ = ["API_KEY_KEY", "KimiAdapter", "USAGE_URL"]