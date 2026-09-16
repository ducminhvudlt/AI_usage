"""Claude usage adapter.

Hits ``claude.ai/api/organizations/me/usage`` with the user's
``sessionKey`` cookie. The endpoint returns per-window utilization
percentages and ISO 8601 reset timestamps.

Both windows are normally present; if a plan ever omits the seven-day
window (Claude Pro has historically done this) we still return a
valid :class:`Usage` with ``seven_day=None``.
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

# Endpoint URL is hard-coded so it's trivial to swap if Claude changes
# it. The ``me`` alias resolves to the current user's primary org.
USAGE_URL = "https://claude.ai/api/organizations/me/usage"

ACCOUNT_ID_KEY = "session_key"


class ClaudeAdapter:
    """Adapter for Anthropic Claude usage limits."""

    provider = Provider.CLAUDE

    def describe_credential(self) -> str:
        return "claude.ai sessionKey cookie value"

    async def fetch(
        self,
        credentials: dict[str, Any],
        *,
        client: httpx.AsyncClient,
    ) -> Usage:
        session_key = _require(credentials, ACCOUNT_ID_KEY)[ACCOUNT_ID_KEY]
        headers = {"Cookie": f"sessionKey={session_key}"}
        try:
            response = await client.get(USAGE_URL, headers=headers)
        except httpx.HTTPError as exc:
            raise map_transport_error(exc) from exc
        if response.status_code != 200:
            raise map_http_error(response, default_message="Claude usage fetch failed")

        body = response.json()
        if not isinstance(body, dict):
            raise AuthError("Claude usage response is not a JSON object")

        account_id = str(credentials.get("account_id") or "claude-default")
        five_hour = body.get("five_hour") or {}
        seven_day = body.get("seven_day") or {}

        return _to_usage(
            account_id,
            Provider.CLAUDE,
            body=body,
            five_hour_pct=to_percent(five_hour.get("utilization")),
            five_hour_resets=five_hour.get("resets_at"),
            # ``seven_day`` may be missing entirely on some plans.
            seven_day_pct=to_percent(seven_day.get("utilization"))
            if isinstance(seven_day, dict)
            else None,
            seven_day_resets=seven_day.get("resets_at")
            if isinstance(seven_day, dict)
            else None,
        )


__all__ = ["ACCOUNT_ID_KEY", "ClaudeAdapter", "USAGE_URL"]