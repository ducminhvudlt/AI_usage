"""Mistral AI usage adapter.

Auth: API key (Mistral La Plateforme console — https://console.mistral.ai).

Primary endpoint:
  GET https://api.mistral.ai/v1/users/me/usage/balance
  Response shape: ``{"balance": <cents int>}``

The ``balance`` field is reported in **cents** of USD credit remaining;
we convert to whole dollars for the popup's *Credits* row. Most
Mistral pay-as-you-go accounts don't expose rolling time-window
quotas via the public API, so the balance endpoint is the canonical
"how much do I have left" signal — both percentage windows are
``None``.
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

USAGE_URL = "https://api.mistral.ai/v1/users/me/usage/balance"


class MistralAdapter:
    """Adapter for Mistral La Plateforme credit balance."""

    provider = Provider.MISTRAL

    def describe_credential(self) -> str:
        return (
            "Mistral La Plateforme API key (console.mistral.ai → API Keys)"
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
                response, default_message="Mistral usage fetch failed"
            )

        body = response.json()
        if not isinstance(body, dict):
            raise AuthError("Mistral usage response is not a JSON object")

        # Account id is provided by the poller; fall back to a
        # deterministic default when called directly (e.g. from a
        # script) so the snapshot is still valid.
        account_id = str(
            credentials.get("account_id") or "mistral-default"
        )

        # Cents → USD. Tolerate missing / non-numeric ``balance`` by
        # leaving ``extra_credits_remaining`` as ``None``.
        cents = body.get("balance")
        usd: float | None
        if isinstance(cents, (int, float)) and not isinstance(cents, bool):
            usd = float(cents) / 100.0
        else:
            usd = None

        return _to_usage(
            account_id,
            Provider.MISTRAL,
            body=body,
            five_hour_pct=None,
            five_hour_resets=None,
            seven_day_pct=None,
            seven_day_resets=None,
            extra_credits=usd,
        )


__all__ = ["API_KEY_KEY", "MistralAdapter", "USAGE_URL"]