"""OpenRouter usage adapter.

OpenRouter exposes a per-key usage endpoint at
``https://openrouter.ai/api/v1/auth/key`` that returns the dollar
amount charged so far this billing cycle (``usage``) and the credit
limit (``limit``, in cents). Both fields are cents-based integers; we
surface the percentage used in ``five_hour`` (it's the only window
OpenRouter exposes) and the remaining dollar credit in
``extra_credits_remaining``.
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

USAGE_URL = "https://openrouter.ai/api/v1/auth/key"


class OpenRouterAdapter:
    """Adapter for OpenRouter per-key credit usage."""

    provider = Provider.OPENROUTER

    def describe_credential(self) -> str:
        return (
            "OpenRouter API key (openrouter.ai → Keys); the adapter "
            "only reads usage, never calls paid inference endpoints"
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
                response, default_message="OpenRouter usage fetch failed"
            )

        body = response.json()
        if not isinstance(body, dict):
            raise AuthError("OpenRouter usage response is not a JSON object")

        # ``data`` is the documented wrapper; older responses may omit
        # it. Fall back to the top-level object so we don't break on
        # a minor API drift.
        data_obj = body.get("data")
        data: dict[str, Any] = data_obj if isinstance(data_obj, dict) else body

        usage_cents = _as_float(data.get("usage")) or 0.0
        limit_cents = _as_float(data.get("limit")) or 0.0
        if limit_cents > 0:
            pct = max(0.0, min(100.0, usage_cents / limit_cents * 100.0))
            remaining_dollars = max(0.0, (limit_cents - usage_cents) / 100.0)
        else:
            pct = None
            remaining_dollars = None

        account_id = str(
            credentials.get("account_id") or "openrouter-default"
        )

        return _to_usage(
            account_id,
            Provider.OPENROUTER,
            body=body,
            five_hour_pct=pct,
            five_hour_resets=None,
            seven_day_pct=None,
            seven_day_resets=None,
            extra_credits=remaining_dollars,
        )


def _as_float(value: Any) -> float | None:
    """Coerce ``value`` to ``float``; return ``None`` on anything weird."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


__all__ = ["API_KEY_KEY", "OpenRouterAdapter", "USAGE_URL"]
