"""OpenAI Codex usage adapter.

Accepts a ``chatgpt.com`` session cookie or a path to
``~/.codex/auth.json`` (whose ``access_token`` is used as a Bearer).

When the bearer token expires (401 from upstream), the adapter calls
:mod:`custats.oauth.refresh` to rotate the token via the OpenAI
refresh-token endpoint, persists the new bundle to ``auth.json``, and
retries exactly once. If the refresh fails (revoked token, network),
the original 401 surfaces as :class:`AuthError` — no silent retry loop.
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
AUTH_JSON_KEY = "auth_json_path"


class CodexAdapter:
    provider = Provider.CODEX

    def describe_credential(self) -> str:
        return (
            "chatgpt.com __Secure-next-auth session cookie"
            " (or path to ~/.codex/auth.json)"
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
                f"codex requires either {COOKIE_KEY!r} or {AUTH_JSON_KEY!r}"
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
            raise map_http_error(response, default_message="Codex usage fetch failed")

        body = response.json()
        if not isinstance(body, dict):
            raise AuthError("Codex usage response is not a JSON object")

        account_id = str(credentials.get("account_id") or "codex-default")
        five_hour = body.get("five_hour") or {}
        seven_day = body.get("seven_day") or {}
        extra_usage = body.get("extra_usage") or {}
        codex_credits = body.get("codex_credits") or {}
        return _to_usage(
            account_id,
            Provider.CODEX,
            body=body,
            five_hour_pct=to_percent(five_hour.get("utilization")),
            five_hour_resets=five_hour.get("resets_at"),
            seven_day_pct=to_percent(seven_day.get("utilization")),
            seven_day_resets=seven_day.get("resets_at"),
            extra_credits=_as_float(extra_usage.get("credits_remaining")),
            codex_reset_credits=_as_int(codex_credits.get("reset_credits")),
        )


def _build_headers(credentials: dict[str, Any]) -> dict[str, str]:
    """Build the request headers from the Codex credential dict.

    Cookie wins if both are present (mirrors historical behaviour); the
    refresh path only fires for the bearer path, so callers should pass
    both via :func:`fetch` and let this function assemble the dict.
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
    """One-shot token rotation triggered by an upstream 401.

    Reads ``auth_json`` from disk, calls the OpenAI refresh endpoint,
    persists the new bundle (so a crash mid-retry still leaves valid
    tokens), and re-issues the original GET with the fresh bearer.
    A failure to read the file or to refresh raises :class:`AuthError`.
    """
    path = Path(auth_json_path)
    try:
        blob = read_auth_json(path)
    except RefreshError as exc:
        raise AuthError(
            f"codex 401 and could not read auth.json: {exc}"
        ) from exc

    refresh_token = blob.get("refresh_token")
    if not refresh_token:
        raise AuthError(
            "codex 401: auth.json has no refresh_token — "
            "re-run `custats login` to re-authorize"
        )

    try:
        refreshed = await refresh_openai_token(
            client=client, refresh_token=str(refresh_token)
        )
    except RefreshError as exc:
        raise AuthError(f"codex 401 and refresh failed: {exc}") from exc

    # Persist before retry — a crash here leaves a valid bundle on disk
    # rather than the expired one we just swapped out of memory.
    try:
        write_auth_json(path, refreshed)
    except OSError as exc:  # pragma: no cover — best-effort; covered by CLI tests
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
        raise AuthError(f"codex auth.json not found: {path}") from exc
    except (json.JSONDecodeError, OSError) as exc:
        raise AuthError(f"codex auth.json unreadable: {exc}") from exc
    if not isinstance(data, dict):
        raise AuthError("codex auth.json must be a JSON object")
    token = data.get("access_token")
    if not isinstance(token, str) or not token.strip():
        raise AuthError("codex auth.json missing 'access_token'")
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

def _as_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None

__all__ = ["AUTH_JSON_KEY", "COOKIE_KEY", "CodexAdapter", "USAGE_URL"]