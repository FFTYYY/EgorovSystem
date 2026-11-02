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

SCHEMA_VERSION = 2

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
    -- schema 2: display order, and for functions the variable names they
    -- reference (plaintext names, set by the browser), used for implicit grants
    position   REAL,
    refs       TEXT,
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
        self.migrate()

    def migrate(self) -> None:
        """Bring an existing database up to SCHEMA_VERSION.

        Only ever adds columns and fills them in; it never drops or rewrites
        data, so devices and stored items keep working across upgrades."""
        with self.write() as conn:
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(items)")}
            if "position" not in columns:
                conn.execute("ALTER TABLE items ADD COLUMN position REAL")
            if "refs" not in columns:
                conn.execute("ALTER TABLE items ADD COLUMN refs TEXT")
            # Items without a position keep their insertion order.
            conn.execute("UPDATE items SET position = rowid WHERE position IS NULL")
            set_meta(conn, "schema_version", str(SCHEMA_VERSION))

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


def next_position(conn: sqlite3.Connection, kind: str) -> float:
    """New items go to the end of their list."""
    return conn.execute("SELECT COALESCE(MAX(position), 0) + 1 FROM items WHERE kind = ?", (kind,)).fetchone()[0]


def parse_refs(value: Optional[str]) -> Optional[list]:
    if not value:
        return None
    try:
        refs = json.loads(value)
    except ValueError:
        return None
    return refs if isinstance(refs, list) else None


def row_to_item(row: sqlite3.Row) -> dict:
    return {
        "kind": row["kind"],
        "name": row["name"],
        "blob": row["blob"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "position": row["position"],
        "refs": parse_refs(row["refs"]),
    }


def function_refs(conn: sqlite3.Connection) -> dict:
    """{function name: [variable names it references]} for functions that have refs recorded."""
    rows = conn.execute("SELECT name, refs FROM items WHERE kind = 'function' AND refs IS NOT NULL")
    return {row["name"]: parse_refs(row["refs"]) or [] for row in rows}


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
