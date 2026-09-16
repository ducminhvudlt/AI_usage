"""Async polling loop — the ``custats`` "brain".

Drives every account's adapter once per ``refresh_interval_seconds``,
persists the result, computes a :class:`LiveStatus`, fires
notifications on threshold crossings, and broadcasts snapshots.

The HTTP client is created via an injectable ``client_factory`` so
tests can wire in ``httpx.MockTransport`` without monkey-patching.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Callable

import httpx

from .core.config import AppConfig
from .core.models import Account, Provider, UsageStatus
from .core.time_utils import now_utc
from .notifier import Notifier
from .providers import ProviderAdapter, get_adapter
from .providers.base import (
    AdapterError,
    AuthError,
    ProviderUnavailable,
    RateLimitError,
)
from .state import LiveStatus, compute_status
from .storage.db import Database

# Extra buffer added to a provider-supplied retry-after so we don't
# immediately re-trip a 429.
RETRY_AFTER_BUFFER = 5.0

ClientFactory = Callable[[], httpx.AsyncClient]


@dataclass
class PollTarget:
    """One account + its decrypted credentials, ready to poll."""
    account: Account
    credentials: dict[str, Any]


def _default_client_factory() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=10.0)


class Poller:
    """Drives the async poll loop and fans out status updates."""

    def __init__(
        self,
        targets: list[PollTarget],
        *,
        config: AppConfig,
        db: Database,
        notifier: Notifier,
        adapter_for: Callable[[Provider], ProviderAdapter] = get_adapter,
        clock: Callable[[], Any] = now_utc,
        client_factory: ClientFactory | None = None,
    ) -> None:
        if not targets:
            raise ValueError("Poller requires at least one target")
        self._targets = list(targets)
        self._config = config
        self._db = db
        self._notifier = notifier
        self._adapter_for = adapter_for
        self._clock = clock
        self._client_factory = client_factory or _default_client_factory
        self._statuses: dict[str, LiveStatus] = {}
        self._previous: dict[str, UsageStatus] = {}
        self._wait_until: dict[str, float] = {}
        self._was_below_threshold: dict[str, bool] = {}
        self._callbacks: list[Callable[[dict[str, LiveStatus]], None]] = []
        self._task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()
        # Captured by :meth:`_run` once the asyncio loop is up. Used by
        # :meth:`submit_from_any_thread` so callers from other threads (e.g.
        # GTK menu callbacks) can schedule coroutines on the poller's loop
        # without ``asyncio.create_task`` (which would fail with
        # ``RuntimeError: no running event loop`` on Python ≥ 3.10).
        self._loop: asyncio.AbstractEventLoop | None = None

    async def start(self) -> None:
        """Begin polling in the background. Returns immediately."""
        if self._task is not None:
            return
        self._stopping.clear()
        self._task = asyncio.create_task(self._run(), name="custats-poller")

    async def stop(self) -> None:
        """Cancel the background task and wait for it to finish."""
        if self._task is None:
            return
        self._stopping.set()
        self._task.cancel()
        try:
            await self._task
        except Exception:
            pass
        finally:
            self._task = None

    @property
    def statuses(self) -> dict[str, LiveStatus]:
        """Read-only snapshot of the latest status per account id."""
        return dict(self._statuses)

    def subscribe(
        self,
        callback: Callable[[dict[str, LiveStatus]], None],
    ) -> Callable[[], None]:
        """Register a callback fired after every poll. Returns unsubscribe."""
        self._callbacks.append(callback)

        def _unsubscribe() -> None:
            try:
                self._callbacks.remove(callback)
            except ValueError:
                pass

        return _unsubscribe

    def submit_from_any_thread(self, coro):
        """Schedule ``coro`` on the poller's loop from any thread.

        Returns a :class:`concurrent.futures.Future` so callers (e.g. GTK
        menu callbacks) can wait on the result without holding a reference
        to the poller's loop. Raises ``RuntimeError`` if the poller hasn't
        started or has already stopped.
        """
        loop = self._loop
        if loop is None or loop.is_closed():
            raise RuntimeError("poller is not running")
        return asyncio.run_coroutine_threadsafe(coro, loop)

    async def poll_once(self) -> None:
        """Run a single poll cycle across every target."""
        for target in self._targets:
            try:
                status = await self._poll_target(target)
            except Exception as exc:  # never crash the loop
                status = self._error_status(target, str(exc))
            previous = self._previous.get(target.account.id)
            self._statuses[target.account.id] = status
            if status.error is None:
                self._maybe_notify(target.account, status, previous)
                # Latch update: only seen when the latest poll was below
                # the threshold. Using ``.get(..., True)`` in
                # ``_maybe_notify`` means the very first over-threshold
                # poll still fires for accounts we've never seen.
                pct = status.five_hour_percent
                if pct is None or pct < self._config.notify_threshold_percent:
                    self._was_below_threshold[target.account.id] = True
                else:
                    self._was_below_threshold[target.account.id] = False
            self._previous[target.account.id] = status.five_hour_status
        self._broadcast()

    async def _run(self) -> None:
        # Capture the loop so callers from other threads can target it via
        # :meth:`submit_from_any_thread`.
        self._loop = asyncio.get_running_loop()
        while not self._stopping.is_set():
            try:
                await self.poll_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                pass
            try:
                await asyncio.wait_for(
                    self._stopping.wait(),
                    timeout=float(self._config.refresh_interval_seconds),
                )
            except asyncio.TimeoutError:
                pass

    async def _poll_target(self, target: PollTarget) -> LiveStatus:
        # Respect active rate-limit cooldown before hitting the upstream.
        if self._wait_remaining(target.account.id) > 0:
            cached = self._statuses.get(target.account.id)
            if cached is not None:
                return cached
            return self._error_status(target, "rate limited (awaiting retry)")
        client = self._client_factory()
        try:
            try:
                usage = await self._adapter_for(target.account.provider).fetch(
                    target.credentials, client=client
                )
            except AuthError as exc:
                return self._error_status(target, str(exc))
            except RateLimitError as exc:
                wait = float(exc.retry_after_seconds) + RETRY_AFTER_BUFFER
                self._wait_until[target.account.id] = asyncio.get_event_loop().time() + wait
                return self._error_status(target, f"rate limited (retry in {wait:.0f}s)")
            except (ProviderUnavailable, AdapterError) as exc:
                return self._error_status(target, str(exc))
            try:
                self._db.record_usage(target.account.id, usage)
            except Exception as exc:
                return compute_status(
                    target.account, usage,
                    pace_enabled=self._config.pace_enabled,
                    error=f"db write failed: {exc}",
                )
            self._wait_until.pop(target.account.id, None)
            return compute_status(
                target.account, usage, pace_enabled=self._config.pace_enabled
            )
        finally:
            await client.aclose()

    def _error_status(self, target: PollTarget, error: str) -> LiveStatus:
        return compute_status(
            target.account, None,
            pace_enabled=self._config.pace_enabled, error=error,
        )

    def _maybe_notify(
        self,
        account: Account,
        status: LiveStatus,
        previous: UsageStatus | None,
    ) -> None:
        if not self._notifier.available():
            return
        current, percent = status.five_hour_status, status.five_hour_percent
        # AT_LIMIT first — most urgent.
        if current is UsageStatus.AT_LIMIT and previous is not UsageStatus.AT_LIMIT:
            if percent is not None:
                self._notifier.notify_at_limit(account, percent)
            return
        # Threshold crossing.
        if (
            self._config.notify_threshold_percent > 0
            and percent is not None
            and percent >= self._config.notify_threshold_percent
            and self._was_below_threshold.get(account.id, True)
        ):
            self._notifier.notify_threshold(account, percent)
            return
        # Recovery: was CRITICAL/AT_LIMIT, now GOOD/CAUTION.
        if (
            self._config.notify_on_recovery
            and previous in (UsageStatus.CRITICAL, UsageStatus.AT_LIMIT)
            and current in (UsageStatus.GOOD, UsageStatus.CAUTION)
        ):
            self._notifier.notify_recovery(account)

    def _broadcast(self) -> None:
        snapshot = self.statuses
        for cb in list(self._callbacks):
            try:
                cb(snapshot)
            except Exception:
                pass

    def _wait_remaining(self, account_id: str) -> float:
        target = self._wait_until.get(account_id)
        if target is None:
            return 0.0
        return max(0.0, target - asyncio.get_event_loop().time())


__all__ = ["PollTarget", "Poller", "RETRY_AFTER_BUFFER"]
