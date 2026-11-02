"""Authentication for admins (sessions) and devices (bearer tokens)."""

from __future__ import annotations

import base64
import functools
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import struct
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from .. import crypto
from ..names import scope_allows
from . import db as dbm
from .security import client_ip

SCRYPT_N, SCRYPT_R, SCRYPT_P = 2 ** 14, 8, 1


def error(status: int, code: str, message: str, **extra) -> JSONResponse:
    return JSONResponse({"error": code, "message": message, **extra}, status_code=status)


# ---------------------------------------------------------------- verifier

def make_verifier(auth_key: bytes, salt: Optional[bytes] = None) -> tuple[str, str]:
    """Server-side slow hash of the admin auth key (defence in depth: the
    auth key is already PBKDF2-stretched by the browser)."""
    salt = salt or os.urandom(16)
    digest = hashlib.scrypt(auth_key, salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=32, maxmem=64 * 1024 * 1024)
    return crypto.b64e(salt), crypto.b64e(digest)


def check_verifier(auth_key: bytes, salt_b64: str, verifier_b64: str) -> bool:
    _, digest = make_verifier(auth_key, crypto.b64d(salt_b64))
    return hmac.compare_digest(digest, verifier_b64)


def decode_auth_key(value: object) -> Optional[bytes]:
    if not isinstance(value, str):
        return None
    try:
        raw = crypto.b64d(value)
    except crypto.CryptoError:
        return None
    return raw if len(raw) == crypto.KEY_BYTES else None


def fake_kdf(conn: sqlite3.Connection, username: str) -> dict:
    """Stable, plausible KDF parameters for unknown usernames, so prelogin
    does not reveal which usernames exist."""
    secret = dbm.get_meta(conn, "server_secret") or ""
    salt = hmac.new(secret.encode(), b"fake-salt:" + username.encode("utf-8"), hashlib.sha256).digest()[:16]
    return {"alg": crypto.PBKDF2_ALG, "iterations": crypto.PBKDF2_ITERATIONS, "salt": crypto.b64e(salt)}


# ---------------------------------------------------------------- TOTP

def new_totp_secret() -> str:
    return base64.b32encode(os.urandom(20)).decode("ascii").rstrip("=")


def totp_code(secret_b32: str, step: int, digits: int = 6) -> str:
    key = base64.b32decode(secret_b32 + "=" * (-len(secret_b32) % 8))
    digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10 ** digits).zfill(digits)


def verify_totp(secret_b32: str, code: object, last_step: int, now: Optional[float] = None) -> Optional[int]:
    """Return the matched time step, or None. Rejects reuse of a step."""
    if not isinstance(code, str):
        return None
    code = code.strip().replace(" ", "")
    if not (len(code) == 6 and code.isdigit()):
        return None
    current = int((now if now is not None else time.time()) // 30)
    for step in (current - 1, current, current + 1):
        if step > last_step and hmac.compare_digest(totp_code(secret_b32, step), code):
            return step
    return None


def totp_uri(secret_b32: str, username: str, issuer: str = "Egorov") -> str:
    from urllib.parse import quote
    return (
        f"otpauth://totp/{quote(issuer)}:{quote(username)}"
        f"?secret={secret_b32}&issuer={quote(issuer)}&algorithm=SHA1&digits=6&period=30"
    )


# ---------------------------------------------------------------- sessions

@dataclass
class Session:
    id_hash: str
    csrf: str
    created_at: int
    expires_at: int

    @property
    def public_id(self) -> str:
        return self.id_hash[:16]


def _hash_session(raw: str) -> str:
    return hashlib.sha256(raw.encode("ascii")).hexdigest()


def create_session(conn: sqlite3.Connection, request: Request) -> tuple[str, Session]:
    settings = request.app.state.settings
    raw = secrets.token_urlsafe(32)
    now = dbm.now()
    session = Session(_hash_session(raw), secrets.token_urlsafe(32), now, now + settings.session_max_seconds)
    conn.execute(
        "INSERT INTO sessions (id_hash, csrf, created_at, last_seen_at, expires_at, ip, user_agent) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (session.id_hash, session.csrf, now, now, session.expires_at, client_ip(request),
         (request.headers.get("user-agent") or "")[:300]),
    )
    conn.execute("DELETE FROM sessions WHERE expires_at < ? OR last_seen_at < ?",
                 (now, now - settings.session_idle_seconds))
    return raw, session


def load_session(request: Request) -> Optional[Session]:
    settings = request.app.state.settings
    raw = request.cookies.get(settings.cookie_name)
    if not raw or len(raw) > 100:
        return None
    id_hash = _hash_session(raw)
    now = dbm.now()
    with request.app.state.db.write() as conn:
        row = conn.execute("SELECT * FROM sessions WHERE id_hash = ?", (id_hash,)).fetchone()
        if row is None:
            return None
        if row["expires_at"] < now or row["last_seen_at"] < now - settings.session_idle_seconds:
            conn.execute("DELETE FROM sessions WHERE id_hash = ?", (id_hash,))
            return None
        if now - row["last_seen_at"] >= 15:
            conn.execute("UPDATE sessions SET last_seen_at = ? WHERE id_hash = ?", (now, id_hash))
    return Session(row["id_hash"], row["csrf"], row["created_at"], row["expires_at"])


def set_session_cookie(response: Response, request: Request, raw: str) -> None:
    settings = request.app.state.settings
    response.set_cookie(
        settings.cookie_name, raw, max_age=settings.session_max_seconds, path="/",
        secure=settings.secure_cookies, httponly=True, samesite="strict",
    )


def clear_session_cookie(response: Response, request: Request) -> None:
    settings = request.app.state.settings
    response.delete_cookie(settings.cookie_name, path="/", secure=settings.secure_cookies,
                           httponly=True, samesite="strict")


Handler = Callable[..., Awaitable[Response]]


def require_admin(handler: Handler) -> Handler:
    """Session cookie for every call; CSRF header and same-origin fetch
    metadata for every state-changing call."""

    @functools.wraps(handler)
    async def wrapper(request: Request) -> Response:
        site = request.headers.get("sec-fetch-site")
        if site and site not in ("same-origin", "none"):
            return error(403, "cross_site", "Cross-site requests are not allowed.")
        session = load_session(request)
        if session is None:
            return error(401, "not_authenticated", "Please sign in.")
        if request.method not in ("GET", "HEAD"):
            token = request.headers.get("x-csrf-token", "")
            if not hmac.compare_digest(token.encode(), session.csrf.encode()):
                return error(403, "csrf", "Missing or invalid CSRF token.")
        request.state.session = session
        return await handler(request)

    return wrapper


# ---------------------------------------------------------------- devices

@dataclass
class Device:
    id: str
    name: str
    scopes: list
    wrapped_vault_key: str

    def allows(self, kind: str, name: str) -> bool:
        return scope_allows(self.scopes, kind, name)


DEVICE_FAILURE_LIMIT = 30
DEVICE_FAILURE_WINDOW = 600


def require_device(handler: Handler) -> Handler:
    @functools.wraps(handler)
    async def wrapper(request: Request) -> Response:
        ip = client_ip(request)
        limiter = request.app.state.limiter
        fail_key = f"device-fail:{ip}"
        if limiter.count(fail_key, DEVICE_FAILURE_WINDOW) >= DEVICE_FAILURE_LIMIT:
            return error(429, "rate_limited", "Too many failed attempts. Try again later.",
                         retry_after=limiter.retry_after(fail_key, DEVICE_FAILURE_WINDOW))
        device = authenticate_device(request, ip)
        if device is None:
            limiter.add(fail_key, DEVICE_FAILURE_WINDOW)
            return error(401, "unauthorized", "Invalid, disabled or expired device token.")
        request.state.device = device
        return await handler(request)

    return wrapper


def authenticate_device(request: Request, ip: str) -> Optional[Device]:
    header = request.headers.get("authorization", "")
    if not header.startswith("Bearer "):
        return None
    device_id, _, key_b64 = header[7:].strip().partition(".")
    if not crypto.DEVICE_ID_RE.match(device_id):
        return None
    auth_key = decode_auth_key(key_b64)
    if auth_key is None:
        return None
    now = dbm.now()
    with request.app.state.db.write() as conn:
        row = conn.execute("SELECT * FROM devices WHERE id = ?", (device_id,)).fetchone()
        if row is None or not hmac.compare_digest(row["auth_hash"], crypto.auth_hash(auth_key)):
            dbm.audit(conn, f"device:{device_id}", "device.auth", None, ip, ok=False)
            return None
        if not row["enabled"] or (row["expires_at"] and row["expires_at"] < now):
            dbm.audit(conn, f"device:{device_id}", "device.auth", None, ip, ok=False,
                      detail={"reason": "disabled" if not row["enabled"] else "expired"})
            return None
        if row["last_used_ip"] != ip or not row["last_used_at"] or now - row["last_used_at"] >= 60:
            conn.execute("UPDATE devices SET last_used_at = ?, last_used_ip = ? WHERE id = ?", (now, ip, device_id))
    return Device(row["id"], row["name"], json.loads(row["scopes"]), row["wrapped_vault_key"])
