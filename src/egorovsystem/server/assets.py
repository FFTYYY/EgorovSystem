"""Fingerprinted URLs for the admin UI's files.

The interface is served from /admin/assets/<hash>/, where the hash covers the
content of every file in static/. Those URLs never change meaning, so
browsers and Cloudflare may keep them for a year, and a new release always
comes with new URLs: no cache can hand out an old module next to a new one.
(Cloudflare's zone-wide Browser Cache TTL would otherwise turn the "no-cache"
on /admin/static/ into a four-hour max-age.)

index.html and sw.js are written with plain static/ paths, so they still work
as files; the server fills in the fingerprinted directory when serving them.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Optional

from starlette.staticfiles import StaticFiles

IMMUTABLE = "public, max-age=31536000, immutable"
WORKER_LINE = 'const ASSETS = "static/";'


def fingerprint(directory: Path) -> str:
    """A short hash of every file's path and content under ``directory``."""
    digest = hashlib.sha256()
    for path in sorted(p for p in directory.rglob("*") if p.is_file()):
        digest.update(path.relative_to(directory).as_posix().encode("utf-8") + b"\0")
        digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


class AdminAssets:
    """The current asset directory and the two documents that point into it."""

    def __init__(self, directory: Path):
        self.directory = directory
        self.id = fingerprint(directory) if directory.is_dir() else "missing"
        self.base = f"assets/{self.id}/"  # relative to /admin/
        self.index = self._render("index.html", lambda text: text.replace('="static/', f'="{self.base}'))
        self.worker = self._render("sw.js", self._point_worker)

    def _render(self, name: str, transform) -> Optional[bytes]:
        path = self.directory / name
        if not path.exists():  # only in a broken install; the routes report it
            return None
        return transform(path.read_text(encoding="utf-8")).encode("utf-8")

    def _point_worker(self, source: str) -> str:
        if WORKER_LINE not in source:
            raise RuntimeError(f"sw.js must contain the line {WORKER_LINE!r}")
        return source.replace(WORKER_LINE, f'const ASSETS = "{self.base}";')


class ImmutableFiles(StaticFiles):
    """Static files whose URL changes whenever their content does."""

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["cache-control"] = IMMUTABLE
        return response
