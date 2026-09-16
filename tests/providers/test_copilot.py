"""Tests for the GitHub Copilot adapter (Phase 8c).

The adapter reads a Bearer token from ``copilot.json`` (produced by
``custats login --provider copilot``) and hits
``https://api.github.com/copilot_internal/user`` to get the user's
Copilot plan + per-bucket quota (``monthly_ide_chat``,
``monthly_ide_completions``, ``monthly_agent_chat``). The most
user-visible bucket (``monthly_ide_chat``) drives the menu bar.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from custats.core.models import Provider
from custats.providers.base import (
    AuthError,
    ProviderUnavailable,
    RateLimitError,
)
from custats.providers.copilot import (
    AUTH_JSON_KEY,
    GitHubCopilotAdapter,
    USAGE_URL,
)


def _make_client(
    handler,
    *,
    base_url: str = "https://api.github.com",
) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=base_url)


def _body(
    *,
    plan: str = "business",
    quota_chat: int = 200,
    remaining_chat: int = 187,
    unlimited_chat: bool = False,
    pct_chat: float | None = 93.5,
) -> dict:
    """Realistic ``copilot_internal/user`` response body."""
    bucket: dict = {
        "quota": quota_chat,
        "remaining": remaining_chat,
        "unlimited": unlimited_chat,
    }
    if pct_chat is not None:
        bucket["percent_remaining"] = pct_chat
    return {
        "copilot_plan": plan,
        "access_type_sku": "individual",
        "assigned_date": "2026-01-01",
        "chat_enabled": True,
        "limited_user": False,
        "monthly_ide_chat": bucket,
        "monthly_ide_completions": {
            "quota": 1000,
            "remaining": 720,
            "unlimited": False,
            "percent_remaining": 72.0,
        },
        "monthly_agent_chat": {
            "quota": 500,
            "remaining": 250,
            "unlimited": False,
            "percent_remaining": 50.0,
        },
    }


# ---------------------------------------------------------------------- #
# Happy path + header contract
# ---------------------------------------------------------------------- #


async def test_copilot_happy_path(tmp_path: Path) -> None:
    """Bearer auth + realistic Copilot response → ``five_hour_percent``
    equals the *consumed* percent (100 - ``percent_remaining``)."""
    auth_path = tmp_path / "copilot.json"
    auth_path.write_text(
        json.dumps(
            {"access_token": "ghu_copilot_xyz", "refresh_token": "ghr_copilot"}
        ),
        encoding="utf-8",
    )

    seen_headers: dict[str, str] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen_headers.update(req.headers)
        return httpx.Response(200, json=_body(pct_chat=93.5))

    async with _make_client(handler) as client:
        u = await GitHubCopilotAdapter().fetch(
            {AUTH_JSON_KEY: str(auth_path)},
            client=client,
        )

    assert u.provider is Provider.COPILOT
    assert u.account_id == "copilot-default"
    assert u.five_hour is not None
    # 93.5% remaining → 6.5% used (menu bar displays consumed %).
    assert u.five_hour.five_hour_percent == pytest.approx(6.5)
    # The raw body is preserved for the popup / dashboard.
    assert u.raw is not None
    assert u.raw["copilot_plan"] == "business"
    assert u.raw["monthly_ide_chat"]["quota"] == 200

    # Header contract: Bearer + Accept + X-GitHub-Api-Version + User-Agent.
    assert seen_headers["authorization"] == "Bearer ghu_copilot_xyz"
    assert seen_headers["accept"] == "application/vnd.github+json"
    assert seen_headers["x-github-api-version"] == "2022-11-28"
    assert seen_headers["user-agent"] == "custats-cli/0.1.0"


async def test_copilot_account_id_uses_credential_when_present(tmp_path: Path) -> None:
    """The poller may seed ``account_id`` in the credentials dict;
    propagate it through so the Usage snapshot is keyed correctly."""
    auth_path = tmp_path / "copilot.json"
    auth_path.write_text(json.dumps({"access_token": "t"}), encoding="utf-8")

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_body())

    async with _make_client(handler) as client:
        u = await GitHubCopilotAdapter().fetch(
            {AUTH_JSON_KEY: str(auth_path), "account_id": "acc-copilot-789"},
            client=client,
        )
    assert u.account_id == "acc-copilot-789"


# ---------------------------------------------------------------------- #
# Missing / malformed credentials
# ---------------------------------------------------------------------- #


async def test_copilot_missing_credential_raises_auth() -> None:
    """No ``copilot_auth_json`` key → ``AuthError`` before any HTTP."""
    async with _make_client(lambda req: httpx.Response(200)) as client:
        with pytest.raises(AuthError) as ei:
            await GitHubCopilotAdapter().fetch({}, client=client)
    assert "copilot_auth_json" in str(ei.value)


async def test_copilot_empty_credential_raises_auth() -> None:
    """Empty ``copilot_auth_json`` → ``AuthError`` before any HTTP."""
    async with _make_client(lambda req: httpx.Response(200)) as client:
        with pytest.raises(AuthError):
            await GitHubCopilotAdapter().fetch(
                {AUTH_JSON_KEY: "   "}, client=client
            )


async def test_copilot_auth_json_missing_file_raises_auth(tmp_path: Path) -> None:
    missing = tmp_path / "nope.json"
    async with _make_client(lambda req: httpx.Response(200)) as client:
        with pytest.raises(AuthError) as ei:
            await GitHubCopilotAdapter().fetch(
                {AUTH_JSON_KEY: str(missing)}, client=client
            )
    assert "not found" in str(ei.value).lower()


async def test_copilot_auth_json_missing_token_raises_auth(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"refresh_token": "only"}), encoding="utf-8")
    async with _make_client(lambda req: httpx.Response(200)) as client:
        with pytest.raises(AuthError):
            await GitHubCopilotAdapter().fetch(
                {AUTH_JSON_KEY: str(bad)}, client=client
            )


# ---------------------------------------------------------------------- #
# HTTP error → AdapterError mapping
# ---------------------------------------------------------------------- #


async def test_copilot_401_raises_auth_error(tmp_path: Path) -> None:
    """401 from upstream → ``AuthError``.

    Without a refresh path this is a terminal error — the user must
    re-run ``custats login``. The adapter's refresh path is exercised
    separately in the refresh-on-401 tests below.
    """
    auth_path = tmp_path / "copilot.json"
    # auth.json has NO refresh_token so refresh can't fire — pure 401 path.
    auth_path.write_text(json.dumps({"access_token": "t"}), encoding="utf-8")

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(401)

    async with _make_client(handler) as client:
        with pytest.raises(AuthError):
            await GitHubCopilotAdapter().fetch(
                {AUTH_JSON_KEY: str(auth_path)}, client=client
            )


async def test_copilot_429_maps_to_rate_limit(tmp_path: Path) -> None:
    auth_path = tmp_path / "copilot.json"
    auth_path.write_text(json.dumps({"access_token": "t"}), encoding="utf-8")

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "5"})

    async with _make_client(handler) as client:
        with pytest.raises(RateLimitError) as ei:
            await GitHubCopilotAdapter().fetch(
                {AUTH_JSON_KEY: str(auth_path)}, client=client
            )
    assert ei.value.retry_after_seconds == 5.0


async def test_copilot_503_maps_to_unavailable(tmp_path: Path) -> None:
    auth_path = tmp_path / "copilot.json"
    auth_path.write_text(json.dumps({"access_token": "t"}), encoding="utf-8")

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    async with _make_client(handler) as client:
        with pytest.raises(ProviderUnavailable):
            await GitHubCopilotAdapter().fetch(
                {AUTH_JSON_KEY: str(auth_path)}, client=client
            )


async def test_copilot_502_maps_to_unavailable(tmp_path: Path) -> None:
    """Any 5xx → ``ProviderUnavailable`` (not 401/403)."""
    auth_path = tmp_path / "copilot.json"
    auth_path.write_text(json.dumps({"access_token": "t"}), encoding="utf-8")

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(502)

    async with _make_client(handler) as client:
        with pytest.raises(ProviderUnavailable):
            await GitHubCopilotAdapter().fetch(
                {AUTH_JSON_KEY: str(auth_path)}, client=client
            )


async def test_copilot_transport_error_wraps_as_unavailable(tmp_path: Path) -> None:
    """``httpx.ConnectError`` (network down) → ``ProviderUnavailable``."""
    auth_path = tmp_path / "copilot.json"
    auth_path.write_text(json.dumps({"access_token": "t"}), encoding="utf-8")

    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("nope")

    async with _make_client(handler) as client:
        with pytest.raises(ProviderUnavailable):
            await GitHubCopilotAdapter().fetch(
                {AUTH_JSON_KEY: str(auth_path)}, client=client
            )


# ---------------------------------------------------------------------- #
# Bucket selection + mapping
# ---------------------------------------------------------------------- #


async def test_copilot_unlimited_bucket_returns_zero_used(tmp_path: Path) -> None:
    """``unlimited: true`` → ``five_hour_percent = 0.0``.

    There's nothing to count; the menu bar shows 0% so the user
    doesn't see a phantom bar fill up.
    """
    auth_path = tmp_path / "copilot.json"
    auth_path.write_text(json.dumps({"access_token": "t"}), encoding="utf-8")

    body = _body(unlimited_chat=True, pct_chat=100.0)

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    async with _make_client(handler) as client:
        u = await GitHubCopilotAdapter().fetch(
            {AUTH_JSON_KEY: str(auth_path)}, client=client
        )
    assert u.five_hour is not None
    assert u.five_hour.five_hour_percent == 0.0


async def test_copilot_falls_back_to_agent_bucket(tmp_path: Path) -> None:
    """Missing ``monthly_ide_chat`` → adapter falls back to the next bucket.

    The order is documented: monthly_ide_chat → monthly_agent_chat →
    monthly_ide_completions. The agent bucket here reports 50% remaining
    → 50% used.
    """
    auth_path = tmp_path / "copilot.json"
    auth_path.write_text(json.dumps({"access_token": "t"}), encoding="utf-8")

    body = {
        "copilot_plan": "pro",
        # monthly_ide_chat missing entirely.
        "monthly_agent_chat": {
            "quota": 500,
            "remaining": 250,
            "unlimited": False,
            "percent_remaining": 50.0,
        },
    }

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    async with _make_client(handler) as client:
        u = await GitHubCopilotAdapter().fetch(
            {AUTH_JSON_KEY: str(auth_path)}, client=client
        )
    assert u.five_hour is not None
    assert u.five_hour.five_hour_percent == pytest.approx(50.0)


async def test_copilot_falls_back_to_completions_bucket(tmp_path: Path) -> None:
    """Missing both IDE chat + agent → completions bucket (last resort)."""
    auth_path = tmp_path / "copilot.json"
    auth_path.write_text(json.dumps({"access_token": "t"}), encoding="utf-8")

    body = {
        "copilot_plan": "individual",
        # Only monthly_ide_completions present, with 20% remaining.
        "monthly_ide_completions": {
            "quota": 1000,
            "remaining": 200,
            "unlimited": False,
            "percent_remaining": 20.0,
        },
    }

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    async with _make_client(handler) as client:
        u = await GitHubCopilotAdapter().fetch(
            {AUTH_JSON_KEY: str(auth_path)}, client=client
        )
    assert u.five_hour is not None
    # 20% remaining → 80% used.
    assert u.five_hour.five_hour_percent == pytest.approx(80.0)


async def test_copilot_derives_percent_from_quota_remaining(tmp_path: Path) -> None:
    """Missing ``percent_remaining`` → derive from quota / remaining.

    Some Copilot responses (older API shapes) expose ``quota`` and
    ``remaining`` without a ``percent_remaining`` field. The adapter
    must still surface a useful percentage.
    """
    auth_path = tmp_path / "copilot.json"
    auth_path.write_text(json.dumps({"access_token": "t"}), encoding="utf-8")

    body = {
        "copilot_plan": "pro",
        "monthly_ide_chat": {
            "quota": 200,
            "remaining": 50,  # 25% remaining → 75% used
            "unlimited": False,
            # ``percent_remaining`` deliberately omitted.
        },
    }

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    async with _make_client(handler) as client:
        u = await GitHubCopilotAdapter().fetch(
            {AUTH_JSON_KEY: str(auth_path)}, client=client
        )
    assert u.five_hour is not None
    assert u.five_hour.five_hour_percent == pytest.approx(75.0)


async def test_copilot_missing_bucket_returns_none(tmp_path: Path) -> None:
    """No quota bucket at all (e.g. a free plan with no published
    quotas) → ``five_hour_percent = None`` instead of crashing."""
    auth_path = tmp_path / "copilot.json"
    auth_path.write_text(json.dumps({"access_token": "t"}), encoding="utf-8")

    body = {"copilot_plan": "free", "chat_enabled": False}

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    async with _make_client(handler) as client:
        u = await GitHubCopilotAdapter().fetch(
            {AUTH_JSON_KEY: str(auth_path)}, client=client
        )
    # Either no five_hour at all OR five_hour_percent is None — both are
    # acceptable signals for "no data to display".
    if u.five_hour is not None:
        assert u.five_hour.five_hour_percent is None


async def test_copilot_bucket_missing_quota_returns_none(tmp_path: Path) -> None:
    """A bucket without ``quota`` AND without ``percent_remaining`` →
    ``None`` (we can't derive a percentage without a denominator)."""
    auth_path = tmp_path / "copilot.json"
    auth_path.write_text(json.dumps({"access_token": "t"}), encoding="utf-8")

    body = {
        "copilot_plan": "pro",
        "monthly_ide_chat": {
            # Both fields absent — only ``unlimited`` flag.
            "unlimited": False,
        },
    }

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    async with _make_client(handler) as client:
        u = await GitHubCopilotAdapter().fetch(
            {AUTH_JSON_KEY: str(auth_path)}, client=client
        )
    if u.five_hour is not None:
        assert u.five_hour.five_hour_percent is None


# ---------------------------------------------------------------------- #
# Refresh-on-401 (Phase 8c: GitHub mirror of Codex refresh path)
# ---------------------------------------------------------------------- #


async def test_copilot_refreshes_on_401(tmp_path: Path) -> None:
    """First call returns 401; the adapter refreshes the token and
    retries; the second call (with the fresh token) returns 200."""
    auth_path = tmp_path / "copilot.json"
    auth_path.write_text(
        json.dumps({"access_token": "expired-tok", "refresh_token": "rt-old"}),
        encoding="utf-8",
    )

    call_count = 0
    seen_authorization: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        seen_authorization.append(req.headers.get("authorization", ""))
        if call_count == 1:
            # First request with the stale token → 401.
            return httpx.Response(401)
        if call_count == 2:
            # Second request should hit the refresh endpoint.
            assert req.url.path == "/login/oauth/access_token"
            return httpx.Response(
                200,
                json={
                    "access_token": "fresh-tok",
                    "refresh_token": "rt-fresh",
                    "token_type": "bearer",
                    "scope": "copilot read:user",
                    "expires_in": 28800,
                },
            )
        # Third request: the retried usage call with the new token → 200.
        assert req.url.path == "/copilot_internal/user"
        return httpx.Response(200, json=_body(pct_chat=80.0))

    async with _make_client(handler) as client:
        u = await GitHubCopilotAdapter().fetch(
            {AUTH_JSON_KEY: str(auth_path)}, client=client
        )

    assert call_count == 3, (
        f"expected 3 calls (original 401 + refresh + retry), got {call_count}"
    )
    # First call used the stale token, third used the fresh one.
    assert seen_authorization[0] == "Bearer expired-tok"
    assert seen_authorization[2] == "Bearer fresh-tok"
    # Auth.json on disk was updated with the fresh bundle.
    on_disk = json.loads(auth_path.read_text(encoding="utf-8"))
    assert on_disk["access_token"] == "fresh-tok"
    assert on_disk["refresh_token"] == "rt-fresh"
    assert u.five_hour is not None
    assert u.five_hour.five_hour_percent == pytest.approx(20.0)


async def test_copilot_refresh_missing_refresh_token_raises_auth(
    tmp_path: Path,
) -> None:
    """If the auth.json has no ``refresh_token`` AND the first call 401s,
    the adapter surfaces ``AuthError`` (the user must re-run ``login``)."""
    auth_path = tmp_path / "copilot.json"
    auth_path.write_text(json.dumps({"access_token": "stale"}), encoding="utf-8")

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(401)

    async with _make_client(handler) as client:
        with pytest.raises(AuthError) as ei:
            await GitHubCopilotAdapter().fetch(
                {AUTH_JSON_KEY: str(auth_path)}, client=client
            )
    assert "refresh_token" in str(ei.value)


async def test_copilot_refresh_failure_raises_auth(tmp_path: Path) -> None:
    """The refresh endpoint returns 400 (revoked refresh_token) → the
    adapter surfaces ``AuthError`` rather than silently looping."""
    auth_path = tmp_path / "copilot.json"
    auth_path.write_text(
        json.dumps({"access_token": "stale", "refresh_token": "revoked"}),
        encoding="utf-8",
    )

    call_count = 0

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return httpx.Response(401)
        if call_count == 2:
            return httpx.Response(400, text='{"error":"invalid_grant"}')
        # Defensive — the adapter must NOT retry after a refresh failure.
        return httpx.Response(500)

    async with _make_client(handler) as client:
        with pytest.raises(AuthError) as ei:
            await GitHubCopilotAdapter().fetch(
                {AUTH_JSON_KEY: str(auth_path)}, client=client
            )
    assert "refresh failed" in str(ei.value).lower()
    assert call_count == 2, (
        f"adapter must NOT retry after a refresh failure "
        f"(got {call_count} calls)"
    )


# ---------------------------------------------------------------------- #
# Constants / introspection
# ---------------------------------------------------------------------- #


async def test_copilot_describe_credential_mentions_copilot() -> None:
    text = GitHubCopilotAdapter().describe_credential().lower()
    assert "copilot" in text
    assert "login" in text or "browser" in text


async def test_copilot_url_constant_matches_documented_endpoint() -> None:
    assert "api.github.com" in USAGE_URL
    assert "/copilot_internal/user" in USAGE_URL
