"""Endpoints used by devices (EGO, the Python library, egolocal).

Every answer carries ciphertext only. A request for an item outside the
device's scope gets the same 404 as a missing item, so scopes do not leak
which names exist.
"""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from ..names import is_valid_kind, is_valid_name
from . import db as dbm
from .auth import error, require_device
from .security import client_ip


@require_device
async def whoami(request: Request) -> JSONResponse:
    device = request.state.device
    return JSONResponse({
        "id": device.id,
        "name": device.name,
        "scopes": device.scopes,
        "wrapped_vault_key": device.wrapped_vault_key,
    })


@require_device
async def list_items(request: Request) -> JSONResponse:
    device = request.state.device
    with request.app.state.db.read() as conn:
        rows = conn.execute("SELECT kind, name, updated_at FROM items ORDER BY kind, name").fetchall()
    items = [dict(row) for row in rows if device.allows(row["kind"], row["name"])]
    return JSONResponse({"items": items})


@require_device
async def get_item(request: Request) -> JSONResponse:
    device = request.state.device
    kind = request.path_params["kind"]
    name = request.path_params["name"]
    if not is_valid_kind(kind) or not is_valid_name(name):
        return error(404, "not_found", "No such item.")
    ip = client_ip(request)
    with request.app.state.db.write() as conn:
        row = None
        if device.allows(kind, name):
            row = conn.execute("SELECT * FROM items WHERE kind = ? AND name = ?", (kind, name)).fetchone()
        dbm.audit(conn, f"device:{device.id}", f"{kind}.read", name, ip, ok=row is not None,
                  retention_days=request.app.state.settings.audit_retention_days)
    if row is None:
        return error(404, "not_found", "No such item.")
    return JSONResponse({
        "kind": kind,
        "name": name,
        "blob": row["blob"],
        "updated_at": row["updated_at"],
        "wrapped_vault_key": device.wrapped_vault_key,
    })


routes = [
    Route("/api/v1/device/whoami", whoami, methods=["GET"]),
    Route("/api/v1/device/items", list_items, methods=["GET"]),
    Route("/api/v1/device/items/{kind}/{name}", get_item, methods=["GET"]),
]
