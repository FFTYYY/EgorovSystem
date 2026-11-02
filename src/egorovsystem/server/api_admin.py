"""Endpoints behind the admin UI.

The browser does all encryption. The server validates shapes, enforces
authentication, CSRF and rate limits, stores blobs and writes the audit log.
"""

from __future__ import annotations

import hmac
import json
import re
from typing import Optional, Union

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from .. import __version__, crypto
from ..names import ITEM_KINDS, is_valid_kind, is_valid_name, normalize_scopes
from . import db as dbm
from .auth import (
    check_verifier, clear_session_cookie, create_session, decode_auth_key, error, fake_kdf,
    load_session, make_verifier, new_totp_secret, require_admin, set_session_cookie, totp_uri,
    verify_totp,
)
from .security import BodyTooLarge, client_ip, read_json

LOGIN_WINDOW = 15 * 60
LOGIN_IP_LIMIT = 10          # failures from one IP, any username
LOGIN_USER_IP_LIMIT = 5      # failures for one username from one IP
LOGIN_USER_LIMIT = 50        # failures for one username from everywhere; high so that
                             # strangers cannot lock the admin out on purpose
AUTH_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
SESSION_ID_RE = re.compile(r"^[0-9a-f]{16}$")
MAX_ITEM_BLOB = 1_500_000


# ---------------------------------------------------------------- helpers

async def _json(request: Request) -> Union[dict, Response]:
    try:
        body = await read_json(request, request.app.state.settings.max_body_bytes)
    except BodyTooLarge:
        return error(413, "too_large", "Request body is too large.")
    except (ValueError, UnicodeDecodeError):
        return error(400, "bad_json", "Request body is not valid JSON.")
    if not isinstance(body, dict):
        return error(400, "bad_json", "Request body must be a JSON object.")
    return body


def _audit(request: Request, conn, action: str, target: Optional[str] = None, ok: bool = True,
           detail: Optional[dict] = None, actor: Optional[str] = None) -> None:
    dbm.audit(conn, actor or "admin", action, target, client_ip(request), ok, detail,
              retention_days=request.app.state.settings.audit_retention_days)


def _session_payload(admin, session) -> dict:
    return {
        "username": admin["username"],
        "csrf": session.csrf,
        "kdf": json.loads(admin["kdf"]),
        "wrapped_vault_key": admin["wrapped_vault_key"],
        "totp_enabled": bool(admin["totp_enabled"]),
        "expires_at": session.expires_at,
    }


def _login_keys(ip: str, username: str) -> tuple:
    return ((f"login-ip:{ip}", LOGIN_IP_LIMIT),
            (f"login-user-ip:{username}:{ip}", LOGIN_USER_IP_LIMIT),
            (f"login-user:{username}", LOGIN_USER_LIMIT))


def _login_blocked(request: Request, ip: str, username: str) -> Optional[Response]:
    limiter = request.app.state.limiter
    for key, limit in _login_keys(ip, username):
        if limiter.count(key, LOGIN_WINDOW) >= limit:
            return error(429, "rate_limited", "Too many failed sign-in attempts. Try again later.",
                         retry_after=limiter.retry_after(key, LOGIN_WINDOW))
    return None


def _record_login_failure(request: Request, ip: str, username: str) -> None:
    limiter = request.app.state.limiter
    for key, _ in _login_keys(ip, username):
        limiter.add(key, LOGIN_WINDOW)


async def _verify_current(request: Request, value: object) -> Optional[Response]:
    """Re-check the admin password for sensitive changes."""
    ip = client_ip(request)
    with request.app.state.db.read() as conn:
        admin = dbm.get_admin(conn)
    blocked = _login_blocked(request, ip, admin["username"])
    if blocked:
        return blocked
    auth_key = decode_auth_key(value)
    if auth_key is None or not await run_in_threadpool(
            check_verifier, auth_key, admin["verifier_salt"], admin["verifier"]):
        _record_login_failure(request, ip, admin["username"])
        with request.app.state.db.write() as conn:
            _audit(request, conn, "admin.reauth", ok=False)
        return error(403, "wrong_password", "The current password is incorrect.")
    return None


def _same_text(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


# ---------------------------------------------------------------- sign in

async def status(request: Request) -> JSONResponse:
    initialized = request.app.state.db.is_initialized()
    session = load_session(request) if initialized else None
    return JSONResponse({"initialized": initialized, "authenticated": session is not None, "version": __version__})


async def prelogin(request: Request) -> Response:
    body = await _json(request)
    if isinstance(body, Response):
        return body
    username = body.get("username")
    if not isinstance(username, str) or not 1 <= len(username) <= 64:
        return error(400, "bad_request", "Username is required.")
    ip = client_ip(request)
    if not request.app.state.limiter.hit(f"prelogin:{ip}", 60, 600):
        return error(429, "rate_limited", "Too many requests. Try again later.")
    with request.app.state.db.read() as conn:
        admin = dbm.get_admin(conn)
        if admin is not None and _same_text(admin["username"], username):
            kdf = json.loads(admin["kdf"])
        else:
            kdf = fake_kdf(conn, username)
    return JSONResponse({"kdf": kdf})


async def login(request: Request) -> Response:
    body = await _json(request)
    if isinstance(body, Response):
        return body
    username = body.get("username")
    if not isinstance(username, str) or not 1 <= len(username) <= 64:
        return error(400, "bad_request", "Username is required.")
    ip = client_ip(request)
    blocked = _login_blocked(request, ip, username)
    if blocked:
        return blocked

    db = request.app.state.db
    with db.read() as conn:
        admin = dbm.get_admin(conn)
    auth_key = decode_auth_key(body.get("auth_key"))
    user_ok = admin is not None and _same_text(admin["username"], username)
    if user_ok and auth_key is not None:
        password_ok = await run_in_threadpool(check_verifier, auth_key, admin["verifier_salt"], admin["verifier"])
    else:
        # Spend the same time as a real check so timing reveals nothing.
        await run_in_threadpool(make_verifier, auth_key or b"\0" * 32)
        password_ok = False
    if not password_ok:
        _record_login_failure(request, ip, username)
        with db.write() as conn:
            _audit(request, conn, "login", username, ok=False, actor="anonymous")
        return error(401, "invalid_credentials", "Wrong username or password.")

    if admin["totp_enabled"]:
        code = body.get("totp_code")
        if not code:
            return error(401, "totp_required", "Enter the code from your authenticator app.")
        step = verify_totp(admin["totp_secret"], code, admin["totp_last_step"])
        if step is None:
            _record_login_failure(request, ip, username)
            with db.write() as conn:
                _audit(request, conn, "login", username, ok=False, detail={"reason": "totp"}, actor="anonymous")
            return error(401, "invalid_totp", "The authenticator code is wrong or was already used.")
        with db.write() as conn:
            updated = conn.execute(
                "UPDATE admin SET totp_last_step = ? WHERE id = 1 AND totp_last_step < ?", (step, step)).rowcount
        if not updated:
            return error(401, "invalid_totp", "The authenticator code is wrong or was already used.")

    request.app.state.limiter.reset(f"login-user-ip:{username}:{ip}")
    with db.write() as conn:
        raw, session = create_session(conn, request)
        _audit(request, conn, "login", username)
        admin = dbm.get_admin(conn)
    response = JSONResponse(_session_payload(admin, session))
    set_session_cookie(response, request, raw)
    return response


@require_admin
async def logout(request: Request) -> Response:
    with request.app.state.db.write() as conn:
        conn.execute("DELETE FROM sessions WHERE id_hash = ?", (request.state.session.id_hash,))
        _audit(request, conn, "logout")
    response = JSONResponse({"ok": True})
    clear_session_cookie(response, request)
    return response


@require_admin
async def session_info(request: Request) -> Response:
    with request.app.state.db.read() as conn:
        admin = dbm.get_admin(conn)
    return JSONResponse(_session_payload(admin, request.state.session))


@require_admin
async def overview(request: Request) -> Response:
    with request.app.state.db.read() as conn:
        counts = {kind: conn.execute("SELECT COUNT(*) FROM items WHERE kind = ?", (kind,)).fetchone()[0]
                  for kind in ITEM_KINDS}
        devices = conn.execute(
            "SELECT COUNT(*) AS total, COALESCE(SUM(enabled), 0) AS enabled FROM devices").fetchone()
        reads_24h = conn.execute(
            "SELECT COUNT(*) FROM audit WHERE action LIKE '%.read' AND ok = 1 AND ts > ?",
            (dbm.now() - 86400,)).fetchone()[0]
        failures_24h = conn.execute(
            "SELECT COUNT(*) FROM audit WHERE ok = 0 AND ts > ?", (dbm.now() - 86400,)).fetchone()[0]
        recent = conn.execute("SELECT * FROM audit ORDER BY id DESC LIMIT 12").fetchall()
        admin = dbm.get_admin(conn)
        meta = {key: dbm.get_meta(conn, key) for key in ("initialized_at", "vault_rotated_at", "recovery_set_at")}
    return JSONResponse({
        "counts": {"variables": counts["variable"], "functions": counts["function"],
                   "devices": devices["total"], "devices_enabled": devices["enabled"]},
        "reads_24h": reads_24h,
        "failures_24h": failures_24h,
        "recent": [_audit_row(row) for row in recent],
        "account": {"username": admin["username"], "totp_enabled": bool(admin["totp_enabled"]),
                    "password_changed_at": admin["password_changed_at"]},
        "vault": {key: int(value) if value else None for key, value in meta.items()},
        "version": __version__,
    })


# ---------------------------------------------------------------- items

@require_admin
async def list_items(request: Request) -> Response:
    with request.app.state.db.read() as conn:
        rows = conn.execute("SELECT * FROM items ORDER BY kind, name").fetchall()
    return JSONResponse({"items": [dict(row) for row in rows]})


def _check_item(kind: object, name: object, blob: object) -> Optional[Response]:
    if not is_valid_kind(kind):
        return error(400, "bad_kind", "Kind must be 'variable' or 'function'.")
    if not is_valid_name(name):
        return error(400, "bad_name", "Names use letters, digits, '_', '.', '-' (at most 128, not starting with '.' or '-').")
    if not crypto.is_blob(blob, MAX_ITEM_BLOB):
        return error(400, "bad_blob", "Value must be an encrypted blob.")
    return None


@require_admin
async def create_item(request: Request) -> Response:
    body = await _json(request)
    if isinstance(body, Response):
        return body
    kind, name, blob = body.get("kind"), body.get("name"), body.get("blob")
    problem = _check_item(kind, name, blob)
    if problem:
        return problem
    now = dbm.now()
    with request.app.state.db.write() as conn:
        if conn.execute("SELECT 1 FROM items WHERE kind = ? AND name = ?", (kind, name)).fetchone():
            return error(409, "exists", f"A {kind} with this name already exists.")
        conn.execute("INSERT INTO items (kind, name, blob, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                     (kind, name, blob, now, now))
        _audit(request, conn, f"{kind}.create", name)
        row = conn.execute("SELECT * FROM items WHERE kind = ? AND name = ?", (kind, name)).fetchone()
    return JSONResponse(dict(row), status_code=201)


@require_admin
async def update_item(request: Request) -> Response:
    kind, name = request.path_params["kind"], request.path_params["name"]
    body = await _json(request)
    if isinstance(body, Response):
        return body
    new_name = body.get("new_name") or name
    blob = body.get("blob")
    problem = _check_item(kind, new_name, blob)
    if problem:
        return problem
    with request.app.state.db.write() as conn:
        if not conn.execute("SELECT 1 FROM items WHERE kind = ? AND name = ?", (kind, name)).fetchone():
            return error(404, "not_found", "No such item.")
        if new_name != name:
            if conn.execute("SELECT 1 FROM items WHERE kind = ? AND name = ?", (kind, new_name)).fetchone():
                return error(409, "exists", f"A {kind} with this name already exists.")
            conn.execute("UPDATE items SET name = ?, blob = ?, updated_at = ? WHERE kind = ? AND name = ?",
                         (new_name, blob, dbm.now(), kind, name))
            _audit(request, conn, f"{kind}.rename", new_name, detail={"from": name})
        else:
            conn.execute("UPDATE items SET blob = ?, updated_at = ? WHERE kind = ? AND name = ?",
                         (blob, dbm.now(), kind, name))
            _audit(request, conn, f"{kind}.update", name)
        row = conn.execute("SELECT * FROM items WHERE kind = ? AND name = ?", (kind, new_name)).fetchone()
    return JSONResponse(dict(row))


@require_admin
async def delete_item(request: Request) -> Response:
    kind, name = request.path_params["kind"], request.path_params["name"]
    with request.app.state.db.write() as conn:
        deleted = conn.execute("DELETE FROM items WHERE kind = ? AND name = ?", (kind, name)).rowcount
        if deleted:
            _audit(request, conn, f"{kind}.delete", name)
    if not deleted:
        return error(404, "not_found", "No such item.")
    return JSONResponse({"ok": True})


# ---------------------------------------------------------------- devices

def _check_expiry(value: object) -> Union[int, None, Response]:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value <= dbm.now():
        return error(400, "bad_expiry", "Expiry must be a future Unix timestamp or null.")
    return value


def _check_device_name(value: object) -> Optional[Response]:
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= 80:
        return error(400, "bad_name", "Device name must be 1 to 80 characters.")
    return None


@require_admin
async def list_devices(request: Request) -> Response:
    with request.app.state.db.read() as conn:
        rows = conn.execute("SELECT * FROM devices ORDER BY created_at DESC").fetchall()
    return JSONResponse({"devices": [dbm.row_to_device(row) for row in rows]})


@require_admin
async def create_device(request: Request) -> Response:
    body = await _json(request)
    if isinstance(body, Response):
        return body
    device_id, name = body.get("id"), body.get("name")
    if not isinstance(device_id, str) or not crypto.DEVICE_ID_RE.match(device_id):
        return error(400, "bad_id", "Device id must be 12 lowercase hex characters.")
    problem = _check_device_name(name)
    if problem:
        return problem
    if not isinstance(body.get("auth_hash"), str) or not AUTH_HASH_RE.match(body["auth_hash"]):
        return error(400, "bad_auth_hash", "auth_hash must be a SHA-256 hex digest.")
    if not crypto.is_blob(body.get("wrapped_vault_key"), 1000):
        return error(400, "bad_blob", "wrapped_vault_key must be an encrypted blob.")
    try:
        scopes = normalize_scopes(body.get("scopes", ["*"]))
    except ValueError as exc:
        return error(400, "bad_scopes", str(exc))
    expires_at = _check_expiry(body.get("expires_at"))
    if isinstance(expires_at, Response):
        return expires_at
    with request.app.state.db.write() as conn:
        if conn.execute("SELECT 1 FROM devices WHERE id = ?", (device_id,)).fetchone():
            return error(409, "exists", "A device with this id already exists.")
        conn.execute(
            "INSERT INTO devices (id, name, auth_hash, wrapped_vault_key, scopes, enabled, created_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?, 1, ?, ?)",
            (device_id, name.strip(), body["auth_hash"], body["wrapped_vault_key"], json.dumps(scopes),
             dbm.now(), expires_at))
        _audit(request, conn, "device.create", device_id, detail={"name": name.strip(), "scopes": scopes})
        row = conn.execute("SELECT * FROM devices WHERE id = ?", (device_id,)).fetchone()
    return JSONResponse(dbm.row_to_device(row), status_code=201)


@require_admin
async def update_device(request: Request) -> Response:
    device_id = request.path_params["device_id"]
    body = await _json(request)
    if isinstance(body, Response):
        return body
    changes: dict = {}
    if "name" in body:
        problem = _check_device_name(body["name"])
        if problem:
            return problem
        changes["name"] = body["name"].strip()
    if "scopes" in body:
        try:
            changes["scopes"] = json.dumps(normalize_scopes(body["scopes"]))
        except ValueError as exc:
            return error(400, "bad_scopes", str(exc))
    if "enabled" in body:
        if not isinstance(body["enabled"], bool):
            return error(400, "bad_request", "enabled must be true or false.")
        changes["enabled"] = 1 if body["enabled"] else 0
    if "expires_at" in body:
        expires_at = _check_expiry(body["expires_at"])
        if isinstance(expires_at, Response):
            return expires_at
        changes["expires_at"] = expires_at
    if not changes:
        return error(400, "bad_request", "Nothing to change.")
    with request.app.state.db.write() as conn:
        assignments = ", ".join(f"{column} = ?" for column in changes)
        updated = conn.execute(f"UPDATE devices SET {assignments} WHERE id = ?",
                               (*changes.values(), device_id)).rowcount
        if not updated:
            return error(404, "not_found", "No such device.")
        _audit(request, conn, "device.update", device_id, detail={k: v for k, v in body.items() if k in changes})
        row = conn.execute("SELECT * FROM devices WHERE id = ?", (device_id,)).fetchone()
    return JSONResponse(dbm.row_to_device(row))


@require_admin
async def delete_device(request: Request) -> Response:
    device_id = request.path_params["device_id"]
    with request.app.state.db.write() as conn:
        row = conn.execute("SELECT name FROM devices WHERE id = ?", (device_id,)).fetchone()
        if row is None:
            return error(404, "not_found", "No such device.")
        conn.execute("DELETE FROM devices WHERE id = ?", (device_id,))
        _audit(request, conn, "device.delete", device_id, detail={"name": row["name"]})
    return JSONResponse({"ok": True})


# ---------------------------------------------------------------- audit

def _audit_row(row) -> dict:
    entry = dict(row)
    entry["ok"] = bool(entry["ok"])
    entry["detail"] = json.loads(entry["detail"]) if entry["detail"] else None
    return entry


@require_admin
async def list_audit(request: Request) -> Response:
    params = request.query_params
    try:
        limit = min(max(int(params.get("limit", 50)), 1), 200)
        before = int(params["before"]) if params.get("before") else None
    except ValueError:
        return error(400, "bad_request", "limit and before must be integers.")
    clauses, values = [], []
    if before is not None:
        clauses.append("id < ?")
        values.append(before)
    if params.get("actor"):
        clauses.append("actor LIKE ? ESCAPE '\\'")
        values.append(params["actor"].replace("%", "\\%").replace("_", "\\_") + "%")
    if params.get("action"):
        clauses.append("action LIKE ? ESCAPE '\\'")
        values.append(params["action"].replace("%", "\\%").replace("_", "\\_") + "%")
    if params.get("failed") == "1":
        clauses.append("ok = 0")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with request.app.state.db.read() as conn:
        rows = conn.execute(f"SELECT * FROM audit {where} ORDER BY id DESC LIMIT ?", (*values, limit + 1)).fetchall()
    entries = [_audit_row(row) for row in rows[:limit]]
    next_before = entries[-1]["id"] if len(rows) > limit else None
    return JSONResponse({"entries": entries, "next_before": next_before})


# ---------------------------------------------------------------- account

@require_admin
async def change_password(request: Request) -> Response:
    body = await _json(request)
    if isinstance(body, Response):
        return body
    problem = await _verify_current(request, body.get("current_auth_key"))
    if problem:
        return problem
    try:
        kdf = crypto.validate_kdf_params(body.get("kdf"))
    except crypto.CryptoError as exc:
        return error(400, "bad_kdf", str(exc))
    auth_key = decode_auth_key(body.get("auth_key"))
    if auth_key is None or not crypto.is_blob(body.get("wrapped_vault_key"), 1000):
        return error(400, "bad_request", "auth_key and wrapped_vault_key are required.")
    salt, verifier = await run_in_threadpool(make_verifier, auth_key)
    with request.app.state.db.write() as conn:
        conn.execute(
            "UPDATE admin SET kdf = ?, verifier_salt = ?, verifier = ?, wrapped_vault_key = ?, "
            "password_changed_at = ? WHERE id = 1",
            (json.dumps(kdf), salt, verifier, body["wrapped_vault_key"], dbm.now()))
        conn.execute("DELETE FROM sessions WHERE id_hash != ?", (request.state.session.id_hash,))
        _audit(request, conn, "admin.password")
    return JSONResponse({"ok": True})


@require_admin
async def set_recovery(request: Request) -> Response:
    """Replace the recovery key (the browser generates and wraps it)."""
    body = await _json(request)
    if isinstance(body, Response):
        return body
    problem = await _verify_current(request, body.get("current_auth_key"))
    if problem:
        return problem
    if not crypto.is_blob(body.get("recovery_wrapped_vault_key"), 1000):
        return error(400, "bad_blob", "recovery_wrapped_vault_key must be an encrypted blob.")
    with request.app.state.db.write() as conn:
        dbm.set_meta(conn, "recovery_wrapped_vault_key", body["recovery_wrapped_vault_key"])
        dbm.set_meta(conn, "recovery_set_at", str(dbm.now()))
        _audit(request, conn, "admin.recovery")
    return JSONResponse({"ok": True})


@require_admin
async def totp_setup(request: Request) -> Response:
    body = await _json(request)
    if isinstance(body, Response):
        return body
    problem = await _verify_current(request, body.get("current_auth_key"))
    if problem:
        return problem
    secret = new_totp_secret()
    with request.app.state.db.write() as conn:
        admin = dbm.get_admin(conn)
        if admin["totp_enabled"]:
            return error(409, "totp_enabled", "Two-factor authentication is already on.")
        conn.execute("UPDATE admin SET totp_secret = ? WHERE id = 1", (secret,))
    uri = totp_uri(secret, admin["username"])
    qr = None
    try:
        import segno
        qr = segno.make(uri, error="m").svg_data_uri(scale=5, border=2, dark="#1a1512", light="#efe6d4")
    except ImportError:  # pragma: no cover - segno is a declared dependency
        pass
    return JSONResponse({"secret": secret, "uri": uri, "qr": qr})


@require_admin
async def totp_enable(request: Request) -> Response:
    body = await _json(request)
    if isinstance(body, Response):
        return body
    with request.app.state.db.write() as conn:
        admin = dbm.get_admin(conn)
        if admin["totp_enabled"] or not admin["totp_secret"]:
            return error(409, "totp_state", "Start the setup first.")
        step = verify_totp(admin["totp_secret"], body.get("code"), admin["totp_last_step"])
        if step is None:
            return error(400, "invalid_totp", "The code is wrong. Check the clock on your phone.")
        conn.execute("UPDATE admin SET totp_enabled = 1, totp_last_step = ? WHERE id = 1", (step,))
        _audit(request, conn, "admin.totp.enable")
    return JSONResponse({"ok": True})


@require_admin
async def totp_disable(request: Request) -> Response:
    body = await _json(request)
    if isinstance(body, Response):
        return body
    problem = await _verify_current(request, body.get("current_auth_key"))
    if problem:
        return problem
    with request.app.state.db.write() as conn:
        admin = dbm.get_admin(conn)
        if not admin["totp_enabled"]:
            return error(409, "totp_state", "Two-factor authentication is not on.")
        step = verify_totp(admin["totp_secret"], body.get("code"), admin["totp_last_step"])
        if step is None:
            return error(400, "invalid_totp", "The authenticator code is wrong.")
        conn.execute("UPDATE admin SET totp_enabled = 0, totp_secret = NULL, totp_last_step = ? WHERE id = 1", (step,))
        _audit(request, conn, "admin.totp.disable")
    return JSONResponse({"ok": True})


@require_admin
async def list_sessions(request: Request) -> Response:
    current = request.state.session.id_hash
    with request.app.state.db.read() as conn:
        rows = conn.execute("SELECT * FROM sessions WHERE expires_at > ? ORDER BY last_seen_at DESC",
                            (dbm.now(),)).fetchall()
    return JSONResponse({"sessions": [{
        "id": row["id_hash"][:16], "created_at": row["created_at"], "last_seen_at": row["last_seen_at"],
        "expires_at": row["expires_at"], "ip": row["ip"], "user_agent": row["user_agent"],
        "current": row["id_hash"] == current,
    } for row in rows]})


@require_admin
async def delete_session(request: Request) -> Response:
    sid = request.path_params["sid"]
    if not SESSION_ID_RE.match(sid):
        return error(404, "not_found", "No such session.")
    with request.app.state.db.write() as conn:
        deleted = conn.execute("DELETE FROM sessions WHERE substr(id_hash, 1, 16) = ?", (sid,)).rowcount
        if deleted:
            _audit(request, conn, "session.revoke", sid)
    if not deleted:
        return error(404, "not_found", "No such session.")
    response = JSONResponse({"ok": True})
    if request.state.session.id_hash.startswith(sid):
        clear_session_cookie(response, request)
    return response


@require_admin
async def revoke_other_sessions(request: Request) -> Response:
    with request.app.state.db.write() as conn:
        count = conn.execute("DELETE FROM sessions WHERE id_hash != ?", (request.state.session.id_hash,)).rowcount
        _audit(request, conn, "session.revoke_others", detail={"count": count})
    return JSONResponse({"ok": True, "revoked": count})


@require_admin
async def rotate_vault(request: Request) -> Response:
    """Replace the vault key: every item re-encrypted by the browser, a new
    admin wrap, a new recovery wrap. All device tokens become invalid."""
    body = await _json(request)
    if isinstance(body, Response):
        return body
    problem = await _verify_current(request, body.get("current_auth_key"))
    if problem:
        return problem
    items = body.get("items")
    if not isinstance(items, list):
        return error(400, "bad_request", "items must be a list.")
    for key in ("wrapped_vault_key", "recovery_wrapped_vault_key"):
        if not crypto.is_blob(body.get(key), 1000):
            return error(400, "bad_blob", f"{key} must be an encrypted blob.")
    provided = {}
    for item in items:
        if not isinstance(item, dict):
            return error(400, "bad_request", "Each item must be an object.")
        problem = _check_item(item.get("kind"), item.get("name"), item.get("blob"))
        if problem:
            return problem
        provided[(item["kind"], item["name"])] = item["blob"]
    now = dbm.now()
    with request.app.state.db.write() as conn:
        existing = {(row["kind"], row["name"]) for row in conn.execute("SELECT kind, name FROM items")}
        if existing != set(provided):
            return error(409, "items_changed", "Items changed while rotating. Reload and try again.")
        for (kind, name), blob in provided.items():
            conn.execute("UPDATE items SET blob = ? WHERE kind = ? AND name = ?", (blob, kind, name))
        conn.execute("UPDATE admin SET wrapped_vault_key = ? WHERE id = 1", (body["wrapped_vault_key"],))
        dbm.set_meta(conn, "recovery_wrapped_vault_key", body["recovery_wrapped_vault_key"])
        dbm.set_meta(conn, "vault_rotated_at", str(now))
        dbm.set_meta(conn, "recovery_set_at", str(now))
        removed = conn.execute("DELETE FROM devices").rowcount
        _audit(request, conn, "vault.rotate", detail={"items": len(provided), "devices_removed": removed})
    return JSONResponse({"ok": True, "devices_removed": removed})


routes = [
    Route("/api/v1/admin/status", status, methods=["GET"]),
    Route("/api/v1/admin/prelogin", prelogin, methods=["POST"]),
    Route("/api/v1/admin/login", login, methods=["POST"]),
    Route("/api/v1/admin/logout", logout, methods=["POST"]),
    Route("/api/v1/admin/session", session_info, methods=["GET"]),
    Route("/api/v1/admin/overview", overview, methods=["GET"]),
    Route("/api/v1/admin/items", list_items, methods=["GET"]),
    Route("/api/v1/admin/items", create_item, methods=["POST"]),
    Route("/api/v1/admin/items/{kind}/{name}", update_item, methods=["PUT"]),
    Route("/api/v1/admin/items/{kind}/{name}", delete_item, methods=["DELETE"]),
    Route("/api/v1/admin/devices", list_devices, methods=["GET"]),
    Route("/api/v1/admin/devices", create_device, methods=["POST"]),
    Route("/api/v1/admin/devices/{device_id}", update_device, methods=["PATCH"]),
    Route("/api/v1/admin/devices/{device_id}", delete_device, methods=["DELETE"]),
    Route("/api/v1/admin/audit", list_audit, methods=["GET"]),
    Route("/api/v1/admin/password", change_password, methods=["POST"]),
    Route("/api/v1/admin/recovery", set_recovery, methods=["POST"]),
    Route("/api/v1/admin/totp/setup", totp_setup, methods=["POST"]),
    Route("/api/v1/admin/totp/enable", totp_enable, methods=["POST"]),
    Route("/api/v1/admin/totp/disable", totp_disable, methods=["POST"]),
    Route("/api/v1/admin/sessions", list_sessions, methods=["GET"]),
    Route("/api/v1/admin/sessions/revoke-others", revoke_other_sessions, methods=["POST"]),
    Route("/api/v1/admin/sessions/{sid}", delete_session, methods=["DELETE"]),
    Route("/api/v1/admin/rotate", rotate_vault, methods=["POST"]),
]
