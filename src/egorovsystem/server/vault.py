"""Vault operations that run on the server machine through the CLI.

They need the admin password or the recovery key, typed by the operator, so
they never weaken the end-to-end model: nothing here stores a usable key.
"""

from __future__ import annotations

import json
import secrets
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from .. import crypto
from ..names import find_references, is_valid_name
from . import db as dbm
from .auth import check_verifier, make_verifier

MIN_PASSWORD_LENGTH = 12


class VaultError(Exception):
    pass


def password_problem(password: str, username: str = "") -> str | None:
    """A short reason why a master password is too weak, or None."""
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"use at least {MIN_PASSWORD_LENGTH} characters"
    if len(set(password)) < 5:
        return "use more distinct characters"
    if username and username.lower() in password.lower():
        return "do not include the username"
    return None


def initialize(database: dbm.Database, username: str, password: str,
               iterations: int = crypto.PBKDF2_ITERATIONS) -> str:
    """Create the admin account and vault. Returns the formatted recovery key."""
    if database.is_initialized():
        raise VaultError("this server is already initialized")
    database.create()
    kdf = crypto.new_kdf_params(iterations)
    keys = crypto.derive_admin_keys(password, kdf)
    vault_key = crypto.new_vault_key()
    recovery = crypto.new_recovery_key()
    salt, verifier = make_verifier(keys.auth_key)
    now = dbm.now()
    with database.write() as conn:
        conn.execute(
            "INSERT INTO admin (id, username, kdf, verifier_salt, verifier, wrapped_vault_key, created_at, "
            "password_changed_at) VALUES (1, ?, ?, ?, ?, ?, ?, ?)",
            (username, json.dumps(kdf), salt, verifier, crypto.wrap_vault_key(keys.wrap_key, vault_key), now, now))
        dbm.set_meta(conn, "server_secret", secrets.token_urlsafe(32))
        dbm.set_meta(conn, "recovery_wrapped_vault_key",
                     crypto.wrap_vault_key(crypto.recovery_wrap_key(recovery), vault_key))
        dbm.set_meta(conn, "initialized_at", str(now))
        dbm.set_meta(conn, "recovery_set_at", str(now))
        dbm.audit(conn, "cli", "vault.init", username)
    return crypto.format_recovery_key(recovery)


def unlock(database: dbm.Database, password: str) -> bytes:
    """Return the vault key, or raise VaultError for a wrong password."""
    with database.read() as conn:
        admin = dbm.get_admin(conn)
    if admin is None:
        raise VaultError("this server is not initialized; run egoserver-init first")
    keys = crypto.derive_admin_keys(password, json.loads(admin["kdf"]))
    if not check_verifier(keys.auth_key, admin["verifier_salt"], admin["verifier"]):
        raise VaultError("wrong password")
    return crypto.unwrap_vault_key(keys.wrap_key, admin["wrapped_vault_key"])


def reset_password(database: dbm.Database, recovery_text: str, new_password: str,
                   iterations: int = crypto.PBKDF2_ITERATIONS) -> None:
    """Set a new master password using the recovery key. Signs out everyone."""
    raw = crypto.parse_recovery_key(recovery_text)
    with database.read() as conn:
        wrapped = dbm.get_meta(conn, "recovery_wrapped_vault_key")
    if not wrapped:
        raise VaultError("no recovery key is set on this server")
    try:
        vault_key = crypto.unwrap_vault_key(crypto.recovery_wrap_key(raw), wrapped)
    except crypto.CryptoError as exc:
        raise VaultError("the recovery key does not match this server") from exc
    kdf = crypto.new_kdf_params(iterations)
    keys = crypto.derive_admin_keys(new_password, kdf)
    salt, verifier = make_verifier(keys.auth_key)
    with database.write() as conn:
        conn.execute(
            "UPDATE admin SET kdf = ?, verifier_salt = ?, verifier = ?, wrapped_vault_key = ?, "
            "password_changed_at = ? WHERE id = 1",
            (json.dumps(kdf), salt, verifier, crypto.wrap_vault_key(keys.wrap_key, vault_key), dbm.now()))
        conn.execute("DELETE FROM sessions")
        dbm.audit(conn, "cli", "admin.password.reset")


def disable_totp(database: dbm.Database) -> bool:
    with database.write() as conn:
        changed = conn.execute(
            "UPDATE admin SET totp_enabled = 0, totp_secret = NULL WHERE id = 1 AND totp_enabled = 1").rowcount
        if changed:
            dbm.audit(conn, "cli", "admin.totp.disable")
    return bool(changed)


def revoke_sessions(database: dbm.Database) -> int:
    with database.write() as conn:
        count = conn.execute("DELETE FROM sessions").rowcount
        dbm.audit(conn, "cli", "session.revoke_all", detail={"count": count})
    return count


@dataclass
class ImportReport:
    imported: list = field(default_factory=list)
    skipped_existing: list = field(default_factory=list)
    skipped_invalid: list = field(default_factory=list)


def read_legacy(path: Path) -> list[tuple[str, str, dict]]:
    """Read variables and functions from an old Django db.sqlite3."""
    conn = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        if "server_variable" not in tables:
            raise VaultError("this does not look like an old Egorov database (no server_variable table)")
        records: list[tuple[str, str, dict]] = []
        for row in conn.execute("SELECT key, val FROM server_variable ORDER BY id"):
            records.append(("variable", row["key"] or "", {"value": row["val"] or "", "note": ""}))
        if "server_function" in tables:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(server_function)")}
            type_column = "type" if "type" in columns else "'cmd'"
            for row in conn.execute(f"SELECT key, val, {type_column} AS type FROM server_function ORDER BY id"):
                ftype = row["type"] if row["type"] in ("cmd", "python") else "cmd"
                records.append(("function", row["key"] or "", {"type": ftype, "content": row["val"] or "", "note": ""}))
        return records
    finally:
        conn.close()


def import_legacy(database: dbm.Database, vault_key: bytes, path: Path,
                  overwrite: bool = False, dry_run: bool = False) -> ImportReport:
    report = ImportReport()
    records = read_legacy(path)
    now = dbm.now()
    with database.write() as conn:
        for kind, name, payload in records:
            if not is_valid_name(name):
                report.skipped_invalid.append((kind, name))
                continue
            exists = conn.execute("SELECT 1 FROM items WHERE kind = ? AND name = ?", (kind, name)).fetchone()
            if exists and not overwrite:
                report.skipped_existing.append((kind, name))
                continue
            report.imported.append((kind, name))
            if dry_run:
                continue
            blob = crypto.encrypt_item(vault_key, kind, name, payload)
            refs = json.dumps(find_references(payload.get("content", ""))) if kind == "function" else None
            conn.execute(
                "INSERT INTO items (kind, name, blob, created_at, updated_at, position, refs) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (kind, name) DO UPDATE SET blob = excluded.blob, updated_at = excluded.updated_at, "
                "refs = excluded.refs",
                (kind, name, blob, now, now, dbm.next_position(conn, kind), refs))
        if not dry_run:  # a dry run never writes, so the transaction commits nothing
            dbm.audit(conn, "cli", "vault.import_legacy", str(path),
                      detail={"imported": len(report.imported), "skipped": len(report.skipped_existing)})
    return report
