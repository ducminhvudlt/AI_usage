"""Shared interface and helpers for provider adapters.

Every adapter in :mod:`custats.providers` implements the
:class:`ProviderAdapter` protocol. The adapter is given an already
authenticated ``httpx.AsyncClient`` (the caller controls cookies,
headers, timeouts) and is expected to return a fresh :class:`Usage`
snapshot.

Adapters are **never** responsible for credential storage or refresh —
the poller (Phase 3) loads credentials from the encrypted store and
re-reads them on every poll. This keeps the adapters small and easy
to test.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

import httpx

from ..core.models import Provider, ProviderLimits, Usage
from ..core.time_utils import now_utc

__all__ = [
    "AdapterError",
    "AuthError",
    "ProviderAdapter",
    "ProviderUnavailable",
    "RateLimitError",
    "map_http_error",
    "to_percent",
]


# ---------------------------------------------------------------------- #
# Errors
# ---------------------------------------------------------------------- #


class AdapterError(Exception):
    """Base class for any adapter-level failure.

    Catch this in the poller to log a problem and move on without
    crashing the whole monitoring loop.
    """


class AuthError(AdapterError):
    """The credential was rejected (401/403) or missing required fields.

    Surfaces as a user-visible prompt to refresh / re-paste credentials.
    """


class RateLimitError(AdapterError):
    """The upstream returned 429.

    Carries an optional ``retry_after_seconds`` hint from the
    ``Retry-After`` header so the poller can back off accordingly.
    """

    retry_after_seconds: float = 0.0

    def __init__(self, message: str, *, retry_after_seconds: float = 60.0) -> None:
        super().__init__(message)
        self.retry_after_seconds = max(0.0, float(retry_after_seconds))


class ProviderUnavailable(AdapterError):
    """The upstream is down or unreachable.

    5xx responses and ``httpx.HTTPError`` (connection resets, DNS
    failures, timeouts) all map here. The poller should retry on its
    normal cadence and never crash.
    """


# ---------------------------------------------------------------------- #
# Protocol
# ---------------------------------------------------------------------- #


@runtime_checkable
class ProviderAdapter(Protocol):
    """Async fetcher for one provider's current usage snapshot."""

    provider: Provider

    async def fetch(
        self,
        credentials: dict[str, Any],
        *,
        client: httpx.AsyncClient,
    ) -> Usage:
        """Return a fresh :class:`Usage` snapshot, or raise an
        :class:`AdapterError` subclass.

        ``credentials`` is a dict the storage layer decrypted; the
        adapter chooses which keys it needs and raises
        :class:`AuthError` if they're missing.
        ``client`` is an :class:`httpx.AsyncClient` the caller owns;
        do not close it.
        """
        ...

    def describe_credential(self) -> str:
        """One-sentence human description of what credential this needs.

        Used by ``custats add`` to prompt the user and by ``doctor``
        to explain a missing credential.
        """
        ...


# ---------------------------------------------------------------------- #
# Helpers used by every concrete adapter
# ---------------------------------------------------------------------- #


def _require(credentials: dict[str, Any], *keys: str) -> dict[str, Any]:
    """Return ``credentials`` if every key in ``keys`` is present and non-empty.

    Empty strings, ``None``, and missing keys are all rejected. The
    ``AuthError`` message names the missing key so the user knows what
    to re-paste.
    """
    if not isinstance(credentials, dict):
        raise AuthError("credentials must be a dict")
    missing = [
        key
        for key in keys
        if key not in credentials
        or credentials[key] is None
        or (isinstance(credentials[key], str) and not credentials[key].strip())
    ]
    if missing:
        joined = ", ".join(repr(k) for k in missing)
        raise AuthError(f"missing required credential key(s): {joined}")
    return credentials


def _to_usage(
    account_id: str,
    provider: Provider,
    *,
    body: dict[str, Any],
    five_hour_pct: float | None = None,
    five_hour_resets: Any = None,
    seven_day_pct: float | None = None,
    seven_day_resets: Any = None,
    extra_credits: float | None = None,
    grok_window: str | None = None,
    codex_reset_credits: int | None = None,
    monthly_percent: float | None = None,
) -> Usage:
    """Build a :class:`Usage` snapshot from already-parsed fields.

    Centralises the boilerplate (timezone conversion, ``raw`` capture,
    ``fetched_at`` stamp) so every adapter stays small and consistent.
    Extra window metadata is folded into the appropriate
    :class:`ProviderLimits` (e.g. ``grok_window``).
    """
    from ..core.time_utils import parse_iso  # local import: avoids cycles in tests

    five_hour: ProviderLimits | None = None
    if five_hour_pct is not None or grok_window is not None:
        five_hour = ProviderLimits(
            five_hour_percent=five_hour_pct,
            grok_window=grok_window,  # type: ignore[arg-type]
        )

    seven_day: ProviderLimits | None = None
    if (
        seven_day_pct is not None
        or codex_reset_credits is not None
        or monthly_percent is not None
    ):
        seven_day = ProviderLimits(
            seven_day_percent=seven_day_pct,
            codex_reset_credits=codex_reset_credits,
            monthly_percent=monthly_percent,
        )

    return Usage(
        account_id=account_id,
        provider=provider,
        fetched_at=now_utc(),
        five_hour=five_hour,
        five_hour_resets_at=parse_iso(five_hour_resets) if five_hour_resets else None,
        seven_day=seven_day,
        seven_day_resets_at=parse_iso(seven_day_resets) if seven_day_resets else None,
        extra_credits_remaining=extra_credits,
        raw=body,
    )


# ---------------------------------------------------------------------- #
# HTTP error → AdapterError mapping
# ---------------------------------------------------------------------- #


def map_http_error(
    response: httpx.Response,
    *,
    default_message: str | None = None,
) -> AdapterError:
    """Translate an ``httpx.Response`` (non-2xx) into the right
    :class:`AdapterError` subclass.

    - 401/403 → ``AuthError``
    - 429    → ``RateLimitError`` with ``Retry-After`` parsed
    - 5xx    → ``ProviderUnavailable``
    - other  → ``AdapterError``

    Callers that already caught :class:`httpx.HTTPError` should use
    :func:`map_transport_error` instead.
    """
    status = response.status_code
    text = default_message or f"HTTP {status} from upstream"
    if status in (401, 403):
        return AuthError(f"{text} (auth rejected; re-paste credential)")
    if status == 429:
        retry_after = _parse_retry_after(response.headers.get("Retry-After"))
        return RateLimitError(
            f"{text} (rate limited; retry after {retry_after}s)",
            retry_after_seconds=retry_after,
        )
    if 500 <= status <= 599:
        return ProviderUnavailable(f"{text} (upstream {status})")
    return AdapterError(f"{text} (unexpected status {status})")


def map_transport_error(exc: httpx.HTTPError) -> ProviderUnavailable:
    """Translate a transport-layer ``httpx`` exception into
    :class:`ProviderUnavailable`.

    Use this when ``client.get(...)`` raises instead of returning a
    response. Always wrapping transport failures in the same error
    type keeps the poller's recovery logic uniform.
    """
    return ProviderUnavailable(f"transport error: {exc!s}")


def _parse_retry_after(value: str | None) -> float:
    """Parse a ``Retry-After`` header value into seconds.

    Supports both delta-seconds (``"120"``) and HTTP-date forms
    (``"Wed, 21 Oct 2026 07:28:00 GMT"``). Returns 60.0 if the value
    is missing or unparseable so callers always have a sensible
    default.
    """
    if not value:
        return 60.0
    value = value.strip()
    # delta-seconds form
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    # HTTP-date form
    try:
        from email.utils import parsedate_to_datetime
        from datetime import datetime, timezone

        target = parsedate_to_datetime(value)
        if target is None:
            return 60.0
        now = datetime.now(timezone.utc)
        if target.tzinfo is None:
            target = target.replace(tzinfo=timezone.utc)
        return max(0.0, (target - now).total_seconds())
    except (TypeError, ValueError):
        return 60.0


def to_percent(raw: Any) -> float | None:
    """Normalize a 0..1 fraction or 0..100 number to 0..100.

    Returns ``None`` if the upstream value is missing or out of range.
    Handles both ``0.42`` (fraction) and ``42`` (whole percent) — the
    upstream API format varies between providers and may change
    without notice, so be lenient.
    """
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if value < 0:
        return None
    if value <= 1.5:
        value *= 100.0
    if value > 100.0:
        value = 100.0
    return value