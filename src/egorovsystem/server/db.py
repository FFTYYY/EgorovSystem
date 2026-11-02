"""SQLite storage. The database holds ciphertext and metadata only.

One short-lived connection per operation keeps things simple and safe across
threads; SQLite in WAL mode handles this load easily.
"""

from __future__ import annotations

import json
import os
import random
import sqlite3
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS admin (
    id                  INTEGER PRIMARY KEY CHECK (id = 1),
    username            TEXT NOT NULL,
    kdf                 TEXT NOT NULL,
    verifier_salt       TEXT NOT NULL,
    verifier            TEXT NOT NULL,
    wrapped_vault_key   TEXT NOT NULL,
    totp_secret         TEXT,
    totp_enabled        INTEGER NOT NULL DEFAULT 0,
    totp_last_step      INTEGER NOT NULL DEFAULT 0,
    created_at          INTEGER NOT NULL,
    password_changed_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS items (
    kind       TEXT NOT NULL CHECK (kind IN ('variable', 'function')),
    name       TEXT NOT NULL,
    blob       TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY (kind, name)
);

CREATE TABLE IF NOT EXISTS devices (
    id                TEXT PRIMARY KEY,
    name              TEXT NOT NULL,
    auth_hash         TEXT NOT NULL,
    wrapped_vault_key TEXT NOT NULL,
    scopes            TEXT NOT NULL,
    enabled           INTEGER NOT NULL DEFAULT 1,
    created_at        INTEGER NOT NULL,
    expires_at        INTEGER,
    last_used_at      INTEGER,
    last_used_ip      TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    id_hash      TEXT PRIMARY KEY,
    csrf         TEXT NOT NULL,
    created_at   INTEGER NOT NULL,
    last_seen_at INTEGER NOT NULL,
    expires_at   INTEGER NOT NULL,
    ip           TEXT,
    user_agent   TEXT
);

CREATE TABLE IF NOT EXISTS audit (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    ts     INTEGER NOT NULL,
    actor  TEXT NOT NULL,
    action TEXT NOT NULL,
    target TEXT,
    ip     TEXT,
    ok     INTEGER NOT NULL,
    detail TEXT
);

CREATE INDEX IF NOT EXISTS audit_ts ON audit (ts);
"""


def now() -> int:
    return int(time.time())


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)

    # ------------------------------------------------------------ plumbing

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def write(self) -> Iterator[sqlite3.Connection]:
        """A transaction that takes the write lock up front."""
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def create(self) -> None:
        """Create the data directory and schema with private permissions."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if sys.platform != "win32":
            os.chmod(self.path.parent, 0o700)
            if not self.path.exists():
                fd = os.open(self.path, os.O_CREAT | os.O_WRONLY, 0o600)
                os.close(fd)
        with self.read() as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.executescript(SCHEMA)
            conn.execute(
                "INSERT OR IGNORE INTO meta (key, value) VALUES ('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )

    def exists(self) -> bool:
        return self.path.exists()

    def is_initialized(self) -> bool:
        if not self.path.exists():
            return False
        try:
            with self.read() as conn:
                return conn.execute("SELECT 1 FROM admin WHERE id = 1").fetchone() is not None
        except sqlite3.DatabaseError:
            return False

    def backup_to(self, destination: Path) -> None:
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with self.read() as src:
            dst = sqlite3.connect(destination)
            try:
                src.backup(dst)
            finally:
                dst.close()
        if sys.platform != "win32":
            os.chmod(destination, 0o600)


# ---------------------------------------------------------------- helpers

def get_meta(conn: sqlite3.Connection, key: str) -> Optional[str]:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return None if row is None else row["value"]


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) "
        "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def get_admin(conn: sqlite3.Connection) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT * FROM admin WHERE id = 1").fetchone()


def audit(
    conn: sqlite3.Connection,
    actor: str,
    action: str,
    target: Optional[str] = None,
    ip: Optional[str] = None,
    ok: bool = True,
    detail: Optional[dict] = None,
    retention_days: int = 365,
) -> None:
    conn.execute(
        "INSERT INTO audit (ts, actor, action, target, ip, ok, detail) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (now(), actor, action, target, ip, 1 if ok else 0, json.dumps(detail) if detail else None),
    )
    # Prune old entries now and then instead of running a scheduler.
    if random.random() < 0.01:
        conn.execute("DELETE FROM audit WHERE ts < ?", (now() - retention_days * 86400,))


def row_to_device(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "scopes": json.loads(row["scopes"]),
        "enabled": bool(row["enabled"]),
        "created_at": row["created_at"],
        "expires_at": row["expires_at"],
        "last_used_at": row["last_used_at"],
        "last_used_ip": row["last_used_ip"],
    }
