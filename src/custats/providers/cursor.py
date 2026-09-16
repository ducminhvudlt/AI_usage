"""Cursor usage adapter.

Cursor uses a ``WorkosCursor`` cookie on ``cursor.com``. The cookie
does not refresh — there's no programmatic API for it — so custats
simply re-reads whatever credential the user pasted and surfaces an
:class:`AuthError` on 401, telling them to re-paste.

Cursor has only a 7-day window; the 5-hour field is always ``None``.
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

# Public usage endpoint. Hard-coded so swapping is trivial.
USAGE_URL = "https://www.cursor.com/api/usage"

COOKIE_KEY = "workos_cursor_cookie"


class CursorAdapter:
    """Adapter for Cursor usage limits."""

    provider = Provider.CURSOR

    def describe_credential(self) -> str:
        return (
            "cursor.com WorkosCursor cookie value"
            " (cookies do not refresh — re-paste if expired)"
        )

    async def fetch(
        self,
        credentials: dict[str, Any],
        *,
        client: httpx.AsyncClient,
    ) -> Usage:
        cookie = _require(credentials, COOKIE_KEY)[COOKIE_KEY].strip()
        headers = {"Cookie": f"WorkosCursor={cookie}"}

        try:
            response = await client.get(USAGE_URL, headers=headers)
        except httpx.HTTPError as exc:
            raise map_transport_error(exc) from exc
        if response.status_code == 401:
            raise AuthError(
                "Cursor cookie rejected (401); cookies do not refresh — "
                "re-paste a fresh WorkosCursor value"
            )
        if response.status_code != 200:
            raise map_http_error(response, default_message="Cursor usage fetch failed")

        body = response.json()
        if not isinstance(body, dict):
            raise AuthError("Cursor usage response is not a JSON object")

        account_id = str(credentials.get("account_id") or "cursor-default")

        # Cursor only exposes a 7-day window.
        seven_day = body.get("seven_day") or {}
        return _to_usage(
            account_id,
            Provider.CURSOR,
            body=body,
            five_hour_pct=None,
            five_hour_resets=None,
            seven_day_pct=to_percent(seven_day.get("utilization"))
            if isinstance(seven_day, dict)
            else None,
            seven_day_resets=seven_day.get("resets_at")
            if isinstance(seven_day, dict)
            else None,
        )


__all__ = ["COOKIE_KEY", "CursorAdapter", "USAGE_URL"]