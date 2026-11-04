"""Server settings. Everything has a safe default; the CLI can override."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path


def default_data_dir() -> Path:
    """Where the database lives. Never inside the installed package."""
    env = os.environ.get("EGOROV_DATA_DIR")
    if env:
        return Path(env).expanduser()
    if sys.platform.startswith("linux") and hasattr(os, "geteuid") and os.geteuid() == 0:
        return Path("/var/lib/egorov")
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        return base / "egorov" / "server"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "egorov-server"
    base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / "egorov-server"


@dataclass
class Settings:
    data_dir: Path = field(default_factory=default_data_dir)
    # Trust CF-Connecting-IP / X-Forwarded-* only when the TCP peer is the
    # local machine, i.e. cloudflared. Requests from anywhere else use the
    # socket address no matter what headers they carry.
    trust_proxy: bool = True
    # Cookies are always Secure in production. Browsers also accept Secure
    # cookies on http://localhost, so this only matters for odd dev setups.
    secure_cookies: bool = True
    session_idle_seconds: int = 30 * 60
    session_max_seconds: int = 12 * 60 * 60
    # After a browser passes two-factor sign-in, it only needs the master
    # password for this long (counted from that sign-in, not extended by use).
    totp_remember_seconds: int = 48 * 60 * 60
    audit_retention_days: int = 365
    max_body_bytes: int = 2 * 1024 * 1024

    @property
    def db_path(self) -> Path:
        return self.data_dir / "egorov.sqlite3"

    @property
    def cookie_name(self) -> str:
        return "__Host-egorov" if self.secure_cookies else "egorov"

    @property
    def trust_cookie_name(self) -> str:
        return "__Host-egorov-trust" if self.secure_cookies else "egorov-trust"
