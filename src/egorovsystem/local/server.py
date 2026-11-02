"""egolocal: a per-user proxy that holds the device token in memory.

Programs on this machine ask egolocal instead of carrying EGOROV_PWD
themselves. To keep other users and web pages out:

* it listens on 127.0.0.1 only;
* every request must carry a random token that egolocal writes to a file only
  the current user can read (``~/.egorov/egolocal-<port>.token``);
* it sends no CORS headers and refuses any request that carries an Origin
  header, so browsers cannot read from it;
* it checks the Host header, which blocks DNS-rebinding tricks.
"""

from __future__ import annotations

import argparse
import getpass
import hmac
import json
import os
import re
import secrets
import signal
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import unquote

from .. import __version__
from ..client import DEFAULT_PORT, AuthError, EgorovError, NotFoundError, RemoteClient, egorov_home, local_token_path
from ..names import is_valid_kind, is_valid_name

ITEM_PATH = re.compile(r"^/v1/items/([a-z]+)/([^/]+)$")
UPGRADE = ("egolocal was upgraded to Egorov v1. Upgrade this program's egorovsystem package "
           "with `pip install -U egorovsystem`.")


def write_private_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if sys.platform != "win32":
        os.chmod(path.parent, 0o700)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(path, flags, 0o600)
    try:
        os.write(fd, content.encode("ascii"))
    finally:
        os.close(fd)
    if sys.platform != "win32":
        os.chmod(path, 0o600)


class LocalProxy:
    def __init__(self, remote: RemoteClient, port: int = DEFAULT_PORT, quiet: bool = False):
        self.remote = remote
        self.port = port
        self.quiet = quiet
        self.token = secrets.token_urlsafe(32)
        self.token_path = local_token_path(port)
        self.httpd: Optional[ThreadingHTTPServer] = None

    # ------------------------------------------------------------ lifecycle

    def start(self) -> None:
        handler = type("Handler", (_Handler,), {"proxy": self})
        self.httpd = ThreadingHTTPServer(("127.0.0.1", self.port), handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.token_path = local_token_path(self.port)
        write_private_file(self.token_path, self.token)

    def serve_forever(self) -> None:
        assert self.httpd is not None
        try:
            self.httpd.serve_forever()
        finally:
            self.close()

    def start_in_thread(self) -> threading.Thread:
        self.start()
        thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        thread.start()
        return thread

    def shutdown(self) -> None:
        if self.httpd is not None:
            self.httpd.shutdown()
        self.close()

    def close(self) -> None:
        if self.httpd is not None:
            self.httpd.server_close()
        try:
            if self.token_path.read_text(encoding="ascii").strip() == self.token:
                self.token_path.unlink()
        except OSError:
            pass

    # ------------------------------------------------------------ requests

    def allowed_hosts(self) -> set:
        return {f"127.0.0.1:{self.port}", f"localhost:{self.port}"}

    def handle(self, path: str) -> tuple[int, dict]:
        if path == "/v1/whoami":
            return 200, self.remote.whoami()
        if path == "/v1/items":
            return 200, {"items": self.remote.list_items()}
        match = ITEM_PATH.match(path)
        if match:
            kind, name = match.group(1), unquote(match.group(2))
            if not is_valid_kind(kind) or not is_valid_name(name):
                return 404, {"error": "not_found", "message": "No such item."}
            return 200, {"payload": self.remote.get_item(kind, name)}
        return 404, {"error": "not_found", "message": "Unknown path."}


class _Handler(BaseHTTPRequestHandler):
    proxy: LocalProxy
    server_version = "egolocal"
    sys_version = ""

    def _send(self, status: int, body: dict) -> None:
        data = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def _guard(self) -> bool:
        if self.headers.get("Host") not in self.proxy.allowed_hosts():
            self._send(403, {"error": "bad_host", "message": "Unexpected Host header."})
            return False
        if self.headers.get("Origin") is not None:
            self._send(403, {"error": "browser", "message": "Browsers may not use egolocal."})
            return False
        return True

    def do_GET(self) -> None:  # noqa: N802 - http.server API
        if not self._guard():
            return
        token = self.headers.get("X-Egorov-Local", "")
        if not hmac.compare_digest(token.encode(), self.proxy.token.encode()):
            self._send(401, {"error": "unauthorized", "message": "Missing or wrong egolocal token."})
            return
        try:
            status, body = self.proxy.handle(self.path.split("?", 1)[0])
        except NotFoundError as exc:
            status, body = 404, {"error": "not_found", "message": str(exc)}
        except AuthError as exc:
            status, body = 502, {"error": "upstream_auth", "message": str(exc)}
        except EgorovError as exc:
            status, body = 502, {"error": "upstream", "message": str(exc)}
        self._send(status, body)

    def do_POST(self) -> None:  # noqa: N802
        if not self._guard():
            return
        # v0 clients POST to /ask_variable/<key>; tell them to upgrade.
        self._send(410, {"errorflag": True, "errormsg": UPGRADE})

    def do_PUT(self) -> None:  # noqa: N802
        self._send(405, {"error": "method", "message": "Method not allowed."})

    do_DELETE = do_PATCH = do_OPTIONS = do_PUT

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        if not self.proxy.quiet:
            sys.stderr.write(f"egolocal: {self.command} {self.path.split('?', 1)[0]}\n")


def run(argv: Optional[list] = None) -> None:
    parser = argparse.ArgumentParser(prog="egolocal-run",
                                     description="Serve Egorov to programs on this machine without giving them the token.")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="port on 127.0.0.1 (default: %(default)s)")
    parser.add_argument("--server", help="server address (default: $EGOROV_SERVER)")
    parser.add_argument("--quiet", action="store_true", help="do not log requests")
    args = parser.parse_args(argv)

    server = args.server or os.environ.get("EGOROV_SERVER") or input("Egorov server (e.g. egorov.example.com): ").strip()
    token = os.environ.get("EGOROV_PWD") or getpass.getpass("Device token (ego1....): ").strip()
    try:
        remote = RemoteClient(server, token)
        me = remote.whoami()
    except EgorovError as exc:
        sys.exit(f"egolocal: {exc}")

    proxy = LocalProxy(remote, args.port, quiet=args.quiet)
    try:
        proxy.start()
    except OSError as exc:
        sys.exit(f"egolocal: cannot listen on 127.0.0.1:{args.port}: {exc}")
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    print(f"egolocal {__version__} serving device '{me['name']}' on 127.0.0.1:{proxy.port}")
    print(f"Access token for local programs: {proxy.token_path}")
    print("Press Ctrl+C to stop.")
    try:
        proxy.serve_forever()
    except KeyboardInterrupt:
        print("\negolocal stopped.")


if __name__ == "__main__":
    run()
