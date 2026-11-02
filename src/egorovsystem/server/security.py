"""HTTP hardening: security headers, client IP, body limits, rate limits."""

from __future__ import annotations

import ipaddress
import json
import threading
import time
from collections import deque
from typing import Optional

from starlette.requests import Request
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
    # The admin UI never turns strings into HTML, so every DOM XSS sink can
    # be switched off outright.
    "require-trusted-types-for 'script'",
    "trusted-types 'none'",
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


def request_is_https(scope: Scope, trust_proxy: bool) -> bool:
    if scope.get("scheme") == "https":
        return True
    if peer_is_trusted_proxy(scope, trust_proxy):
        if (_header(scope, b"x-forwarded-proto") or "").lower() == "https":
            return True
        try:
            return json.loads(_header(scope, b"cf-visitor") or "{}").get("scheme") == "https"
        except (ValueError, AttributeError):
            return False
    return False


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
