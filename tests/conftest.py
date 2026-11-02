from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from egorovsystem import crypto
from egorovsystem.server import vault
from egorovsystem.server.app import create_app
from egorovsystem.server.config import Settings
from egorovsystem.server.db import Database

PASSWORD = "correct horse battery staple"
USERNAME = "admin"
FAST_ITERATIONS = 1000  # keeps the suite quick; production uses 600k


@pytest.fixture
def server(tmp_path):
    settings = Settings(data_dir=tmp_path / "data", secure_cookies=False)
    database = Database(settings.db_path)
    recovery = vault.initialize(database, USERNAME, PASSWORD, iterations=FAST_ITERATIONS)
    app = create_app(settings)
    return SimpleNamespace(settings=settings, db=database, app=app, recovery=recovery)


@pytest.fixture
def client(server):
    with TestClient(server.app) as test_client:
        yield test_client


class AdminSession:
    """Drives the admin API exactly like the browser UI does."""

    def __init__(self, client: TestClient, username: str = USERNAME, password: str = PASSWORD):
        self.client = client
        self.username = username
        self.password = password
        self.csrf = None
        self.vault_key = None
        self.keys = None

    def derive(self, password=None):
        kdf = self.client.post("/api/v1/admin/prelogin", json={"username": self.username}).json()["kdf"]
        return crypto.derive_admin_keys(password or self.password, kdf)

    def login(self, totp_code=None, expect=200):
        self.keys = self.derive()
        body = {"username": self.username, "auth_key": crypto.b64e(self.keys.auth_key)}
        if totp_code:
            body["totp_code"] = totp_code
        response = self.client.post("/api/v1/admin/login", json=body)
        assert response.status_code == expect, response.text
        if response.status_code == 200:
            data = response.json()
            self.csrf = data["csrf"]
            self.vault_key = crypto.unwrap_vault_key(self.keys.wrap_key, data["wrapped_vault_key"])
        return response

    @property
    def headers(self):
        return {"X-CSRF-Token": self.csrf}

    def get(self, path, **kw):
        return self.client.get(path, **kw)

    def post(self, path, payload=None, **kw):
        return self.client.post(path, json=payload, headers=self.headers, **kw)

    def put(self, path, payload=None, **kw):
        return self.client.put(path, json=payload, headers=self.headers, **kw)

    def patch(self, path, payload=None, **kw):
        return self.client.patch(path, json=payload, headers=self.headers, **kw)

    def delete(self, path, **kw):
        return self.client.delete(path, headers=self.headers, **kw)

    def current(self):
        return crypto.b64e(self.keys.auth_key)

    def create_variable(self, name, value, note=""):
        blob = crypto.encrypt_item(self.vault_key, "variable", name, {"value": value, "note": note})
        return self.post("/api/v1/admin/items", {"kind": "variable", "name": name, "blob": blob})

    def create_function(self, name, content, ftype="cmd", note=""):
        blob = crypto.encrypt_item(self.vault_key, "function", name, {"type": ftype, "content": content, "note": note})
        return self.post("/api/v1/admin/items", {"kind": "function", "name": name, "blob": blob})

    def create_device(self, name="laptop", scopes=None, expires_at=None):
        token, auth_hash, wrapped = crypto.provision_device(self.vault_key)
        payload = {"id": token.device_id, "name": name, "auth_hash": auth_hash,
                   "wrapped_vault_key": wrapped, "scopes": scopes or ["*"], "expires_at": expires_at}
        response = self.post("/api/v1/admin/devices", payload)
        assert response.status_code == 201, response.text
        return token


@pytest.fixture
def admin(client):
    session = AdminSession(client)
    session.login()
    return session


def device_get(client, token, path):
    return client.get(path, headers={"Authorization": token.authorization()})


def audit_entries(server):
    with server.db.read() as conn:
        rows = conn.execute("SELECT * FROM audit ORDER BY id").fetchall()
    return [dict(row, detail=json.loads(row["detail"]) if row["detail"] else None) for row in rows]
