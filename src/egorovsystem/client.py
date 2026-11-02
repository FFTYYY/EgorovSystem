"""Client side of Egorov. Fetches ciphertext and decrypts it on this machine.

Lookup order is the same as in v0:

1. values set with ``set_variable`` in this process;
2. direct connection when a server and a device token are given, either as
   arguments or through EGOROV_SERVER and EGOROV_PWD;
3. otherwise the local egolocal proxy (``egolocal-run``).
"""

from __future__ import annotations

import ipaddress
import os
import threading
from pathlib import Path
from typing import Optional, Union
from urllib.parse import quote, urlsplit

import requests

from . import crypto
from ._version import __version__
from .names import substitute

DEFAULT_PORT = 11451
DEFAULT_TIMEOUT = 15
USER_AGENT = f"egorovsystem/{__version__}"


class EgorovError(RuntimeError):
    """Base class. Subclasses RuntimeError so v0 code that caught
    RuntimeError keeps working."""


class NotFoundError(EgorovError):
    """The item does not exist or this device may not read it."""


class AuthError(EgorovError):
    """The device token was rejected."""


# ---------------------------------------------------------------- local files

def egorov_home() -> Path:
    return Path(os.environ.get("EGOROV_HOME") or Path.home() / ".egorov")


def local_token_path(port: int) -> Path:
    return egorov_home() / f"egolocal-{port}.token"


# ---------------------------------------------------------------- helpers

def _is_loopback_host(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def normalize_server(server: str) -> str:
    """Accept ``egorov.example.com``, ``https://egorov.example.com`` or, for
    local testing only, ``http://127.0.0.1:7999``."""
    text = (server or "").strip().rstrip("/")
    if not text:
        raise EgorovError("No Egorov server given. Set EGOROV_SERVER, e.g. egorov.example.com")
    if "://" not in text:
        text = "https://" + text
    parts = urlsplit(text)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise EgorovError(f"Not a valid server address: {server!r}")
    if parts.scheme == "http" and not _is_loopback_host(parts.hostname) and os.environ.get("EGOROV_ALLOW_HTTP") != "1":
        raise EgorovError("Refusing to talk to a remote Egorov server over plain HTTP. Use https://.")
    return f"{parts.scheme}://{parts.netloc}{parts.path}"


def _error_message(response: requests.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return f"HTTP {response.status_code}"
    return data.get("message") or data.get("errormsg") or data.get("error") or f"HTTP {response.status_code}"


def _raise_for(response: requests.Response, what: str) -> None:
    if response.status_code == 200:
        return
    message = _error_message(response)
    if response.status_code == 404:
        raise NotFoundError(f"{what} not found (or this device may not read it)")
    if response.status_code == 401:
        raise AuthError(f"Egorov rejected this device: {message}")
    raise EgorovError(f"Egorov error for {what}: {message}")


# ---------------------------------------------------------------- remote

class RemoteClient:
    """Talks to the Egorov server directly with a device token."""

    def __init__(self, server: str, token: Union[str, crypto.DeviceToken], timeout: float = DEFAULT_TIMEOUT):
        self.base = normalize_server(server)
        try:
            self.token = token if isinstance(token, crypto.DeviceToken) else crypto.parse_device_token(token)
        except crypto.CryptoError as exc:
            raise AuthError(str(exc)) from None
        self.timeout = timeout
        self._http = requests.Session()
        self._http.headers.update({"Authorization": self.token.authorization(), "User-Agent": USER_AGENT})
        if _is_loopback_host(urlsplit(self.base).hostname or ""):
            self._http.trust_env = False  # never send loopback traffic through an HTTP proxy
        self._vault: dict[str, bytes] = {}
        self._lock = threading.Lock()

    def __repr__(self) -> str:
        return f"RemoteClient({self.base!r}, device={self.token.device_id!r})"

    def _get(self, path: str, what: str) -> dict:
        try:
            response = self._http.get(self.base + path, timeout=self.timeout)
        except requests.RequestException as exc:
            raise EgorovError(f"Cannot reach the Egorov server at {self.base}: {exc}") from None
        _raise_for(response, what)
        return response.json()

    def _vault_key(self, wrapped: str) -> bytes:
        with self._lock:
            if wrapped not in self._vault:
                try:
                    self._vault = {wrapped: crypto.unwrap_vault_key(self.token.wrap_key, wrapped)}
                except crypto.CryptoError:
                    raise AuthError("This device token cannot unlock the vault. "
                                    "The vault key may have been rotated; create a new token.") from None
            return self._vault[wrapped]

    def whoami(self) -> dict:
        data = self._get("/api/v1/device/whoami", "device")
        self._vault_key(data["wrapped_vault_key"])  # proves the token can decrypt
        return {"id": data["id"], "name": data["name"], "scopes": data["scopes"]}

    def list_items(self) -> list:
        return self._get("/api/v1/device/items", "item list")["items"]

    def get_item(self, kind: str, name: str) -> dict:
        data = self._get(f"/api/v1/device/items/{kind}/{quote(name, safe='')}", f"{kind} {name!r}")
        vault_key = self._vault_key(data["wrapped_vault_key"])
        try:
            return crypto.decrypt_item(vault_key, kind, name, data["blob"])
        except crypto.CryptoError as exc:
            raise EgorovError(f"Could not decrypt {kind} {name!r}: {exc}") from None


# ---------------------------------------------------------------- local proxy

class LocalClient:
    """Talks to egolocal on this machine. egolocal holds the device token so
    that individual programs do not need it."""

    def __init__(self, port: int = DEFAULT_PORT, host: str = "127.0.0.1", timeout: float = DEFAULT_TIMEOUT):
        self.port = int(port)
        self.host = host
        self.timeout = timeout
        self._http = requests.Session()
        self._http.trust_env = False  # loopback only; ignore HTTP_PROXY and friends

    def _token(self) -> str:
        path = local_token_path(self.port)
        try:
            return path.read_text(encoding="ascii").strip()
        except OSError:
            raise EgorovError(
                f"egolocal is not running on port {self.port} (no {path}). "
                "Start it with `egolocal-run`, or set EGOROV_SERVER and EGOROV_PWD."
            ) from None

    def _get(self, path: str, what: str) -> dict:
        url = f"http://{self.host}:{self.port}{path}"
        try:
            response = self._http.get(url, headers={"X-Egorov-Local": self._token(), "User-Agent": USER_AGENT},
                                      timeout=self.timeout)
        except requests.RequestException as exc:
            raise EgorovError(f"Cannot reach egolocal on port {self.port}: {exc}") from None
        _raise_for(response, what)
        return response.json()

    def whoami(self) -> dict:
        return self._get("/v1/whoami", "device")

    def list_items(self) -> list:
        return self._get("/v1/items", "item list")["items"]

    def get_item(self, kind: str, name: str) -> dict:
        return self._get(f"/v1/items/{kind}/{quote(name, safe='')}", f"{kind} {name!r}")["payload"]


# ---------------------------------------------------------------- public API

_memory: dict[str, str] = {}
_clients: dict[tuple, RemoteClient] = {}
_clients_lock = threading.Lock()


def set_variable(key: str, val: str) -> None:
    """Override a variable in this process (handy for tests)."""
    _memory[key] = val


def get_client(port: int = DEFAULT_PORT, local_ip: str = "127.0.0.1",
               server_ip: Optional[str] = None, password: Optional[str] = None):
    server = server_ip or os.environ.get("EGOROV_SERVER")
    token = password or os.environ.get("EGOROV_PWD")
    if server and token:
        key = (server, token)
        with _clients_lock:
            if key not in _clients:
                _clients[key] = RemoteClient(server, token)
            return _clients[key]
    return LocalClient(port, local_ip)


def get_variable(key: str, port: int = DEFAULT_PORT, local_ip: str = "127.0.0.1",
                 server_ip: Optional[str] = None, password: Optional[str] = None) -> str:
    if key in _memory:
        return _memory[key]
    payload = get_client(port, local_ip, server_ip, password).get_item("variable", key)
    return payload.get("value", "")


def get_function(key: str, port: int = DEFAULT_PORT, local_ip: str = "127.0.0.1",
                 server_ip: Optional[str] = None, password: Optional[str] = None,
                 substitute_variables: bool = True) -> dict:
    """Return ``{"type": "cmd" | "python", "content": str, "note": str}`` with
    ``${variable}`` references already filled in."""
    payload = get_client(port, local_ip, server_ip, password).get_item("function", key)
    content = payload.get("content", "")
    if substitute_variables:
        def lookup(name: str) -> Optional[str]:
            try:
                return get_variable(name, port, local_ip, server_ip, password)
            except NotFoundError:
                return None
        content = substitute(content, lookup)
    return {"type": payload.get("type", "cmd"), "content": content, "note": payload.get("note", "")}


def get_data(port, local_ip, server_ip, password, type, key):  # noqa: A002 - v0 signature
    """v0 compatibility wrapper."""
    if type == "variable":
        return get_variable(key, port, local_ip, server_ip, password)
    if type == "function":
        return get_function(key, port, local_ip, server_ip, password)
    raise EgorovError(f"unknown item type {type!r}")


class _Egorov:
    """``Egorov["name"]``: a variable if one exists, otherwise a function."""

    def __getitem__(self, key: str):
        try:
            return get_variable(key)
        except NotFoundError:
            return get_function(key)

    def __repr__(self) -> str:
        return "Egorov"


Egorov = _Egorov()
