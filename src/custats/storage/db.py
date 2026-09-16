"""SQLite persistence layer.

The :class:`Database` is sync on purpose — SQLite's stdlib driver
is synchronous and the storage layer is meant to be thread-confined
(no shared instance across threads). Each instance owns its own
connection.

Credentials are encrypted at rest with AES-256-GCM; the key is
provided by the caller (typically loaded via
:func:`custats.storage.encrypted.load_or_create_key`).
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

from ..core.models import Account, Provider, ProviderLimits, Usage
from ..core.time_utils import iso, parse_iso
from .encrypted import decrypt, encrypt

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS accounts (
    id TEXT PRIMARY KEY,
    alias TEXT NOT NULL,
    provider TEXT NOT NULL,
    credential_blob BLOB NOT NULL,
    created_at TEXT NOT NULL,
    last_seen_at TEXT,
    is_active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS usage_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    five_hour_percent REAL,
    seven_day_percent REAL,
    five_hour_resets_at TEXT,
    seven_day_resets_at TEXT,
    extra_credits_remaining REAL,
    raw_json TEXT,
    FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_usage_account_time
    ON usage_history(account_id, fetched_at);
"""

_PRAGMAS = (
    "PRAGMA foreign_keys = ON",
    "PRAGMA journal_mode = WAL",
)


class Database:
    """Thin SQLite wrapper for accounts and usage history."""

    def __init__(self, db_path: Path, encryption_key: bytes) -> None:
        if not encryption_key:
            raise ValueError("encryption_key must be a non-empty bytes-like")
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._key = bytes(encryption_key)
        self._conn = sqlite3.connect(
            str(self._db_path),
            check_same_thread=True,
            isolation_level=None,  # autocommit; we manage txns explicitly
        )
        self._conn.row_factory = sqlite3.Row
        for pragma in _PRAGMAS:
            self._conn.execute(pragma)
        self._conn.executescript(SCHEMA_SQL)

    # ------------------------------------------------------------------ #
    # lifecycle
    # ------------------------------------------------------------------ #

    def close(self) -> None:
        """Close the underlying connection (idempotent)."""
        try:
            self._conn.close()
        except sqlite3.ProgrammingError:
            pass

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()

    # ------------------------------------------------------------------ #
    # accounts
    # ------------------------------------------------------------------ #

    def add_account(self, account: Account, credentials: dict[str, Any]) -> None:
        """Insert a new account row with encrypted credentials."""
        if not credentials:
            raise ValueError("credentials must not be empty")
        blob = encrypt(self._key, _json_dumps(credentials))
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO accounts
                    (id, alias, provider, credential_blob,
                     created_at, last_seen_at, is_active)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    account.id,
                    account.alias,
                    account.provider.value,
                    blob,
                    iso(account.created_at),
                    iso(account.last_seen_at) if account.last_seen_at else None,
                    1 if account.is_active else 0,
                ),
            )

    def update_account(self, account: Account) -> None:
        """Update alias / provider / last_seen_at / is_active (no creds)."""
        with self._conn:
            self._conn.execute(
                """
                UPDATE accounts
                   SET alias = ?,
                       provider = ?,
                       last_seen_at = ?,
                       is_active = ?
                 WHERE id = ?
                """,
                (
                    account.alias,
                    account.provider.value,
                    iso(account.last_seen_at) if account.last_seen_at else None,
                    1 if account.is_active else 0,
                    account.id,
                ),
            )

    def delete_account(self, account_id: str) -> None:
        """Remove an account and cascade-delete its usage history."""
        with self._conn:
            self._conn.execute("DELETE FROM accounts WHERE id = ?", (account_id,))

    def get_account(
        self, account_id: str
    ) -> tuple[Account, dict[str, Any]] | None:
        """Return the account and its decrypted credentials, or ``None``."""
        row = self._conn.execute(
            "SELECT * FROM accounts WHERE id = ?", (account_id,)
        ).fetchone()
        if row is None:
            return None
        return _row_to_account(row, self._key)

    def list_accounts(
        self, include_inactive: bool = False
    ) -> list[tuple[Account, dict[str, Any]]]:
        """Return every account (active-only by default)."""
        query = "SELECT * FROM accounts"
        if not include_inactive:
            query += " WHERE is_active = 1"
        query += " ORDER BY created_at ASC"
        rows = self._conn.execute(query).fetchall()
        return [_row_to_account(row, self._key) for row in rows]

    # ------------------------------------------------------------------ #
    # usage history
    # ------------------------------------------------------------------ #

    def record_usage(self, account_id: str, usage: Usage) -> None:
        """Append a usage snapshot to the history."""
        fetched_at = iso(usage.fetched_at)
        five_hour = usage.five_hour
        seven_day = usage.seven_day
        five_hour_pct = five_hour.five_hour_percent if five_hour else None
        raw_json = _json_dumps(usage.raw) if usage.raw is not None else None
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO usage_history
                    (account_id, provider, fetched_at,
                     five_hour_percent, seven_day_percent,
                     five_hour_resets_at, seven_day_resets_at,
                     extra_credits_remaining, raw_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    account_id,
                    usage.provider.value,
                    fetched_at,
                    five_hour_pct,
                    seven_day.seven_day_percent if seven_day else None,
                    iso(usage.five_hour_resets_at) if usage.five_hour_resets_at else None,
                    iso(usage.seven_day_resets_at) if usage.seven_day_resets_at else None,
                    usage.extra_credits_remaining,
                    raw_json,
                ),
            )
        with self._conn:
            self._conn.execute(
                "UPDATE accounts SET last_seen_at = ? WHERE id = ?",
                (fetched_at, account_id),
            )

    def latest_usage(self, account_id: str) -> Usage | None:
        """Return the most recent usage snapshot, or ``None``."""
        row = self._conn.execute(
            """
            SELECT * FROM usage_history
             WHERE account_id = ?
             ORDER BY fetched_at DESC, id DESC
             LIMIT 1
            """,
            (account_id,),
        ).fetchone()
        if row is None:
            return None
        return _row_to_usage(row)

    def usage_history(
        self, account_id: str, since: datetime | None = None
    ) -> list[Usage]:
        """Return usage snapshots ordered oldest → newest."""
        params: tuple[Any, ...]
        if since is None:
            query = (
                "SELECT * FROM usage_history WHERE account_id = ? "
                "ORDER BY fetched_at ASC, id ASC"
            )
            params = (account_id,)
        else:
            query = (
                "SELECT * FROM usage_history WHERE account_id = ? "
                "AND fetched_at >= ? ORDER BY fetched_at ASC, id ASC"
            )
            params = (account_id, iso(since))
        rows = self._conn.execute(query, params).fetchall()
        return [_row_to_usage(row) for row in rows]


# ---------------------------------------------------------------------- #
# helpers
# ---------------------------------------------------------------------- #


def _json_dumps(obj: Any) -> bytes:
    """Serialize to compact JSON bytes (UTF-8)."""
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _json_loads(blob: bytes) -> Any:
    return json.loads(blob.decode("utf-8"))


def _row_to_account(
    row: sqlite3.Row, key: bytes
) -> tuple[Account, dict[str, Any]]:
    creds = _json_loads(decrypt(key, row["credential_blob"]))
    if not isinstance(creds, dict):
        raise ValueError(f"decrypted credentials for {row['id']!r} are not a dict")
    account = Account(
        id=row["id"],
        alias=row["alias"],
        provider=Provider.parse(row["provider"]),
        created_at=parse_iso(row["created_at"]),
        last_seen_at=parse_iso(row["last_seen_at"]) if row["last_seen_at"] else None,
        is_active=bool(row["is_active"]),
    )
    return account, creds


def _row_to_usage(row: sqlite3.Row) -> Usage:
    fetched_at = parse_iso(row["fetched_at"])
    five_hour: ProviderLimits | None = None
    if row["five_hour_percent"] is not None:
        five_hour = ProviderLimits(five_hour_percent=row["five_hour_percent"])
    seven_day: ProviderLimits | None = None
    if row["seven_day_percent"] is not None:
        seven_day = ProviderLimits(seven_day_percent=row["seven_day_percent"])
    raw: dict[str, Any] | None = None
    if row["raw_json"]:
        decoded = _json_loads(row["raw_json"])
        if isinstance(decoded, dict):
            raw = decoded
    return Usage(
        account_id=row["account_id"],
        provider=Provider.parse(row["provider"]),
        fetched_at=fetched_at,
        five_hour=five_hour,
        five_hour_resets_at=(
            parse_iso(row["five_hour_resets_at"])
            if row["five_hour_resets_at"]
            else None
        ),
        seven_day=seven_day,
        seven_day_resets_at=(
            parse_iso(row["seven_day_resets_at"])
            if row["seven_day_resets_at"]
            else None
        ),
        extra_credits_remaining=row["extra_credits_remaining"],
        raw=raw,
    )


__all__ = ["Database", "SCHEMA_SQL"]
