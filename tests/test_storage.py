"""Tests for custats.storage.db."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from custats.core.models import Account, Provider, ProviderLimits, Usage
from custats.storage.db import Database
from custats.storage.encrypted import load_or_create_key


@pytest.fixture
def key(tmp_path: Path) -> bytes:
    return load_or_create_key(tmp_path / "secret.key")


@pytest.fixture
def db(tmp_path: Path, key: bytes) -> Database:
    return Database(tmp_path / "state.db", key)


@pytest.fixture
def sample_account() -> Account:
    return Account(
        id="acc-1",
        alias="work",
        provider=Provider.CLAUDE,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


@pytest.fixture
def second_account() -> Account:
    return Account(
        id="acc-2",
        alias="personal",
        provider=Provider.GROK,
        created_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )


class TestAccounts:
    def test_add_and_get(self, db: Database, sample_account: Account) -> None:
        creds = {"session_key": "sk-abc123", "team": "blue"}
        db.add_account(sample_account, creds)
        result = db.get_account(sample_account.id)
        assert result is not None
        loaded, loaded_creds = result
        assert loaded == sample_account
        assert loaded_creds == creds

    def test_get_unknown_returns_none(self, db: Database) -> None:
        assert db.get_account("nope") is None

    def test_credentials_encrypted_at_rest(
        self, db: Database, sample_account: Account, key: bytes
    ) -> None:
        secret = "sk-totally-secret-value"
        db.add_account(sample_account, {"session_key": secret})
        # Open a fresh connection so we hit the on-disk blob directly.
        raw = db._conn.execute(  # noqa: SLF001 — intentional inspection
            "SELECT credential_blob FROM accounts WHERE id = ?",
            (sample_account.id,),
        ).fetchone()[0]
        # The encrypted blob must NOT contain the plaintext directly.
        assert secret.encode() not in raw

    def test_update_account(self, db: Database, sample_account: Account) -> None:
        db.add_account(sample_account, {"session_key": "x"})
        updated = Account(
            id=sample_account.id,
            alias="renamed",
            provider=Provider.CURSOR,
            created_at=sample_account.created_at,
            last_seen_at=datetime(2026, 2, 1, tzinfo=timezone.utc),
            is_active=False,
        )
        db.update_account(updated)
        result = db.get_account(sample_account.id)
        assert result is not None
        loaded, _ = result
        assert loaded.alias == "renamed"
        assert loaded.provider is Provider.CURSOR
        assert loaded.is_active is False
        assert loaded.last_seen_at == datetime(2026, 2, 1, tzinfo=timezone.utc)

    def test_delete_account_cascades(
        self, db: Database, sample_account: Account
    ) -> None:
        db.add_account(sample_account, {"session_key": "x"})
        usage = Usage(
            account_id=sample_account.id,
            provider=sample_account.provider,
            fetched_at=datetime(2026, 1, 3, tzinfo=timezone.utc),
            five_hour=ProviderLimits(five_hour_percent=10),
        )
        db.record_usage(sample_account.id, usage)
        db.delete_account(sample_account.id)
        assert db.get_account(sample_account.id) is None
        # Cascade should have removed usage_history rows too.
        assert db.usage_history(sample_account.id) == []

    def test_list_accounts_active_only(
        self,
        db: Database,
        sample_account: Account,
        second_account: Account,
    ) -> None:
        db.add_account(sample_account, {"session_key": "a"})
        db.add_account(second_account, {"session_key": "b"})
        inactive = Account(
            id="acc-3",
            alias="archived",
            provider=Provider.CODEX,
            created_at=datetime(2026, 3, 1, tzinfo=timezone.utc),
            is_active=False,
        )
        db.add_account(inactive, {"session_key": "c"})

        active = db.list_accounts()
        assert {a.id for a, _ in active} == {"acc-1", "acc-2"}

        everything = db.list_accounts(include_inactive=True)
        assert {a.id for a, _ in everything} == {"acc-1", "acc-2", "acc-3"}

    def test_add_account_requires_credentials(
        self, db: Database, sample_account: Account
    ) -> None:
        with pytest.raises(ValueError):
            db.add_account(sample_account, {})

    def test_close_is_idempotent(self, db: Database) -> None:
        db.close()
        db.close()  # must not raise


class TestUsageHistory:
    def test_record_and_latest(
        self, db: Database, sample_account: Account
    ) -> None:
        db.add_account(sample_account, {"session_key": "x"})
        now = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
        usage = Usage(
            account_id=sample_account.id,
            provider=sample_account.provider,
            fetched_at=now,
            five_hour=ProviderLimits(five_hour_percent=42.0),
            five_hour_resets_at=now + timedelta(hours=4),
            seven_day=ProviderLimits(seven_day_percent=12.0),
            seven_day_resets_at=now + timedelta(days=5),
            extra_credits_remaining=100.0,
            raw={"upstream": "value"},
        )
        db.record_usage(sample_account.id, usage)
        latest = db.latest_usage(sample_account.id)
        assert latest is not None
        assert latest.five_hour.five_hour_percent == 42.0
        assert latest.seven_day is not None
        assert latest.seven_day.seven_day_percent == 12.0
        assert latest.extra_credits_remaining == 100.0
        assert latest.raw == {"upstream": "value"}

        # record_usage also bumps last_seen_at.
        result = db.get_account(sample_account.id)
        assert result is not None
        acct, _ = result
        assert acct.last_seen_at == now

    def test_latest_usage_empty(self, db: Database, sample_account: Account) -> None:
        db.add_account(sample_account, {"session_key": "x"})
        assert db.latest_usage(sample_account.id) is None

    def test_usage_history_ordering(
        self, db: Database, sample_account: Account
    ) -> None:
        db.add_account(sample_account, {"session_key": "x"})
        base = datetime(2026, 1, 1, tzinfo=timezone.utc)
        for i in range(5):
            db.record_usage(
                sample_account.id,
                Usage(
                    account_id=sample_account.id,
                    provider=sample_account.provider,
                    fetched_at=base + timedelta(hours=i),
                    five_hour=ProviderLimits(five_hour_percent=float(i * 10)),
                ),
            )
        history = db.usage_history(sample_account.id)
        assert len(history) == 5
        assert [u.five_hour.five_hour_percent for u in history] == [
            0.0,
            10.0,
            20.0,
            30.0,
            40.0,
        ]

    def test_usage_history_since_filter(
        self, db: Database, sample_account: Account
    ) -> None:
        db.add_account(sample_account, {"session_key": "x"})
        base = datetime(2026, 1, 1, tzinfo=timezone.utc)
        for i in range(5):
            db.record_usage(
                sample_account.id,
                Usage(
                    account_id=sample_account.id,
                    provider=sample_account.provider,
                    fetched_at=base + timedelta(days=i),
                    five_hour=ProviderLimits(five_hour_percent=float(i)),
                ),
            )
        cutoff = base + timedelta(days=2)
        history = db.usage_history(sample_account.id, since=cutoff)
        assert len(history) == 3
        assert all(u.fetched_at >= cutoff for u in history)

    def test_usage_preserves_provider_round_trip(
        self, db: Database, sample_account: Account, second_account: Account
    ) -> None:
        # GROK account
        db.add_account(second_account, {"session_key": "x"})
        now = datetime(2026, 1, 4, tzinfo=timezone.utc)
        db.record_usage(
            second_account.id,
            Usage(
                account_id=second_account.id,
                provider=second_account.provider,  # GROK
                fetched_at=now,
                five_hour=ProviderLimits(five_hour_percent=10.0),
            ),
        )
        latest = db.latest_usage(second_account.id)
        assert latest is not None
        assert latest.provider is Provider.GROK

        # Non-Grok, non-Claude: CODEX
        codex_acct = Account(
            id="acc-codex",
            alias="codex1",
            provider=Provider.CODEX,
            created_at=datetime(2026, 1, 5, tzinfo=timezone.utc),
        )
        db.add_account(codex_acct, {"session_key": "y"})
        db.record_usage(
            codex_acct.id,
            Usage(
                account_id=codex_acct.id,
                provider=codex_acct.provider,  # CODEX
                fetched_at=now,
                five_hour=ProviderLimits(five_hour_percent=20.0),
            ),
        )
        latest_codex = db.latest_usage(codex_acct.id)
        assert latest_codex is not None
        assert latest_codex.provider is Provider.CODEX

        # Verify the raw column actually contains the value (no silent fallback).
        raw = db._conn.execute(  # noqa: SLF001 — intentional inspection
            "SELECT provider FROM usage_history WHERE account_id = ?",
            (second_account.id,),
        ).fetchone()[0]
        assert raw == "grok"


class TestPersistence:
    def test_db_persists_across_connections(
        self, tmp_path: Path, key: bytes, sample_account: Account
    ) -> None:
        path = tmp_path / "state.db"
        with Database(path, key) as db:
            db.add_account(sample_account, {"session_key": "x"})
        # New connection → same data.
        with Database(path, key) as db:
            result = db.get_account(sample_account.id)
            assert result is not None
            loaded, creds = result
            assert loaded == sample_account
            assert creds == {"session_key": "x"}

    def test_db_rejects_empty_key(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError):
            Database(tmp_path / "state.db", b"")  # type: ignore[arg-type]
