"""HTTP hardening: security headers, client IP, body limits, rate limits."""

from __future__ import annotations

import ipaddress
import json
import re
import threading
import time
from collections import deque
from typing import Optional

from starlette.requests import Request
from starlette.responses import RedirectResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

CSP = "; ".join([
    "default-src 'none'",
    "script-src 'self'",
    "style-src 'self'",
    "img-src 'self' data:",
    "font-src 'self'",
    "connect-src 'self'",
    "manifest-src 'self'",
    "base-uri 'none'",
    "form-action 'none'",
    "frame-ancestors 'none'",
    # The admin UI never turns strings into HTML, so every DOM XSS sink stays
    # switched off. The one policy allowed can only produce the URL of the
    # service worker (see static/js/pwa.js), which registering it requires.
    "require-trusted-types-for 'script'",
    "trusted-types egorov-sw",
])

BASE_HEADERS = [
    (b"content-security-policy", CSP.encode()),
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"cross-origin-opener-policy", b"same-origin"),
    (b"cross-origin-resource-policy", b"same-origin"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=(), payment=(), usb=(), interest-cohort=()"),
]
HSTS = (b"strict-transport-security", b"max-age=31536000")


def _is_loopback(host: Optional[str]) -> bool:
    if not host:
        return False
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host == "localhost"


def _header(scope: Scope, name: bytes) -> Optional[str]:
    for key, value in scope.get("headers", []):
        if key == name:
            return value.decode("latin-1")
    return None


def peer_is_trusted_proxy(scope: Scope, trust_proxy: bool) -> bool:
    client = scope.get("client")
    return trust_proxy and bool(client) and _is_loopback(client[0])


def client_ip(request: Request) -> str:
    scope = request.scope
    if peer_is_trusted_proxy(scope, request.app.state.settings.trust_proxy):
        forwarded = _header(scope, b"cf-connecting-ip") or (_header(scope, b"x-forwarded-for") or "").split(",")[0]
        forwarded = forwarded.strip()
        if forwarded:
            try:
                return str(ipaddress.ip_address(forwarded))
            except ValueError:
                pass
    client = scope.get("client")
    return client[0] if client else "unknown"


def visitor_scheme(scope: Scope, trust_proxy: bool) -> Optional[str]:
    """The scheme the visitor used to reach Cloudflare, as reported by a
    trusted local proxy (cloudflared), or None when nothing reliable says so."""
    if not peer_is_trusted_proxy(scope, trust_proxy):
        return None
    try:
        scheme = json.loads(_header(scope, b"cf-visitor") or "{}").get("scheme")
    except (ValueError, AttributeError):
        scheme = None
    if scheme not in ("http", "https"):
        scheme = (_header(scope, b"x-forwarded-proto") or "").lower()
    return scheme if scheme in ("http", "https") else None


def request_is_https(scope: Scope, trust_proxy: bool) -> bool:
    return scope.get("scheme") == "https" or visitor_scheme(scope, trust_proxy) == "https"


_HOST_RE = re.compile(r"^[A-Za-z0-9.-]{1,253}(:[0-9]{1,5})?$")


def https_redirect_target(scope: Scope, trust_proxy: bool) -> Optional[str]:
    """Where to send a visitor who came in over plain HTTP, or None.

    The server does this itself so that no zone-wide Cloudflare setting
    (such as Always Use HTTPS) is needed. 308 keeps the method, so even old
    clients that POST get the upgrade notice over HTTPS."""
    if visitor_scheme(scope, trust_proxy) != "http":
        return None
    host = _header(scope, b"host") or ""
    if not _HOST_RE.match(host):
        return None
    path = scope.get("raw_path") or scope.get("path", "/").encode()
    target = f"https://{host}{path.decode('latin-1')}"
    query = scope.get("query_string", b"")
    return f"{target}?{query.decode('latin-1')}" if query else target


class SecurityHeadersMiddleware:
    """Adds security headers and sensible cache rules to every response."""

    def __init__(self, app: ASGIApp, trust_proxy: bool = True):
        self.app = app
        self.trust_proxy = trust_proxy

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        https = request_is_https(scope, self.trust_proxy)
        path = scope.get("path", "")

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = [(k, v) for k, v in message.get("headers", []) if k.lower() != b"server"]
                present = {k.lower() for k, _ in headers}
                for key, value in BASE_HEADERS:
                    if key not in present:
                        headers.append((key, value))
                if https:
                    headers.append(HSTS)
                if b"cache-control" not in present:
                    # API answers and the UI shell must never be cached by
                    # Cloudflare or the browser. Static files revalidate.
                    if path.startswith("/admin/static/"):
                        headers.append((b"cache-control", b"no-cache"))
                    else:
                        headers.append((b"cache-control", b"no-store"))
                message["headers"] = headers
            await send(message)

        redirect = https_redirect_target(scope, self.trust_proxy)
        if redirect:
            await RedirectResponse(redirect, status_code=308)(scope, receive, send_wrapper)
            return
        await self.app(scope, receive, send_wrapper)


class BodyTooLarge(Exception):
    pass


async def read_json(request: Request, max_bytes: int) -> object:
    """Read a JSON body without ever buffering more than ``max_bytes``."""
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > max_bytes:
        raise BodyTooLarge()
    chunks = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > max_bytes:
            raise BodyTooLarge()
        chunks.append(chunk)
    if not chunks:
        return None
    return json.loads(b"".join(chunks).decode("utf-8"))


class RateLimiter:
    """In-memory sliding-window counters. The server runs as one process, so
    this is exact; Cloudflare WAF rules add a second, distributed layer."""

    def __init__(self) -> None:
        self._events: dict[str, deque] = {}
        self._lock = threading.Lock()

    def _prune(self, key: str, window: float, now: float) -> deque:
        events = self._events.setdefault(key, deque())
        while events and events[0] <= now - window:
            events.popleft()
        return events

    def count(self, key: str, window: float) -> int:
        with self._lock:
            return len(self._prune(key, window, time.monotonic()))

    def add(self, key: str, window: float) -> int:
        with self._lock:
            now = time.monotonic()
            events = self._prune(key, window, now)
            events.append(now)
            if len(self._events) > 50_000:  # crude memory guard
                self._events = {k: v for k, v in self._events.items() if v}
            return len(events)

    def hit(self, key: str, limit: int, window: float) -> bool:
        """Record an event; return False once ``limit`` is exceeded."""
        return self.add(key, window) <= limit

    def retry_after(self, key: str, window: float) -> int:
        with self._lock:
            events = self._events.get(key)
            if not events:
                return 0
            return max(1, int(events[0] + window - time.monotonic()) + 1)

    def reset(self, key: str) -> None:
        with self._lock:
            self._events.pop(key, None)
