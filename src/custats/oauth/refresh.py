"""OAuth refresh-token rotation (RFC 6749 §6).

Used by Codex and ChatGPT (OpenAI's OAuth) today. Generic enough to wrap
any provider that exposes a standard token endpoint.

We refresh on demand — the adapter tries the original request first,
catches 401, swaps in a fresh access_token via this helper, and retries
exactly once. If the refresh itself fails (refresh_token revoked,
network error), the adapter surfaces the original 401 — no infinite
refresh loops, no silent token persistence.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx


class RefreshError(RuntimeError):
    """Raised when the refresh-token exchange fails (revoked, network, etc)."""


# OpenAI's public Codex CLI client_id. Same one ``codex login`` uses.
OPENAI_REFRESH_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"

# GitHub's public OAuth App ID for Copilot integrations. Same value the
# official ``gh`` CLI uses; it's documented in their CLI samples and is
# *not* a secret — the same client_id works for any OAuth-app integration.
GITHUB_REFRESH_CLIENT_ID = "Iv23li5gtHhsd3iHGyqx"

# GitHub requires a User-Agent header on every OAuth call.
_GITHUB_REFRESH_HEADERS: dict[str, str] = {
    "Accept": "application/json",
    "User-Agent": "custats-cli/0.1.0",
}


async def _post_refresh_form(
    *,
    client: httpx.AsyncClient,
    refresh_token: str,
    token_url: str,
    client_id: str,
    extra_form: dict[str, str] | None = None,
    extra_headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """RFC 6749 §6 POST: exchange ``refresh_token`` for a fresh token bundle.

    Returns the full response dict (``access_token``, ``refresh_token``,
    ``expires_in``, …). Raises :class:`RefreshError` on non-200,
    malformed JSON, or missing ``access_token``.
    """
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
        **(extra_form or {}),
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    if extra_headers:
        headers.update(extra_headers)
    response = await client.post(token_url, data=data, headers=headers)
    if response.status_code != 200:
        raise RefreshError(
            f"token refresh returned {response.status_code}: {response.text[:200]}"
        )
    try:
        body = response.json()
    except json.JSONDecodeError as exc:
        raise RefreshError(f"token refresh returned non-JSON: {exc}") from exc
    if "access_token" not in body:
        raise RefreshError(f"token refresh missing access_token: {body!r}")
    return body


async def refresh_openai_token(
    *,
    client: httpx.AsyncClient,
    refresh_token: str,
    token_url: str = "https://auth.openai.com/oauth/token",
    client_id: str = OPENAI_REFRESH_CLIENT_ID,
) -> dict[str, Any]:
    """Exchange an OpenAI ``refresh_token`` for a fresh token bundle."""
    return await _post_refresh_form(
        client=client,
        refresh_token=refresh_token,
        token_url=token_url,
        client_id=client_id,
        extra_form={"scope": "openid profile email offline-access"},
    )


async def refresh_github_token(
    *,
    client: httpx.AsyncClient,
    refresh_token: str,
    token_url: str = "https://github.com/login/oauth/access_token",
    client_id: str = GITHUB_REFRESH_CLIENT_ID,
) -> dict[str, Any]:
    """Exchange a GitHub ``refresh_token`` for a fresh token bundle.

    GitHub's token endpoint accepts the same RFC 6749 §6 form-encoded
    body as OpenAI's, but requires a ``User-Agent`` header (their docs
    explicitly call this out — requests without one are rejected).
    """
    return await _post_refresh_form(
        client=client,
        refresh_token=refresh_token,
        token_url=token_url,
        client_id=client_id,
        extra_headers=_GITHUB_REFRESH_HEADERS,
    )


def read_auth_json(path: Path) -> dict[str, Any]:
    """Read an OAuth auth.json file. Raises :class:`RefreshError` if
    the file is missing or unreadable."""
    if not path.is_file():
        raise RefreshError(f"auth.json not found: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RefreshError(f"auth.json unreadable: {exc}") from exc


def write_auth_json(path: Path, body: dict[str, Any]) -> None:
    """Persist a refreshed token bundle to disk. Sets 0600 perms."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, indent=2), encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass  # best-effort; some filesystems don't support chmod


__all__ = [
    "GITHUB_REFRESH_CLIENT_ID",
    "OPENAI_REFRESH_CLIENT_ID",
    "RefreshError",
    "read_auth_json",
    "refresh_github_token",
    "refresh_openai_token",
    "write_auth_json",
]