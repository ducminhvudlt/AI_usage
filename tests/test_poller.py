"""Tests for custats.poller — the async polling loop.

Every test injects an ``httpx.MockTransport`` via the
``client_factory`` seam. This is the *only* seam the poller exposes
for HTTP — we never monkey-patch ``httpx`` globally.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from unittest.mock import MagicMock

import httpx
import pytest

from custats.core.config import AppConfig
from custats.core.models import (
    Account,
    Provider,
    ProviderLimits,
    Usage,
    UsageStatus,
)
from custats.notifier import Notifier
from custats.poller import PollTarget, Poller
from custats.providers.base import (
    AdapterError,
    AuthError,
    ProviderUnavailable,
    RateLimitError,
)
from custats.storage.db import Database
from custats.storage.encrypted import load_or_create_key


# ---------------------------------------------------------------------- #
# fixtures
# ---------------------------------------------------------------------- #


@pytest.fixture
def key(tmp_path: Path) -> bytes:
    return load_or_create_key(tmp_path / "secret.key")


@pytest.fixture
def db(tmp_path: Path, key: bytes) -> Database:
    return Database(tmp_path / "state.db", key)


@pytest.fixture
def config() -> AppConfig:
    return AppConfig(refresh_interval_seconds=1, notify_threshold_percent=80)


@pytest.fixture
def notifier() -> Notifier:
    return Notifier(enabled=False)


@pytest.fixture
def account(db: Database) -> Account:
    acct = Account(
        id="acc-1",
        alias="work",
        provider=Provider.CLAUDE,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    db.add_account(acct, {"session_key": "x"})
    return acct


@pytest.fixture
def second_account(db: Database) -> Account:
    acct = Account(
        id="acc-2",
        alias="personal",
        provider=Provider.CODEX,
        created_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )
    db.add_account(acct, {"session_key": "y"})
    return acct


def _make_usage(
    account_id: str,
    provider: Provider,
    *,
    five: float = 10.0,
    seven: float = 5.0,
) -> Usage:
    now = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    return Usage(
        account_id=account_id,
        provider=provider,
        fetched_at=now,
        five_hour=ProviderLimits(five_hour_percent=five),
        five_hour_resets_at=now + timedelta(hours=4),
        seven_day=ProviderLimits(seven_day_percent=seven),
        seven_day_resets_at=now + timedelta(hours=120),
    )


# ---------------------------------------------------------------------- #
# Fake adapter helpers
# ---------------------------------------------------------------------- #


class _FakeAdapter:
    """Adapter whose fetch returns a sequence of pre-canned ``Usage`` objects
    (or raises a sequence of exceptions). Tracks call count."""

    provider = Provider.CLAUDE

    def __init__(self, results: list[Any]) -> None:
        self._results = list(results)
        self.calls = 0

    def describe_credential(self) -> str:
        return "fake"

    async def fetch(
        self, credentials: dict[str, Any], *, client: httpx.AsyncClient
    ) -> Usage:
        self.calls += 1
        if not self._results:
            raise AssertionError("FakeAdapter ran out of results")
        result = self._results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class _AdapterRegistry:
    """Map ``Provider -> FakeAdapter`` so the poller can route correctly."""

    def __init__(self) -> None:
        self.adapters: dict[Provider, _FakeAdapter] = {}

    def register(self, provider: Provider, adapter: _FakeAdapter) -> None:
        adapter.provider = provider
        self.adapters[provider] = adapter

    def __call__(self, provider: Provider) -> _FakeAdapter:
        return self.adapters[provider]


# ---------------------------------------------------------------------- #
# Poller factory
# ---------------------------------------------------------------------- #


def _make_poller(
    *,
    account: Account,
    db: Database,
    config: AppConfig,
    notifier: Notifier,
    adapter: _FakeAdapter,
    transport: httpx.MockTransport | None = None,
    clock: Callable[[], datetime] | None = None,
) -> Poller:
    """Build a single-target poller with an httpx MockTransport."""
    if transport is None:

        def _default_handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={})

        transport = httpx.MockTransport(_default_handler)
    registry = _AdapterRegistry()
    registry.register(account.provider, adapter)
    target = PollTarget(account=account, credentials={"session_key": "x"})
    return Poller(
        targets=[target],
        config=config,
        db=db,
        notifier=notifier,
        adapter_for=registry,
        clock=clock or (lambda: datetime(2026, 1, 1, tzinfo=timezone.utc)),
        client_factory=lambda: httpx.AsyncClient(transport=transport),
    )


# ---------------------------------------------------------------------- #
# Tests
# ---------------------------------------------------------------------- #


class TestPollOnce:
    async def test_polls_each_target_and_persists(
        self,
        account: Account,
        db: Database,
        config: AppConfig,
        notifier: Notifier,
    ) -> None:
        usage = _make_usage(account.id, account.provider)
        adapter = _FakeAdapter([usage])
        poller = _make_poller(
            account=account,
            db=db,
            config=config,
            notifier=notifier,
            adapter=adapter,
        )
        await poller.poll_once()
        assert adapter.calls == 1
        history = db.usage_history(account.id)
        assert len(history) == 1
        assert history[0].account_id == account.id
        assert account.id in poller.statuses
        assert poller.statuses[account.id].five_hour_percent == 10.0

    async def test_polls_multiple_targets(
        self,
        account: Account,
        second_account: Account,
        db: Database,
        config: AppConfig,
        notifier: Notifier,
    ) -> None:
        # Both accounts are already inserted by their fixtures; the
        # foreign-key constraint on usage_history is satisfied.
        registry = _AdapterRegistry()
        claude_adapter = _FakeAdapter([_make_usage(account.id, Provider.CLAUDE)])
        codex_adapter = _FakeAdapter([_make_usage(second_account.id, Provider.CODEX)])
        registry.register(Provider.CLAUDE, claude_adapter)
        registry.register(Provider.CODEX, codex_adapter)
        poller = Poller(
            targets=[
                PollTarget(account=account, credentials={"session_key": "x"}),
                PollTarget(account=second_account, credentials={"session_key": "y"}),
            ],
            config=config,
            db=db,
            notifier=notifier,
            adapter_for=registry,
            client_factory=lambda: httpx.AsyncClient(
                transport=httpx.MockTransport(lambda r: httpx.Response(200))
            ),
        )
        await poller.poll_once()
        assert claude_adapter.calls == 1
        assert codex_adapter.calls == 1
        assert set(poller.statuses) == {account.id, second_account.id}


class TestStartStop:
    async def test_start_then_stop_runs_at_least_one_poll(
        self,
        account: Account,
        db: Database,
        notifier: Notifier,
    ) -> None:
        # refresh_interval_seconds=1 is fine; we just need at least one
        # poll before stop() takes effect.
        config = AppConfig(refresh_interval_seconds=1)
        usage = _make_usage(account.id, account.provider)
        adapter = _FakeAdapter([usage] * 10)
        poller = _make_poller(
            account=account, db=db, config=config, notifier=notifier, adapter=adapter
        )
        await poller.start()
        # Give the loop a beat to actually run poll_once.
        await asyncio.sleep(0.05)
        await poller.stop()
        assert adapter.calls >= 1


class TestSubscribe:
    async def test_callback_fires_after_poll(
        self,
        account: Account,
        db: Database,
        config: AppConfig,
        notifier: Notifier,
    ) -> None:
        usage = _make_usage(account.id, account.provider)
        adapter = _FakeAdapter([usage])
        poller = _make_poller(
            account=account,
            db=db,
            config=config,
            notifier=notifier,
            adapter=adapter,
        )
        received: list[dict[str, Any]] = []
        unsubscribe = poller.subscribe(lambda snap: received.append(dict(snap)))
        await poller.poll_once()
        assert len(received) == 1
        assert account.id in received[0]
        unsubscribe()
        await poller.poll_once()
        # No new callback after unsubscribe.
        assert len(received) == 1


class TestAuthError:
    async def test_auth_error_sets_status_no_notify(
        self,
        account: Account,
        db: Database,
        config: AppConfig,
    ) -> None:
        # Build a notifier we can spy on; it must report available() == True
        # for ``_maybe_notify`` to run, so swap in a recording fake.
        spy = MagicMock(spec=Notifier)
        spy.available.return_value = True

        adapter = _FakeAdapter([AuthError("bad creds")])
        poller = _make_poller(
            account=account,
            db=db,
            config=config,
            notifier=spy,
            adapter=adapter,
        )
        await poller.poll_once()
        status = poller.statuses[account.id]
        assert status.error is not None
        assert "bad creds" in status.error
        assert status.five_hour_status is UsageStatus.UNKNOWN
        # No notifications for AuthError.
        spy.notify_at_limit.assert_not_called()
        spy.notify_threshold.assert_not_called()
        spy.notify_recovery.assert_not_called()


class TestThresholdNotification:
    async def test_threshold_crossing_fires(
        self,
        account: Account,
        db: Database,
        config: AppConfig,
    ) -> None:
        spy = MagicMock(spec=Notifier)
        spy.available.return_value = True

        # First poll: 79% (below threshold). Second poll: 81% (above).
        first = _make_usage(account.id, account.provider, five=79.0)
        second = _make_usage(account.id, account.provider, five=81.0)
        adapter = _FakeAdapter([first, second])
        poller = _make_poller(
            account=account,
            db=db,
            config=config,
            notifier=spy,
            adapter=adapter,
        )
        await poller.poll_once()
        spy.notify_threshold.assert_not_called()
        await poller.poll_once()
        spy.notify_threshold.assert_called_once()
        args, _ = spy.notify_threshold.call_args
        assert args[0].id == account.id
        assert args[1] == pytest.approx(81.0)

    async def test_threshold_latch_prevents_repeat_firing(
        self,
        account: Account,
        db: Database,
        config: AppConfig,
    ) -> None:
        """Regression for R2: holding at 82% (above the 80% threshold) must
        only fire ``notify_threshold`` once. The previous predicate was
        status-enum based and re-fired on every ``CAUTION -> CAUTION``
        poll once the percent stabilised >= threshold."""
        spy = MagicMock(spec=Notifier)
        spy.available.return_value = True

        first = _make_usage(account.id, account.provider, five=82.0)
        second = _make_usage(account.id, account.provider, five=82.0)
        third = _make_usage(account.id, account.provider, five=82.0)
        adapter = _FakeAdapter([first, second, third])
        poller = _make_poller(
            account=account,
            db=db,
            config=config,
            notifier=spy,
            adapter=adapter,
        )
        await poller.poll_once()
        await poller.poll_once()
        await poller.poll_once()
        spy.notify_threshold.assert_called_once()
        assert spy.notify_threshold.call_count == 1

    async def test_threshold_latch_refires_after_drop_below(
        self,
        account: Account,
        db: Database,
        config: AppConfig,
    ) -> None:
        """After the latch flips back to ``True`` (poll below threshold),
        the next crossing must fire again."""
        spy = MagicMock(spec=Notifier)
        spy.available.return_value = True

        u_82 = _make_usage(account.id, account.provider, five=82.0)
        u_75 = _make_usage(account.id, account.provider, five=75.0)
        u_82b = _make_usage(account.id, account.provider, five=82.0)
        adapter = _FakeAdapter([u_82, u_75, u_82b])
        poller = _make_poller(
            account=account,
            db=db,
            config=config,
            notifier=spy,
            adapter=adapter,
        )
        await poller.poll_once()
        await poller.poll_once()
        await poller.poll_once()
        assert spy.notify_threshold.call_count == 2


class TestAtLimitNotification:
    async def test_at_limit_fires(
        self,
        account: Account,
        db: Database,
        config: AppConfig,
    ) -> None:
        spy = MagicMock(spec=Notifier)
        spy.available.return_value = True

        first = _make_usage(account.id, account.provider, five=99.0)
        second = _make_usage(account.id, account.provider, five=100.0)
        adapter = _FakeAdapter([first, second])
        poller = _make_poller(
            account=account,
            db=db,
            config=config,
            notifier=spy,
            adapter=adapter,
        )
        await poller.poll_once()
        await poller.poll_once()
        spy.notify_at_limit.assert_called_once()


class TestRecoveryNotification:
    async def test_recovery_fires_when_dropping_back(
        self,
        account: Account,
        db: Database,
        config: AppConfig,
    ) -> None:
        spy = MagicMock(spec=Notifier)
        spy.available.return_value = True

        first = _make_usage(account.id, account.provider, five=95.0)
        second = _make_usage(account.id, account.provider, five=50.0)
        adapter = _FakeAdapter([first, second])
        poller = _make_poller(
            account=account,
            db=db,
            config=config,
            notifier=spy,
            adapter=adapter,
        )
        await poller.poll_once()
        await poller.poll_once()
        spy.notify_recovery.assert_called_once()

    async def test_recovery_disabled_by_config(
        self,
        account: Account,
        db: Database,
    ) -> None:
        cfg = AppConfig(refresh_interval_seconds=1, notify_on_recovery=False)
        spy = MagicMock(spec=Notifier)
        spy.available.return_value = True

        first = _make_usage(account.id, account.provider, five=95.0)
        second = _make_usage(account.id, account.provider, five=50.0)
        adapter = _FakeAdapter([first, second])
        poller = _make_poller(
            account=account, db=db, config=cfg, notifier=spy, adapter=adapter
        )
        await poller.poll_once()
        await poller.poll_once()
        spy.notify_recovery.assert_not_called()


class TestRateLimitBackoff:
    async def test_rate_limit_skips_subsequent_calls(
        self,
        account: Account,
        db: Database,
        config: AppConfig,
        notifier: Notifier,
    ) -> None:
        # First call: 429 with retry_after_seconds=10 → triggers cooldown.
        # The poller must NOT call the adapter again for ~10 seconds.
        adapter = _FakeAdapter([RateLimitError("429", retry_after_seconds=10.0)])
        poller = _make_poller(
            account=account,
            db=db,
            config=config,
            notifier=notifier,
            adapter=adapter,
        )
        await poller.poll_once()
        assert adapter.calls == 1
        # Subsequent polls within the cooldown must reuse the cached status.
        await poller.poll_once()
        await poller.poll_once()
        assert adapter.calls == 1
        # Status carries the rate-limit hint.
        status = poller.statuses[account.id]
        assert status.error is not None
        assert "rate limited" in status.error.lower()

    async def test_rate_limit_other_accounts_still_polled(
        self,
        account: Account,
        second_account: Account,
        db: Database,
        config: AppConfig,
        notifier: Notifier,
    ) -> None:
        # Both accounts are inserted by their fixtures.
        registry = _AdapterRegistry()
        claude_adapter = _FakeAdapter(
            [RateLimitError("429", retry_after_seconds=60.0)]
        )
        codex_adapter = _FakeAdapter(
            [_make_usage(second_account.id, Provider.CODEX)]
        )
        registry.register(Provider.CLAUDE, claude_adapter)
        registry.register(Provider.CODEX, codex_adapter)
        poller = Poller(
            targets=[
                PollTarget(account=account, credentials={"session_key": "x"}),
                PollTarget(
                    account=second_account, credentials={"session_key": "y"}
                ),
            ],
            config=config,
            db=db,
            notifier=notifier,
            adapter_for=registry,
            client_factory=lambda: httpx.AsyncClient(
                transport=httpx.MockTransport(lambda r: httpx.Response(200))
            ),
        )
        await poller.poll_once()
        assert claude_adapter.calls == 1
        assert codex_adapter.calls == 1
        # Second cycle: Claude is rate-limited → still skipped, Codex polled.
        await poller.poll_once()
        assert claude_adapter.calls == 1
        assert codex_adapter.calls == 2


class TestTransientErrors:
    async def test_provider_unavailable_does_not_notify(
        self,
        account: Account,
        db: Database,
        config: AppConfig,
    ) -> None:
        spy = MagicMock(spec=Notifier)
        spy.available.return_value = True
        adapter = _FakeAdapter([ProviderUnavailable("upstream 503")])
        poller = _make_poller(
            account=account,
            db=db,
            config=config,
            notifier=spy,
            adapter=adapter,
        )
        await poller.poll_once()
        status = poller.statuses[account.id]
        assert status.error is not None
        assert "503" in status.error
        spy.notify_threshold.assert_not_called()
        spy.notify_at_limit.assert_not_called()

    async def test_generic_adapter_error_does_not_notify(
        self,
        account: Account,
        db: Database,
        config: AppConfig,
    ) -> None:
        spy = MagicMock(spec=Notifier)
        spy.available.return_value = True
        adapter = _FakeAdapter([AdapterError("parse fail")])
        poller = _make_poller(
            account=account,
            db=db,
            config=config,
            notifier=spy,
            adapter=adapter,
        )
        await poller.poll_once()
        spy.notify_threshold.assert_not_called()
        spy.notify_at_limit.assert_not_called()


class TestDbWriteFailure:
    async def test_db_failure_does_not_kill_loop(
        self,
        account: Account,
        config: AppConfig,
        notifier: Notifier,
    ) -> None:
        # Use a mock DB that raises on record_usage.
        bad_db = MagicMock(spec=Database)
        bad_db.record_usage.side_effect = RuntimeError("disk full")
        usage = _make_usage(account.id, account.provider)
        adapter = _FakeAdapter([usage])
        poller = _make_poller(
            account=account,
            db=bad_db,  # type: ignore[arg-type]
            config=config,
            notifier=notifier,
            adapter=adapter,
        )
        await poller.poll_once()
        status = poller.statuses[account.id]
        # Still populated, but the error field is set.
        assert status.five_hour_percent == 10.0
        assert status.error is not None
        assert "disk full" in status.error


class TestSubmitFromAnyThread:
    """R2: ``Poller.submit_from_any_thread`` lets GTK menu callbacks (which
    run on the GTK main thread, not the poller's asyncio loop) schedule
    coroutines on the poller's loop without ``asyncio.create_task`` raising
    ``RuntimeError: no running event loop``."""

    def _start_poller_on_bg_thread(self, poller: Poller) -> tuple[asyncio.AbstractEventLoop, threading.Thread]:
        """Run ``poller.start()`` on a background thread that owns its own
        event loop. Returns ``(loop, thread)`` so the caller can shut them
        down at the end of the test."""
        loop = asyncio.new_event_loop()

        def _bg() -> None:
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(poller.start())
                loop.run_forever()
            finally:
                loop.close()

        thread = threading.Thread(target=_bg, name="custats-test-bg", daemon=True)
        thread.start()
        return loop, thread

    @staticmethod
    def _wait_for(predicate: Callable[[], bool], *, timeout: float = 2.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.01)
        raise AssertionError("predicate did not become true within timeout")

    def test_submit_succeeds_from_main_thread(
        self,
        account: Account,
        db: Database,
        config: AppConfig,
        notifier: Notifier,
    ) -> None:
        adapter = _FakeAdapter([_make_usage(account.id, account.provider)] * 5)
        poller = _make_poller(
            account=account, db=db, config=config, notifier=notifier, adapter=adapter
        )
        loop, thread = self._start_poller_on_bg_thread(poller)
        try:
            # The poller's _loop is captured inside _run().
            self._wait_for(lambda: poller._loop is not None and not poller._loop.is_closed())

            # Submit a coroutine from the test (main) thread — this is the
            # code path the GTK-thread refresh callback now uses.
            fut = poller.submit_from_any_thread(asyncio.sleep(0))
            assert isinstance(fut, concurrent.futures.Future)
            fut.result(timeout=2)
        finally:
            stop_fut = asyncio.run_coroutine_threadsafe(poller.stop(), loop)
            stop_fut.result(timeout=2)
            loop.call_soon_threadsafe(loop.stop)
            thread.join(timeout=2)

    def test_submit_raises_when_poller_not_running(
        self,
        account: Account,
        db: Database,
        config: AppConfig,
        notifier: Notifier,
    ) -> None:
        """A freshly-constructed poller has no loop and must refuse submits."""
        adapter = _FakeAdapter([_make_usage(account.id, account.provider)])
        poller = _make_poller(
            account=account, db=db, config=config, notifier=notifier, adapter=adapter
        )
        coro = asyncio.sleep(0)
        try:
            with pytest.raises(RuntimeError):
                poller.submit_from_any_thread(coro)
        finally:
            coro.close()

    def test_submit_raises_after_loop_closed(
        self,
        account: Account,
        db: Database,
        config: AppConfig,
        notifier: Notifier,
    ) -> None:
        """After the poller's loop is stopped, submits must raise."""
        adapter = _FakeAdapter([_make_usage(account.id, account.provider)])
        poller = _make_poller(
            account=account, db=db, config=config, notifier=notifier, adapter=adapter
        )
        loop, thread = self._start_poller_on_bg_thread(poller)
        try:
            self._wait_for(lambda: poller._loop is not None and not poller._loop.is_closed())
            stop_fut = asyncio.run_coroutine_threadsafe(poller.stop(), loop)
            stop_fut.result(timeout=2)
        finally:
            loop.call_soon_threadsafe(loop.stop)
            thread.join(timeout=2)

        # Poller's loop is closed — submit must now raise.
        coro = asyncio.sleep(0)
        try:
            with pytest.raises(RuntimeError):
                poller.submit_from_any_thread(coro)
        finally:
            coro.close()
