"""Google Gemini usage adapter.

Gemini's public surface (``generativelanguage.googleapis.com``)
exposes model metadata via the ``models.list`` endpoint but does not
publish a per-account usage / quota API. The adapter therefore
verifies the API key by listing models and returns a :class:`Usage`
snapshot with both window percentages set to ``None`` and the
remaining-credits field populated from ``raw["model_count"]`` only as
a sanity signal — the user's main feedback is "no error = key is
valid". Future Gemini usage endpoints (if Google publishes one) can be
swapped in here without touching other layers.
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

# Public models-list endpoint; the ``?key=`` query param is the
# historically documented auth path. We additionally send the same key
# via the ``x-goog-api-key`` header so the request stays valid even if
# Google flips to header-only auth in the future.
USAGE_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models"
)


class GeminiAdapter:
    """Adapter for Google Gemini usage.

    Gemini does not currently publish a per-account usage / quota API;
    the adapter validates the API key by hitting ``models.list`` and
    returns a snapshot with no window percentages. A user who sees
    "no error" in the tray can treat the key as valid.
    """

    provider = Provider.GEMINI

    def describe_credential(self) -> str:
        return (
            "Google Gemini API key (aistudio.google.com → API keys)"
        )

    async def fetch(
        self,
        credentials: dict[str, Any],
        *,
        client: httpx.AsyncClient,
    ) -> Usage:
        api_key = _require(credentials, API_KEY_KEY)[API_KEY_KEY].strip()
        url = f"{USAGE_URL}?key={api_key}"
        headers = {"x-goog-api-key": api_key}
        try:
            response = await client.get(url, headers=headers)
        except httpx.HTTPError as exc:
            raise map_transport_error(exc) from exc
        if response.status_code != 200:
            raise map_http_error(
                response, default_message="Gemini usage fetch failed"
            )

        body = response.json()
        if not isinstance(body, dict):
            raise AuthError("Gemini usage response is not a JSON object")

        # Account id is provided by the poller when calling fetch;
        # fall back to a deterministic default when called directly
        # (e.g. from a script).
        account_id = str(credentials.get("account_id") or "gemini-default")

        # ``models`` is a list of model metadata dicts; we don't parse
        # any of them — the presence of a non-empty list is the only
        # signal that the key works.
        models = body.get("models")
        model_count = len(models) if isinstance(models, list) else 0
        raw = {"authenticated": True, "model_count": model_count, "body": body}

        return _to_usage(
            account_id,
            Provider.GEMINI,
            body=raw,
            five_hour_pct=None,
            five_hour_resets=None,
            seven_day_pct=None,
            seven_day_resets=None,
            extra_credits=None,
        )


__all__ = ["API_KEY_KEY", "GeminiAdapter", "USAGE_URL"]
