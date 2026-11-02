"""egoserver-init, egoserver-run and egoserver-admin."""

from __future__ import annotations

import argparse
import getpass
import ipaddress
import sys
import time
from pathlib import Path
from typing import Optional

from .. import __version__, crypto
from .config import Settings, default_data_dir
from .db import Database
from . import vault


def _settings(data_dir: Optional[str]) -> Settings:
    return Settings(data_dir=Path(data_dir).expanduser() if data_dir else default_data_dir())


def _ask_new_password(username: str) -> str:
    while True:
        password = getpass.getpass("Master password: ")
        problem = vault.password_problem(password, username)
        if problem:
            print(f"  Too weak: {problem}.")
            continue
        if getpass.getpass("Repeat master password: ") != password:
            print("  The passwords do not match.")
            continue
        return password


def _print_recovery_key(recovery: str) -> None:
    print()
    print("=" * 72)
    print("RECOVERY KEY - shown only once")
    print()
    print(f"    {recovery}")
    print()
    print("Store it offline (paper or a password manager). It is the only way to")
    print("reset a forgotten master password. Without both, the data is gone.")
    print("=" * 72)
    print()


# ---------------------------------------------------------------- init

def _interactive(func):
    """Turn Ctrl+C or Ctrl+D at a prompt into a clean exit instead of a traceback."""
    def wrapper(argv: Optional[list] = None) -> None:
        try:
            func(argv)
        except (EOFError, KeyboardInterrupt):
            sys.exit("\nCancelled.")
    wrapper.__name__ = func.__name__
    wrapper.__doc__ = func.__doc__
    return wrapper


@_interactive
def init(argv: Optional[list] = None) -> None:
    parser = argparse.ArgumentParser(prog="egoserver-init", description="Create the admin account and the encrypted vault.")
    parser.add_argument("--data-dir", help="where to keep the database (default: %(default)s)", default=None)
    parser.add_argument("--username", help="admin username")
    args = parser.parse_args(argv)
    settings = _settings(args.data_dir)
    database = Database(settings.db_path)
    if database.is_initialized():
        sys.exit(f"Already initialized: {settings.db_path}")

    print(f"Egorov server {__version__}")
    print(f"Data directory: {settings.data_dir}")
    print()
    print("The master password encrypts everything. The server never sees it.")
    username = (args.username or input("Admin username [admin]: ").strip() or "admin")
    password = _ask_new_password(username)
    print("Deriving keys...")
    recovery = vault.initialize(database, username, password)
    _print_recovery_key(recovery)
    print("Done. Start the server with: egoserver-run")


# ---------------------------------------------------------------- run

def run(argv: Optional[list] = None) -> None:
    parser = argparse.ArgumentParser(prog="egoserver-run", description="Run the Egorov server.")
    parser.add_argument("--host", default="127.0.0.1", help="bind address (default: %(default)s)")
    parser.add_argument("--port", type=int, default=7999, help="port (default: %(default)s)")
    parser.add_argument("--data-dir", default=None)
    parser.add_argument("--no-trust-proxy", action="store_true",
                        help="ignore CF-Connecting-IP / X-Forwarded-* even from localhost")
    parser.add_argument("--insecure-cookies", action="store_true",
                        help="drop the Secure cookie flag (only for plain-HTTP testing on a LAN)")
    parser.add_argument("--log-level", default="info")
    args = parser.parse_args(argv)

    import uvicorn

    from .app import create_app

    settings = _settings(args.data_dir)
    settings.trust_proxy = not args.no_trust_proxy
    settings.secure_cookies = not args.insecure_cookies
    database = Database(settings.db_path)
    if not database.is_initialized():
        sys.exit(f"No vault at {settings.db_path}. Run egoserver-init first.")
    try:
        public = not ipaddress.ip_address(args.host).is_loopback
    except ValueError:
        public = args.host != "localhost"
    if public:
        print("WARNING: listening on a non-loopback address. Behind Cloudflare Tunnel the")
        print("server should listen on 127.0.0.1 so that only cloudflared can reach it.")

    uvicorn.run(
        create_app(settings), host=args.host, port=args.port, log_level=args.log_level,
        proxy_headers=False, server_header=False, workers=1,
    )


# ---------------------------------------------------------------- admin

@_interactive
def admin(argv: Optional[list] = None) -> None:
    parser = argparse.ArgumentParser(prog="egoserver-admin", description="Maintenance tasks for the Egorov server.")
    parser.add_argument("--data-dir", default=None)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("info", help="show where the data is and what it contains")
    backup = sub.add_parser("backup", help="write a consistent copy of the database (it is already encrypted)")
    backup.add_argument("destination", help="a file, or a directory for timestamped copies")
    backup.add_argument("--keep", type=int, default=0,
                        help="with a directory: keep only the newest N copies (0 keeps all)")
    sub.add_parser("reset-password", help="set a new master password using the recovery key")
    sub.add_parser("disable-totp", help="turn off two-factor sign-in (if you lost your phone)")
    sub.add_parser("revoke-sessions", help="sign out every admin session")
    legacy = sub.add_parser("import-legacy", help="import variables and functions from an old v0 db.sqlite3")
    legacy.add_argument("path")
    legacy.add_argument("--overwrite", action="store_true", help="replace items that already exist")
    legacy.add_argument("--dry-run", action="store_true", help="show what would be imported")
    args = parser.parse_args(argv)

    settings = _settings(args.data_dir)
    database = Database(settings.db_path)
    if not database.is_initialized():
        sys.exit(f"No vault at {settings.db_path}. Run egoserver-init first.")
    database.migrate()

    try:
        if args.command == "info":
            _info(database, settings)
        elif args.command == "backup":
            print(f"Backup written to {_backup(database, Path(args.destination), args.keep)}")
        elif args.command == "reset-password":
            recovery = getpass.getpass("Recovery key: ")
            with database.read() as conn:
                username = conn.execute("SELECT username FROM admin WHERE id = 1").fetchone()[0]
            password = _ask_new_password(username)
            vault.reset_password(database, recovery, password)
            print("Master password changed. All admin sessions were signed out.")
        elif args.command == "disable-totp":
            print("Two-factor sign-in turned off." if vault.disable_totp(database) else "Two-factor sign-in was not on.")
        elif args.command == "revoke-sessions":
            print(f"Signed out {vault.revoke_sessions(database)} session(s).")
        elif args.command == "import-legacy":
            password = getpass.getpass("Master password: ")
            vault_key = vault.unlock(database, password)
            report = vault.import_legacy(database, vault_key, Path(args.path), args.overwrite, args.dry_run)
            verb = "Would import" if args.dry_run else "Imported"
            print(f"{verb} {len(report.imported)} item(s).")
            for kind, name in report.imported:
                print(f"  + {kind:8} {name}")
            for kind, name in report.skipped_existing:
                print(f"  = {kind:8} {name} (already exists; use --overwrite)")
            for kind, name in report.skipped_invalid:
                print(f"  ! {kind:8} {name!r} (name not allowed in v1; recreate it in the admin UI)")
    except (vault.VaultError, crypto.CryptoError) as exc:
        sys.exit(f"Error: {exc}")


def _backup(database: Database, destination: Path, keep: int) -> Path:
    """Back up to a file, or to a timestamped file inside a directory."""
    if destination.is_dir() or keep or str(destination).endswith(("/", "\\")):
        destination.mkdir(parents=True, exist_ok=True)
        target = destination / time.strftime("egorov-%Y%m%d-%H%M%S.sqlite3")
        database.backup_to(target)
        if keep > 0:
            for old in sorted(destination.glob("egorov-*.sqlite3"), reverse=True)[keep:]:
                old.unlink()
        return target
    database.backup_to(destination)
    return destination


def _info(database: Database, settings: Settings) -> None:
    with database.read() as conn:
        admin_row = conn.execute("SELECT username, totp_enabled FROM admin WHERE id = 1").fetchone()
        counts = dict(conn.execute("SELECT kind, COUNT(*) FROM items GROUP BY kind").fetchall())
        devices = conn.execute("SELECT id, name, enabled, last_used_at FROM devices ORDER BY created_at").fetchall()
    print(f"Egorov server {__version__}")
    print(f"Database:   {settings.db_path}")
    print(f"Admin:      {admin_row['username']} (two-factor {'on' if admin_row['totp_enabled'] else 'off'})")
    print(f"Variables:  {counts.get('variable', 0)}")
    print(f"Functions:  {counts.get('function', 0)}")
    print(f"Devices:    {len(devices)}")
    for device in devices:
        state = "enabled" if device["enabled"] else "disabled"
        print(f"  {device['id']}  {device['name']}  ({state})")
