"""ChatGPT (chat.openai.com) subscription usage adapter.

Distinct from Codex: ChatGPT Plus / Team / Pro (the chat product) tracks
monthly message caps on top of the rolling 5-hour and weekly windows.
Auth uses the same ``__Secure-next-auth`` session cookie on ``chatgpt.com``,
or a ChatGPT OAuth ``auth.json`` (forward-compat — ChatGPT does not
currently ship a CLI; the path is included for parity with Codex).

Endpoint: ``https://chatgpt.com/backend-api/usage`` — the same backend as
Codex, but the response shape carries a ``monthly`` field for the
per-cycle message cap.

Phase 7c: on 401 from the bearer path we rotate the token via the
OpenAI refresh endpoint (same client_id as Codex), persist the new
bundle to disk, and retry once. Cookie auth does not refresh.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx

from ..core.models import Provider, Usage
from ..oauth.refresh import (
    RefreshError,
    read_auth_json,
    refresh_openai_token,
    write_auth_json,
)
from .base import (
    AuthError,
    _to_usage,
    map_http_error,
    map_transport_error,
    to_percent,
)

USAGE_URL = "https://chatgpt.com/backend-api/usage"
COOKIE_KEY = "chatgpt_cookie"
AUTH_JSON_KEY = "chatgpt_auth_json"


class ChatGPTAdapter:
    provider = Provider.CHATGPT

    def describe_credential(self) -> str:
        return (
            "chatgpt.com __Secure-next-auth session cookie"
            " (or path to a ChatGPT OAuth auth.json file)"
        )

    async def fetch(
        self,
        credentials: dict[str, Any],
        *,
        client: httpx.AsyncClient,
    ) -> Usage:
        cookie = _str_or_none(credentials.get(COOKIE_KEY))
        auth_json_path = _str_or_none(credentials.get(AUTH_JSON_KEY))
        if not cookie and not auth_json_path:
            raise AuthError(
                f"chatgpt requires either {COOKIE_KEY!r} or {AUTH_JSON_KEY!r}"
            )

        headers = _build_headers(credentials)

        try:
            response = await client.get(USAGE_URL, headers=headers)
        except httpx.HTTPError as exc:
            raise map_transport_error(exc) from exc

        # If the bearer token expired, rotate it once and retry.
        if response.status_code == 401 and auth_json_path and not cookie:
            response = await _refresh_and_retry(
                client=client,
                auth_json_path=auth_json_path,
                headers=headers,
            )

        if response.status_code != 200:
            raise map_http_error(response, default_message="ChatGPT usage fetch failed")

        body = response.json()
        if not isinstance(body, dict):
            raise AuthError("ChatGPT usage response is not a JSON object")

        account_id = str(credentials.get("account_id") or "chatgpt-default")
        five_hour = body.get("five_hour") or {}
        seven_day = body.get("seven_day") or {}
        monthly = body.get("monthly") or {}
        extra_usage = body.get("extra_usage") or {}
        # ChatGPT Plus has a single "monthly" message cap; fall back to
        # ``seven_day`` only if the upstream doesn't expose ``monthly``.
        monthly_pct = to_percent(monthly.get("utilization"))
        if monthly_pct is None:
            monthly_pct = to_percent(seven_day.get("monthly_percent"))
        return _to_usage(
            account_id,
            Provider.CHATGPT,
            body=body,
            five_hour_pct=to_percent(five_hour.get("utilization")),
            five_hour_resets=five_hour.get("resets_at"),
            seven_day_pct=to_percent(seven_day.get("utilization")),
            seven_day_resets=seven_day.get("resets_at"),
            extra_credits=_as_float(extra_usage.get("credits_remaining")),
            monthly_percent=monthly_pct,
        )


def _build_headers(credentials: dict[str, Any]) -> dict[str, str]:
    """Assemble the request headers from the ChatGPT credential dict.

    Cookie takes precedence; the bearer path (auth.json) is the one that
    can be refreshed on 401, so the refresh helper only fires when no
    cookie is present.
    """
    cookie = _str_or_none(credentials.get(COOKIE_KEY))
    auth_json_path = _str_or_none(credentials.get(AUTH_JSON_KEY))
    headers: dict[str, str] = {}
    if cookie:
        headers["Cookie"] = cookie
    if auth_json_path:
        headers["Authorization"] = f"Bearer {_read_access_token(auth_json_path)}"
    return headers


async def _refresh_and_retry(
    *,
    client: httpx.AsyncClient,
    auth_json_path: str,
    headers: dict[str, str],
) -> httpx.Response:
    """One-shot token rotation triggered by an upstream 401."""
    path = Path(auth_json_path)
    try:
        blob = read_auth_json(path)
    except RefreshError as exc:
        raise AuthError(
            f"chatgpt 401 and could not read auth.json: {exc}"
        ) from exc

    refresh_token = blob.get("refresh_token")
    if not refresh_token:
        raise AuthError(
            "chatgpt 401: auth.json has no refresh_token — "
            "re-run `custats login` to re-authorize"
        )

    try:
        refreshed = await refresh_openai_token(
            client=client, refresh_token=str(refresh_token)
        )
    except RefreshError as exc:
        raise AuthError(f"chatgpt 401 and refresh failed: {exc}") from exc

    # Persist before retry — a crash here leaves a valid bundle on disk
    # rather than the expired one we just swapped out of memory.
    try:
        write_auth_json(path, refreshed)
    except OSError as exc:  # pragma: no cover — best-effort
        _ = exc

    new_headers = dict(headers)
    new_headers["Authorization"] = f"Bearer {refreshed['access_token']}"
    try:
        return await client.get(USAGE_URL, headers=new_headers)
    except httpx.HTTPError as exc:
        raise map_transport_error(exc) from exc


def _read_access_token(path: str) -> str:
    """Load ``access_token`` from a JSON file. Raises AuthError on any failure."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise AuthError(f"chatgpt auth.json not found: {path}") from exc
    except (json.JSONDecodeError, OSError) as exc:
        raise AuthError(f"chatgpt auth.json unreadable: {exc}") from exc
    if not isinstance(data, dict):
        raise AuthError("chatgpt auth.json must be a JSON object")
    token = data.get("access_token")
    if not isinstance(token, str) or not token.strip():
        raise AuthError("chatgpt auth.json missing 'access_token'")
    return token.strip()


def _str_or_none(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


__all__ = ["AUTH_JSON_KEY", "CHATGPTAdapter", "COOKIE_KEY", "USAGE_URL"]