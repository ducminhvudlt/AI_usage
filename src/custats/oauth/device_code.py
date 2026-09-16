"""Generic OAuth 2.0 device-code sign-in flow (RFC 8628).

Used by Codex today. Generic enough to wrap any provider that exposes the
two-endpoint pattern: request-code, then poll-until-approved.

The implementation:

* Treats the request-code and poll endpoints as plain ``httpx`` calls —
  callers pass them in, so this module knows nothing about a specific
  provider's URL or payload shape.
* Polls at the ``interval_seconds`` returned by the provider, and
  recognises RFC 8628's ``authorization_pending`` (keep going) and
  ``slow_down`` (increase interval by 5 s) statuses alongside a small
  set of provider-specific aliases.
* Retries transient network errors up to three times before raising
  :class:`DeviceCodeError`.
* Allows the caller to inject a callback (``on_poll``) that fires after
  each poll attempt — the CLI uses this to print the "still waiting…"
  line. The callback may raise :class:`DeviceCodeCancelled` to abort
  polling cleanly without surfacing as an error.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Callable

import httpx


@dataclass(frozen=True)
class DeviceCodeRequest:
    """Response from a provider's "request device code" endpoint."""

    device_id: str
    user_code: str  # short code shown to the user (e.g. "ABCD-EFGH")
    verification_url: str  # URL the user opens in a browser
    interval_seconds: int = 5
    expires_in_seconds: int = 600


@dataclass(frozen=True)
class DeviceCodeApproved:
    """Final response when the user has approved the device."""

    raw: dict[str, Any]  # the full JSON the provider returned


class DeviceCodeError(RuntimeError):
    """Raised on non-recoverable device-code errors (network, expired, etc)."""


class DeviceCodeCancelled(Exception):
    """Raised by an injected ``on_poll`` callback to abort polling cleanly."""


# Recognised terminal success statuses.
_APPROVED_STATUSES = frozenset({"ok", "approved", "success"})

# Statuses that mean "keep polling, but slower / same rate".
_PENDING_STATUSES = frozenset({"authorization_pending", "pending"})
_SLOW_DOWN_STATUSES = frozenset({"slow_down"})

# Terminal failure statuses — these always raise ``DeviceCodeError``.
_FAILURE_STATUSES = frozenset(
    {"access_denied", "expired_token", "expired", "denied"}
)

# How many times to retry a transient transport error before raising.
_MAX_NETWORK_RETRIES = 3

# Extra seconds to add to the poll interval when the provider returns
# ``slow_down`` (RFC 8628 §3.5).
_SLOW_DOWN_EXTRA_SECONDS = 5

# Canonical Cloudflare JS-challenge title. Used to detect Cloudflare's
# bot-management interstitial so the CLI can surface a tailored
# cookie-paste workaround (the device-code endpoint is Cloudflare-gated
# but the cookie-auth path on chatgpt.com is not).
_CLOUDFLARE_TITLE = "<title>Just a moment\u2026</title>"


async def request_device_code(
    *,
    client: httpx.AsyncClient,
    request_url: str,
    payload: dict[str, Any],
    headers: dict[str, str] | None = None,
) -> DeviceCodeRequest:
    """POST to the provider's request-code endpoint, return a DeviceCodeRequest.

    Expected response shape (provider-specific; we map loosely):

        {"device_id": "...", "user_code": "...", "verification_url": "...",
         "interval": 5, "expires_in": 600}

    Some providers wrap the response in ``{"data": ...}`` — we accept
    either a top-level dict or a single-key wrapper so callers don't
    have to unwrap it themselves.
    """
    request_headers = dict(headers or {})
    request_headers.setdefault("Content-Type", "application/json")
    try:
        response = await client.post(
            request_url, json=payload, headers=request_headers
        )
    except httpx.HTTPError as exc:
        raise DeviceCodeError(
            f"device-code request transport error: {exc!s}"
        ) from exc

    if response.status_code != 200:
        if _looks_like_cloudflare_interstitial(response):
            raise DeviceCodeError(
                "device-code request blocked by Cloudflare bot-management "
                "(HTTP 403, <title>Just a moment\u2026</title>): "
                "auth.openai.com is rejecting the client fingerprint."
            )
        raise DeviceCodeError(
            f"device-code request failed: HTTP {response.status_code} "
            f"({response.text[:200]!r})"
        )

    body = _unwrap(response.json())
    if not isinstance(body, dict):
        raise DeviceCodeError(
            f"device-code request body is not a JSON object: {type(body).__name__}"
        )

    try:
        # Accept both RFC 8628's ``device_code`` field (what GitHub's
        # ``github.com/login/device/code`` endpoint returns) and the
        # ``device_id`` alias OpenAI's ``auth.openai.com`` endpoint
        # uses. The provider-specific names are kept as an internal
        # device-id identifier — the field name is a wire detail.
        device_id = str(
            body.get("device_code") or body.get("device_id") or ""
        )
        if not device_id:
            raise KeyError("device_code")
        user_code = str(body["user_code"])
        # Same story: RFC 8628 calls it ``verification_uri``; OpenAI
        # shipped ``verification_url``. Accept either.
        verification_url = str(
            body.get("verification_uri") or body.get("verification_url") or ""
        )
        if not verification_url:
            raise KeyError("verification_uri")
    except KeyError as exc:
        raise DeviceCodeError(
            f"device-code request missing required field {exc.args[0]!r}"
        ) from exc

    interval = _coerce_int(body.get("interval"), default=5)
    expires = _coerce_int(body.get("expires_in"), default=600)

    return DeviceCodeRequest(
        device_id=device_id,
        user_code=user_code,
        verification_url=verification_url,
        interval_seconds=max(1, interval),
        expires_in_seconds=max(1, expires),
    )


async def poll_device_code(
    *,
    client: httpx.AsyncClient,
    poll_url: str,
    payload: dict[str, Any],
    on_poll: Callable[[dict[str, Any]], None] | None = None,
    interval_seconds: int = 5,
    expires_in_seconds: int = 600,
    timeout_seconds: float | None = None,
    headers: dict[str, str] | None = None,
) -> DeviceCodeApproved:
    """Poll the provider until status == "ok" (or auth-style equivalent).

    ``on_poll`` fires after each poll attempt with the latest JSON
    body — useful for the CLI to print the user-visible "still
    waiting…" line. If the callback raises :class:`DeviceCodeCancelled`,
    polling aborts cleanly.

    ``headers`` is forwarded to every poll HTTP request — per-provider
    code passes through the same Cloudflare-friendly headers the
    initial request used, so the whole flow is recognised as one
    legitimate client (e.g. ``codex-cli/0.50.0`` + ``Client-Id``).

    Returns the final approval dict. Raises :class:`DeviceCodeError`
    on expiry, network failure after retries, or unexpected status.
    """
    if timeout_seconds is None:
        timeout_seconds = float(expires_in_seconds)
    if timeout_seconds < 0:
        raise DeviceCodeError("timeout_seconds must be non-negative")

    deadline = time.monotonic() + float(timeout_seconds)
    current_interval = max(0, int(interval_seconds))

    while True:
        if time.monotonic() >= deadline:
            raise DeviceCodeError("device code expired (timed out waiting)")

        body = await _poll_once(
            client=client,
            poll_url=poll_url,
            payload=payload,
            headers=headers,
        )

        if on_poll is not None:
            try:
                on_poll(body)
            except DeviceCodeCancelled:
                raise
            except Exception as exc:  # noqa: BLE001 — don't crash polling
                # Treat callback errors as benign — keep polling so a
                # buggy printer doesn't deny the user their sign-in.
                # We still surface the original exception if polling
                # itself fails later.
                _ = exc

        status = str(body.get("status", "")).lower()
        # RFC 8628 §3.5 + GitHub's implementation: the polling
        # endpoint may use either a ``status`` field (OpenAI's
        # convention) or an ``error`` field (RFC 8628's strict
        # convention). Treat them interchangeably so a single helper
        # works for both Codex and GitHub.
        if not status and "error" in body:
            status = str(body["error"]).lower()
        # GitHub's successful response is the bare token bundle
        # (``access_token``, ``refresh_token``, …) with no ``status``
        # field and no ``error`` field. Recognise that shape as an
        # implicit approval rather than "no signal" so we don't hang.
        elif not status and "access_token" in body:
            return DeviceCodeApproved(raw=body)
        if status in _APPROVED_STATUSES:
            return DeviceCodeApproved(raw=body)
        if status in _FAILURE_STATUSES:
            raise DeviceCodeError(
                f"device-code flow failed with status {status!r}: "
                f"{body!r}"
            )
        if status in _SLOW_DOWN_STATUSES:
            current_interval += _SLOW_DOWN_EXTRA_SECONDS
            await asyncio.sleep(max(0, current_interval))
            continue
        if status in _PENDING_STATUSES or not status:
            # No explicit status, or explicit "still pending" — keep polling.
            await asyncio.sleep(max(0, current_interval))
            continue

        # Anything we don't recognise is a contract change or a
        # provider-specific oddity — fail loudly rather than hang.
        raise DeviceCodeError(
            f"unrecognised device-code status {status!r}: {body!r}"
        )


async def _poll_once(
    *,
    client: httpx.AsyncClient,
    poll_url: str,
    payload: dict[str, Any],
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Make one poll request with bounded retries on transient errors.

    ``headers`` is forwarded verbatim from :func:`poll_device_code` so
    per-provider code can attach the same Cloudflare-friendly headers
    (e.g. ``User-Agent: codex-cli/0.50.0`` + ``Client-Id``) to every poll.
    A ``Content-Type: application/json`` default is applied if the
    caller didn't set one explicitly — mirrors :func:`request_device_code`.
    """
    request_headers = dict(headers or {})
    request_headers.setdefault("Content-Type", "application/json")
    last_error: httpx.HTTPError | None = None
    for attempt in range(1, _MAX_NETWORK_RETRIES + 1):
        try:
            response = await client.post(
                poll_url, json=payload, headers=request_headers
            )
        except httpx.HTTPError as exc:
            last_error = exc
            if attempt < _MAX_NETWORK_RETRIES:
                # Back off briefly before the next attempt — the
                # network blip is rarely resolved instantly.
                await asyncio.sleep(0.5 * attempt)
                continue
            raise DeviceCodeError(
                f"device-code poll transport error after "
                f"{_MAX_NETWORK_RETRIES} attempts: {exc!s}"
            ) from exc

        if response.status_code != 200:
            if _looks_like_cloudflare_interstitial(response):
                raise DeviceCodeError(
                    "device-code poll blocked by Cloudflare bot-management "
                    "(HTTP 403, <title>Just a moment\u2026</title>): "
                    "auth.openai.com is rejecting the client fingerprint."
                )
            raise DeviceCodeError(
                f"device-code poll HTTP {response.status_code}: "
                f"{response.text[:200]!r}"
            )

        body = _unwrap(response.json())
        if not isinstance(body, dict):
            raise DeviceCodeError(
                "device-code poll body is not a JSON object"
            )
        return body

    # Defensive — should be unreachable.
    raise DeviceCodeError(
        f"device-code poll failed: {last_error!s}"
    )


def _looks_like_cloudflare_interstitial(response: httpx.Response) -> bool:
    """Return True if the response is Cloudflare's JS-challenge interstitial.

    Cloudflare's bot-management blocks requests it doesn't recognise
    with a 403 + an HTML page that starts with ``<!DOCTYPE html`` and
    contains the canonical title ``<title>Just a moment\u2026</title>``.
    When the ``httpx`` POST impersonates a real Codex CLI we get past
    it; otherwise we surface the same interstitial so the CLI can
    point the user at the cookie-fallback path (the cookie-based
    auth on ``chatgpt.com`` is NOT behind Cloudflare).

    Detection requires ALL THREE conditions so a plain JSON 403 from a
    legitimate provider is never mistaken for a Cloudflare block —
    false positives would mislead users about the right fix:

    1. status code == 403
    2. body starts with ``<!DOCTYPE html`` (case-insensitive, leading
       whitespace stripped)
    3. body contains the canonical Cloudflare title
       (case-insensitive substring match)
    """
    if response.status_code != 403:
        return False
    body = response.text or ""
    if not body.lstrip().lower().startswith("<!doctype html"):
        return False
    return _CLOUDFLARE_TITLE.lower() in body.lower()


def _unwrap(parsed: Any) -> Any:
    """Unwrap a single-key ``{"data": ...}`` envelope, if present.

    If the parsed body is itself a dict but doesn't match the wrapper
    shape (e.g. it has more than one key, or its only key isn't
    ``data``), we return it unchanged so we don't hide real responses.
    """
    if isinstance(parsed, dict) and len(parsed) == 1 and "data" in parsed:
        return parsed["data"]
    return parsed


def _coerce_int(value: Any, *, default: int) -> int:
    """Return ``value`` as ``int``, falling back to ``default``."""
    if isinstance(value, bool):
        # ``bool`` is a subclass of ``int`` — exclude it so ``True``
        # doesn't silently turn into ``1``.
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return default
    return default


__all__ = [
    "DeviceCodeApproved",
    "DeviceCodeCancelled",
    "DeviceCodeError",
    "DeviceCodeRequest",
    "poll_device_code",
    "request_device_code",
]
