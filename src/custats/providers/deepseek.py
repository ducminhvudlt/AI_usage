"""DeepSeek usage adapter.

DeepSeek publishes a balance endpoint at
``https://api.deepseek.com/user/balance`` that returns the user's
remaining USDT credit. The response shape is::

    {"is_available": true, "balance": ["10.00 USDT"]}

where ``balance`` is a list of strings, each ``"<amount> <currency>"``.
We sum the numeric prefix of every entry and surface the total as
``extra_credits_remaining``. There is no per-window percentage, so
both ``five_hour`` and ``seven_day`` are ``None``.
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

USAGE_URL = "https://api.deepseek.com/user/balance"


class DeepSeekAdapter:
    """Adapter for DeepSeek remaining credit balance."""

    provider = Provider.DEEPSEEK

    def describe_credential(self) -> str:
        return (
            "DeepSeek API key (platform.deepseek.com → API keys)"
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
                response, default_message="DeepSeek usage fetch failed"
            )

        body = response.json()
        if not isinstance(body, dict):
            raise AuthError("DeepSeek usage response is not a JSON object")

        balance_obj = body.get("balance")
        balance_list = balance_obj if isinstance(balance_obj, list) else []
        total: float | None = None
        for entry in balance_list:
            text = str(entry).split()
            try:
                amount = float(text[0])
            except (ValueError, IndexError):
                continue
            total = amount if total is None else total + amount

        account_id = str(
            credentials.get("account_id") or "deepseek-default"
        )

        return _to_usage(
            account_id,
            Provider.DEEPSEEK,
            body=body,
            five_hour_pct=None,
            five_hour_resets=None,
            seven_day_pct=None,
            seven_day_resets=None,
            extra_credits=total,
        )


__all__ = ["API_KEY_KEY", "DeepSeekAdapter", "USAGE_URL"]
