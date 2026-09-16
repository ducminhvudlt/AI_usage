"""GitHub Copilot usage adapter (Phase 8c).

Auth: OAuth device-flow token persisted to ``~/.config/custats/copilot.json``
(``custats login --provider copilot`` runs the browser flow). The adapter
reads ``access_token`` from the JSON blob and uses it as a Bearer against
``https://api.github.com/copilot_internal/user``.

The endpoint returns the user's Copilot plan + per-bucket quota
(``monthly_ide_chat``, ``monthly_ide_completions``, ``monthly_agent_chat``).
We map the most relevant bucket — the IDE chat bucket, which is the one
the menu bar displays — into :class:`Usage.five_hour.five_hour_percent`.

When the bearer token expires (401 from upstream), the adapter calls
:mod:`custats.oauth.refresh` to rotate the token via the GitHub
refresh-token endpoint (``https://github.com/login/oauth/access_token``),
persists the new bundle to ``copilot.json``, and retries exactly once.
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
    refresh_github_token,
    write_auth_json,
)
from .base import (
    AuthError,
    _to_usage,
    map_http_error,
    map_transport_error,
)


USAGE_URL = "https://api.github.com/copilot_internal/user"
AUTH_JSON_KEY = "copilot_auth_json"

# User-Agent GitHub requires on every API call (their docs explicitly
# call this out for the ``copilot_internal`` endpoint family).
_USER_AGENT = "custats-cli/0.1.0"
_API_VERSION = "2022-11-28"


class GitHubCopilotAdapter:
    """Adapter for GitHub Copilot usage quotas."""

    provider = Provider.COPILOT

    def describe_credential(self) -> str:
        return (
            "GitHub Copilot subscription "
            "(run `custats login --provider copilot` to sign in via browser)"
        )

    async def fetch(
        self,
        credentials: dict[str, Any],
        *,
        client: httpx.AsyncClient,
    ) -> Usage:
        # Enforce a non-empty ``copilot_auth_json`` credential key —
        # the adapter never reads the cookie / API-key alternatives
        # Copilot doesn't expose. Missing → AuthError.
        auth_json_path = _str_or_none(credentials.get(AUTH_JSON_KEY))
        if not auth_json_path:
            raise AuthError(
                f"copilot requires {AUTH_JSON_KEY!r} credential "
                f"(run `custats login --provider copilot` first)"
            )

        access_token = _read_access_token(auth_json_path)
        headers = _build_headers(access_token)

        try:
            response = await client.get(USAGE_URL, headers=headers)
        except httpx.HTTPError as exc:
            raise map_transport_error(exc) from exc

        # If the bearer token expired, rotate it once and retry.
        if response.status_code == 401:
            response = await _refresh_and_retry(
                client=client,
                auth_json_path=auth_json_path,
                headers=headers,
            )

        if response.status_code != 200:
            raise map_http_error(
                response, default_message="Copilot usage fetch failed"
            )

        body = response.json()
        if not isinstance(body, dict):
            raise AuthError("Copilot usage response is not a JSON object")

        account_id = str(credentials.get("account_id") or "copilot-default")

        # Pick the most user-visible quota bucket. Order:
        #   1. monthly_ide_chat  — the user-facing IDE chat quota
        #   2. monthly_agent_chat — Copilot Chat / agentic chat quota
        #   3. monthly_ide_completions — code completion quota
        # Displayed as "used" so the menu bar shows consumption %
        # directly. ``unlimited`` buckets are coerced to 0% — there's
        # nothing to show.
        bucket = (
            body.get("monthly_ide_chat")
            or body.get("monthly_agent_chat")
            or body.get("monthly_ide_completions")
            or {}
        )

        five_hour_pct: float | None = _bucket_percent_used(bucket)

        return _to_usage(
            account_id,
            Provider.COPILOT,
            body=body,
            five_hour_pct=five_hour_pct,
        )


def _build_headers(access_token: str) -> dict[str, str]:
    """Assemble the request headers for the Copilot usage endpoint.

    GitHub requires ``Accept: application/vnd.github+json`` AND the
    ``X-GitHub-Api-Version`` header on every API call (their docs
    explicitly call this out for the ``copilot_internal`` family).
    """
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {access_token}",
        "X-GitHub-Api-Version": _API_VERSION,
        "User-Agent": _USER_AGENT,
    }


def _bucket_percent_used(bucket: Any) -> float | None:
    """Map a Copilot quota bucket to the ``used`` percentage.

    Copilot buckets expose ``percent_remaining`` (the user-facing
    "remaining" percentage). The menu bar displays *consumed* percent,
    so we invert: ``used = 100 - remaining``.

    Fallbacks:
    - ``unlimited: true`` → 0.0 (no usage to display)
    - Missing ``percent_remaining`` → derive from ``quota`` / ``remaining``
    - Both missing → ``None`` (no signal to surface)
    """
    if not isinstance(bucket, dict):
        return None

    # ``unlimited`` plans always show 0% — there's nothing to count.
    if bucket.get("unlimited") is True:
        return 0.0

    pct_remaining = _as_float(bucket.get("percent_remaining"))
    if pct_remaining is None:
        # Derive from quota / remaining. The Copilot API emits these
        # in absolute counts (messages or completions); the conversion
        # is `remaining / quota * 100`, which gives the same percent
        # the upstream ``percent_remaining`` would.
        quota = _as_float(bucket.get("quota"))
        remaining = _as_float(bucket.get("remaining"))
        if quota is None or remaining is None or quota <= 0:
            return None
        pct_remaining = (remaining / quota) * 100.0

    # ``percent_remaining`` may already be 0..100 or 0..1; normalise.
    if pct_remaining is None:
        return None
    if pct_remaining <= 1.5:
        pct_remaining *= 100.0
    pct_remaining = max(0.0, min(100.0, pct_remaining))

    used = 100.0 - pct_remaining
    return max(0.0, min(100.0, used))


async def _refresh_and_retry(
    *,
    client: httpx.AsyncClient,
    auth_json_path: str,
    headers: dict[str, str],
) -> httpx.Response:
    """One-shot token rotation triggered by an upstream 401.

    Mirrors the Codex / ChatGPT pattern: read ``copilot.json`` from
    disk, call GitHub's refresh-token endpoint, persist the new bundle
    (so a crash mid-retry still leaves valid tokens), and re-issue the
    original GET with the fresh bearer.
    """
    path = Path(auth_json_path)
    try:
        blob = read_auth_json(path)
    except RefreshError as exc:
        raise AuthError(
            f"copilot 401 and could not read auth.json: {exc}"
        ) from exc

    refresh_token = blob.get("refresh_token")
    if not refresh_token:
        raise AuthError(
            "copilot 401: auth.json has no refresh_token — "
            "re-run `custats login --provider copilot` to re-authorize"
        )

    try:
        refreshed = await refresh_github_token(
            client=client, refresh_token=str(refresh_token)
        )
    except RefreshError as exc:
        raise AuthError(f"copilot 401 and refresh failed: {exc}") from exc

    # Persist before retry — a crash here leaves a valid bundle on disk
    # rather than the expired one we just swapped out of memory.
    try:
        write_auth_json(path, refreshed)
    except OSError as exc:  # pragma: no cover — best-effort
        _ = exc

    new_access = refreshed.get("access_token")
    if not isinstance(new_access, str) or not new_access.strip():
        raise AuthError("copilot refresh returned no access_token")
    new_headers = dict(headers)
    new_headers["Authorization"] = f"Bearer {new_access.strip()}"
    try:
        return await client.get(USAGE_URL, headers=new_headers)
    except httpx.HTTPError as exc:
        raise map_transport_error(exc) from exc


def _read_access_token(path: str) -> str:
    """Load ``access_token`` from a Copilot auth.json file.

    Raises :class:`AuthError` on any failure (missing file, unreadable,
    wrong shape, missing/blank ``access_token``). Mirrors the Codex /
    ChatGPT adapter's contract so the error messages are uniform.
    """
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise AuthError(f"copilot auth.json not found: {path}") from exc
    except (json.JSONDecodeError, OSError) as exc:
        raise AuthError(f"copilot auth.json unreadable: {exc}") from exc
    if not isinstance(data, dict):
        raise AuthError("copilot auth.json must be a JSON object")
    token = data.get("access_token")
    if not isinstance(token, str) or not token.strip():
        raise AuthError("copilot auth.json missing 'access_token'")
    return token.strip()


def _str_or_none(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        # ``bool`` is a subclass of ``int`` — exclude it so ``True``
        # doesn't silently turn into ``1.0``.
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


__all__ = [
    "AUTH_JSON_KEY",
    "GitHubCopilotAdapter",
    "USAGE_URL",
]
