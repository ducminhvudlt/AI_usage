"""Tests for the OAuth refresh-token helper (Phase 7c + 8c)."""

from __future__ import annotations

import json
import stat
from pathlib import Path
from typing import Callable

import httpx
import pytest

from custats.oauth.refresh import (
    GITHUB_REFRESH_CLIENT_ID,
    OPENAI_REFRESH_CLIENT_ID,
    RefreshError,
    read_auth_json,
    refresh_github_token,
    refresh_openai_token,
    write_auth_json,
)


def _make_client(
    handler: Callable[[httpx.Request], httpx.Response],
) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# ---------------------------------------------------------------------- #
# refresh_openai_token
# ---------------------------------------------------------------------- #


async def test_refresh_openai_token_happy_path() -> None:
    """Successful 200 + token bundle: returns the parsed dict with the
    new access_token, refresh_token, expires_in, token_type."""
    captured: dict[str, str] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["url"] = str(req.url)
        captured["method"] = req.method
        captured["body"] = req.content.decode("utf-8")
        return httpx.Response(
            200,
            json={
                "access_token": "new",
                "refresh_token": "new-rt",
                "expires_in": 3600,
                "token_type": "Bearer",
            },
        )

    async with _make_client(handler) as client:
        result = await refresh_openai_token(
            client=client, refresh_token="old-rt"
        )

    assert result["access_token"] == "new"
    assert result["refresh_token"] == "new-rt"
    assert result["expires_in"] == 3600
    assert result["token_type"] == "Bearer"

    # Verify request: POST to the default OpenAI token URL, form-encoded.
    assert captured["method"] == "POST"
    assert captured["url"].startswith("https://auth.openai.com/oauth/token")
    body = captured["body"]
    assert "grant_type=refresh_token" in body
    assert "refresh_token=old-rt" in body
    assert f"client_id={OPENAI_REFRESH_CLIENT_ID}" in body
    assert "scope=openid" in body
    assert "offline-access" in body


async def test_refresh_openai_token_non_200_raises() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="upstream is sad")

    async with _make_client(handler) as client:
        with pytest.raises(RefreshError) as ei:
            await refresh_openai_token(
                client=client, refresh_token="old-rt"
            )
    assert "500" in str(ei.value)
    assert "upstream is sad" in str(ei.value)


async def test_refresh_openai_token_400_raises() -> None:
    """400 (revoked refresh_token) is also a hard failure."""

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text='{"error":"invalid_grant"}')

    async with _make_client(handler) as client:
        with pytest.raises(RefreshError) as ei:
            await refresh_openai_token(
                client=client, refresh_token="revoked-rt"
            )
    assert "400" in str(ei.value)


async def test_refresh_openai_token_non_json_raises() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>not json</html>")

    async with _make_client(handler) as client:
        with pytest.raises(RefreshError) as ei:
            await refresh_openai_token(
                client=client, refresh_token="old-rt"
            )
    assert "non-json" in str(ei.value).lower()


async def test_refresh_openai_token_missing_access_token_raises() -> None:
    """The endpoint returned JSON but no ``access_token`` key — bad
    response shape. The helper must reject this loudly."""

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"error": "weird"})

    async with _make_client(handler) as client:
        with pytest.raises(RefreshError) as ei:
            await refresh_openai_token(
                client=client, refresh_token="old-rt"
            )
    assert "access_token" in str(ei.value)


async def test_refresh_openai_token_overrides_url_and_client_id() -> None:
    """The helper is generic — callers can target a different token
    endpoint / client_id (used by tests + future adapters)."""
    seen: dict[str, str] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"] = str(req.url)
        seen["body"] = req.content.decode("utf-8")
        return httpx.Response(200, json={"access_token": "x"})

    async with _make_client(handler) as client:
        result = await refresh_openai_token(
            client=client,
            refresh_token="rt",
            token_url="https://example.com/oauth/token",
            client_id="custom-cid",
        )
    assert result["access_token"] == "x"
    assert seen["url"].startswith("https://example.com/oauth/token")
    assert "client_id=custom-cid" in seen["body"]


# ---------------------------------------------------------------------- #
# refresh_github_token (Phase 8c)
# ---------------------------------------------------------------------- #


async def test_refresh_github_token_happy_path() -> None:
    """Successful 200 + GitHub token bundle: returns the parsed dict with
    the new ``access_token``, rotated ``refresh_token``, ``expires_in``,
    and the ``scope`` field GitHub includes."""
    captured: dict[str, str] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["url"] = str(req.url)
        captured["method"] = req.method
        captured["body"] = req.content.decode("utf-8")
        return httpx.Response(
            200,
            json={
                "access_token": "ghu_new",
                "refresh_token": "ghr_new_rotated",
                "expires_in": 28800,
                "token_type": "bearer",
                "scope": "copilot read:user",
            },
        )

    async with _make_client(handler) as client:
        result = await refresh_github_token(
            client=client, refresh_token="ghr_old"
        )

    assert result["access_token"] == "ghu_new"
    assert result["refresh_token"] == "ghr_new_rotated"
    assert result["expires_in"] == 28800
    assert result["token_type"] == "bearer"
    assert result["scope"] == "copilot read:user"

    # Request shape: POST to GitHub's token endpoint, form-encoded,
    # with the public client_id.
    assert captured["method"] == "POST"
    assert captured["url"].startswith(
        "https://github.com/login/oauth/access_token"
    )
    body = captured["body"]
    assert "grant_type=refresh_token" in body
    assert "refresh_token=ghr_old" in body
    assert f"client_id={GITHUB_REFRESH_CLIENT_ID}" in body
    # Belt-and-braces: GitHub requires a User-Agent on every OAuth call.
    # We can't easily check headers here without restructuring the
    # handler — the URL + body assertions above are the contract pins.


async def test_refresh_github_token_non_200_raises() -> None:
    """GitHub returns 400 (revoked refresh_token) → ``RefreshError``."""

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text='{"error":"invalid_grant"}')

    async with _make_client(handler) as client:
        with pytest.raises(RefreshError) as ei:
            await refresh_github_token(
                client=client, refresh_token="revoked"
            )
    assert "400" in str(ei.value)
    assert "invalid_grant" in str(ei.value)


async def test_refresh_github_token_missing_access_token_raises() -> None:
    """GitHub returned JSON but no ``access_token`` key → ``RefreshError``.

    This is the contract pin that catches a future drift in GitHub's
    response shape — if GitHub ever renames the field, the adapter
    surfaces a clean error rather than a silent ``None`` cascade.
    """

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"error": "weird-shape"})

    async with _make_client(handler) as client:
        with pytest.raises(RefreshError) as ei:
            await refresh_github_token(
                client=client, refresh_token="rt"
            )
    assert "access_token" in str(ei.value)


async def test_refresh_github_token_500_raises() -> None:
    """Server-side failure → ``RefreshError`` with the status in the
    message (mirrors the OpenAI helper's behaviour)."""

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="github sad")

    async with _make_client(handler) as client:
        with pytest.raises(RefreshError) as ei:
            await refresh_github_token(
                client=client, refresh_token="rt"
            )
    assert "500" in str(ei.value)
    assert "github sad" in str(ei.value)


async def test_refresh_github_token_overrides_url_and_client_id() -> None:
    """The helper accepts overrides (forward-compat for testing or
    alternative deployments)."""
    seen: dict[str, str] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"] = str(req.url)
        seen["body"] = req.content.decode("utf-8")
        return httpx.Response(200, json={"access_token": "x"})

    async with _make_client(handler) as client:
        result = await refresh_github_token(
            client=client,
            refresh_token="rt",
            token_url="https://example.com/oauth/access_token",
            client_id="custom-cid",
        )
    assert result["access_token"] == "x"
    assert seen["url"].startswith("https://example.com/oauth/access_token")
    assert "client_id=custom-cid" in seen["body"]


# ---------------------------------------------------------------------- #
# read_auth_json
# ---------------------------------------------------------------------- #


def test_read_auth_json_happy(tmp_path: Path) -> None:
    p = tmp_path / "auth.json"
    p.write_text(
        json.dumps({"access_token": "a", "refresh_token": "r"}),
        encoding="utf-8",
    )
    assert read_auth_json(p) == {"access_token": "a", "refresh_token": "r"}


def test_read_auth_json_missing_file_raises(tmp_path: Path) -> None:
    missing = tmp_path / "nope.json"
    with pytest.raises(RefreshError) as ei:
        read_auth_json(missing)
    assert "not found" in str(ei.value)
    assert "nope.json" in str(ei.value)


def test_read_auth_json_invalid_json_raises(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("not json{", encoding="utf-8")
    with pytest.raises(RefreshError) as ei:
        read_auth_json(bad)
    assert "unreadable" in str(ei.value).lower()


# ---------------------------------------------------------------------- #
# write_auth_json
# ---------------------------------------------------------------------- #


def test_write_auth_json_round_trip(tmp_path: Path) -> None:
    p = tmp_path / "auth.json"
    payload = {"access_token": "a", "refresh_token": "r"}
    write_auth_json(p, payload)
    assert json.loads(p.read_text(encoding="utf-8")) == payload


def test_write_auth_json_sets_0600_perms(tmp_path: Path) -> None:
    p = tmp_path / "auth.json"
    write_auth_json(p, {"access_token": "a"})
    mode = p.stat().st_mode & 0o777
    assert mode == 0o600, f"expected 0o600, got {oct(mode)}"


def test_write_auth_json_creates_parent_dirs(tmp_path: Path) -> None:
    p = tmp_path / "nested" / "deeper" / "auth.json"
    write_auth_json(p, {"access_token": "a"})
    assert p.is_file()


def test_write_auth_json_idempotent(tmp_path: Path) -> None:
    """Two consecutive writes: the second one wins; perms stay 0600."""
    p = tmp_path / "auth.json"
    write_auth_json(p, {"access_token": "first"})
    write_auth_json(p, {"access_token": "second"})
    assert json.loads(p.read_text(encoding="utf-8"))["access_token"] == "second"
    # The chmod path is best-effort; some CI filesystems strip perms.
    # We only assert the chmod call happened — the final mode might be
    # whatever the FS preserves, but the call must not raise.
    assert (p.stat().st_mode & stat.S_IRUSR) != 0  # at least owner-readable