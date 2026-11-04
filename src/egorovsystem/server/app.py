"""ASGI application factory."""

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Optional

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from . import api_admin, api_device
from .config import Settings
from .db import Database
from .security import RateLimiter, SecurityHeadersMiddleware

STATIC_DIR = Path(__file__).parent / "static"

# Do not trust the platform's MIME registry (Windows sometimes maps .js to
# text/plain, which breaks module scripts under X-Content-Type-Options).
for _type, _ext in (("text/javascript", ".js"), ("text/css", ".css"), ("font/woff2", ".woff2"),
                    ("image/svg+xml", ".svg"), ("text/plain", ".txt"), ("image/png", ".png"),
                    ("application/manifest+json", ".webmanifest")):
    mimetypes.add_type(_type, _ext)

UPGRADE_MESSAGE = (
    "This Egorov server runs v1, which encrypts everything end to end. "
    "Upgrade this machine with `pip install -U egorovsystem` and set EGOROV_PWD to a device token "
    "created in the admin UI."
)


async def admin_page(request: Request) -> Response:
    index = STATIC_DIR / "index.html"
    if not index.exists():  # pragma: no cover - only in a broken install
        return PlainTextResponse("Admin UI files are missing from this installation.", status_code=500)
    return FileResponse(index, media_type="text/html; charset=utf-8")


async def service_worker(request: Request) -> Response:
    """The installed app's service worker. It lives at /admin/sw.js rather
    than under /admin/static/ because a worker only controls pages at or
    below its own path."""
    return FileResponse(STATIC_DIR / "sw.js", media_type="text/javascript",
                        headers={"Cache-Control": "no-cache"})


async def root(request: Request) -> Response:
    return RedirectResponse("/admin/", status_code=302)


async def health(request: Request) -> Response:
    return JSONResponse({"ok": True, "initialized": request.app.state.db.is_initialized()})


async def legacy(request: Request) -> Response:
    """Old clients POST to /get/... and expect errorflag/errormsg. Give them a
    clear instruction instead of a confusing failure."""
    return JSONResponse({"errorflag": True, "errormsg": UPGRADE_MESSAGE}, status_code=410)


def create_app(settings: Optional[Settings] = None) -> Starlette:
    settings = settings or Settings()
    database = Database(settings.db_path)
    if database.is_initialized():
        database.migrate()  # adds new columns in place; never drops data

    routes = [
        Route("/", root, methods=["GET"]),
        Route("/healthz", health, methods=["GET"]),
        Route("/admin", root, methods=["GET"]),
        Route("/admin/", admin_page, methods=["GET"]),
        Route("/admin/sw.js", service_worker, methods=["GET"]),
        Mount("/admin/static", app=StaticFiles(directory=STATIC_DIR, check_dir=False), name="static"),
        *api_device.routes,
        *api_admin.routes,
        Route("/get/{kind}/{key:path}", legacy, methods=["GET", "POST"]),
        Route("/add_password/{key:path}", legacy, methods=["GET", "POST"]),
        Route("/delete_password/{key:path}", legacy, methods=["GET", "POST"]),
    ]

    app = Starlette(
        routes=routes,
        middleware=[Middleware(SecurityHeadersMiddleware, trust_proxy=settings.trust_proxy)],
    )
    app.state.settings = settings
    app.state.db = database
    app.state.limiter = RateLimiter()
    return app
