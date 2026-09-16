"""xAI Grok usage adapter.

Reads ``~/.grok/auth.json`` (or an in-memory equivalent that the
storage layer decrypted). We use ``access_token`` as a Bearer token
and **never** write back any refresh token — Grok's auth model is
read-only from custats' perspective; ``grok login`` on the user's
machine is responsible for refreshing tokens.
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
    to_percent,
)

# ``api.x.ai`` is the documented public usage endpoint. The URL is
# hard-coded so swapping is one constant.
USAGE_URL = "https://api.x.ai/v1/usage"

ACCESS_TOKEN_KEY = "access_token"
# ``refresh_token`` is accepted but explicitly ignored. Kept in the
# credential dict so the storage layer doesn't have to know it's
# unused.
REFRESH_TOKEN_KEY = "refresh_token"

_ALLOWED_WINDOWS = ("weekly", "monthly")


class GrokAdapter:
    """Adapter for xAI Grok usage limits."""

    provider = Provider.GROK

    def describe_credential(self) -> str:
        return "~/.grok/auth.json contents (read by custats, never rotated)"

    async def fetch(
        self,
        credentials: dict[str, Any],
        *,
        client: httpx.AsyncClient,
    ) -> Usage:
        access_token = _require(credentials, ACCESS_TOKEN_KEY)[ACCESS_TOKEN_KEY].strip()
        headers = {"Authorization": f"Bearer {access_token}"}

        try:
            response = await client.get(USAGE_URL, headers=headers)
        except httpx.HTTPError as exc:
            raise map_transport_error(exc) from exc
        # TODO: Grok refresh not yet wired — see custats-linux issue #7c
        if response.status_code != 200:
            raise map_http_error(response, default_message="Grok usage fetch failed")

        body = response.json()
        if not isinstance(body, dict):
            raise AuthError("Grok usage response is not a JSON object")

        account_id = str(credentials.get("account_id") or "grok-default")

        # ``window`` may be 'weekly' or 'monthly'. Anything else is
        # dropped — ProviderLimits.grok_window is a Literal and an
        # unknown value would explode the dataclass.
        raw_window = body.get("window")
        window = raw_window if raw_window in _ALLOWED_WINDOWS else None

        five_hour = body.get("five_hour") or {}
        seven_day = body.get("seven_day") or {}
        return _to_usage(
            account_id,
            Provider.GROK,
            body=body,
            five_hour_pct=to_percent(five_hour.get("utilization")),
            five_hour_resets=five_hour.get("resets_at"),
            seven_day_pct=to_percent(seven_day.get("utilization")),
            seven_day_resets=seven_day.get("resets_at"),
            grok_window=window,
        )


__all__ = [
    "ACCESS_TOKEN_KEY",
    "GrokAdapter",
    "REFRESH_TOKEN_KEY",
    "USAGE_URL",
]