"""End-to-end: a real server process (uvicorn in a thread), the client library,
egolocal and EGO, all talking over real sockets."""

from __future__ import annotations

import json
import os
import stat
import sys
import threading
import time
from types import SimpleNamespace

import pytest
import requests
import uvicorn

import egorovsystem
from egorovsystem import client as client_mod
from egorovsystem import crypto, run_command
from egorovsystem.local.server import LocalProxy
from egorovsystem.server import vault
from egorovsystem.server.app import create_app
from egorovsystem.server.config import Settings
from egorovsystem.server.db import Database, now

PASSWORD = "end to end passphrase"

VARIABLES = {
    "openai_keys": "sk-live-123",
    "aws_access": "AKIA SECRET us-east-1 bucket",
    "name": "world",
}
FUNCTIONS = {
    "greet": {"type": "cmd", "content": "echo hello-${name}"},
    "deploy": {"type": "cmd", "content": "echo ${aws_access} ${openai_keys} ${HOME}"},
    "pyfn": {"type": "python", "content": "import sys\nprint('py', sys.argv[1:], '${name}')\n"},
    "fail": {"type": "cmd", "content": "exit 3"},
}


def add_device(db, vault_key, name, scopes):
    token, auth_hash, wrapped = crypto.provision_device(vault_key)
    with db.write() as conn:
        conn.execute(
            "INSERT INTO devices (id, name, auth_hash, wrapped_vault_key, scopes, enabled, created_at) "
            "VALUES (?, ?, ?, ?, ?, 1, ?)", (token.device_id, name, auth_hash, wrapped, json.dumps(scopes), now()))
    return str(token)


@pytest.fixture
def live(tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path / "data", secure_cookies=False)
    db = Database(settings.db_path)
    vault.initialize(db, "admin", PASSWORD, iterations=1000)
    vault_key = vault.unlock(db, PASSWORD)
    with db.write() as conn:
        for name, value in VARIABLES.items():
            conn.execute("INSERT INTO items (kind, name, blob, created_at, updated_at) VALUES ('variable', ?, ?, ?, ?)",
                         (name, crypto.encrypt_item(vault_key, "variable", name, {"value": value, "note": ""}), now(), now()))
        for name, payload in FUNCTIONS.items():
            conn.execute("INSERT INTO items (kind, name, blob, created_at, updated_at) VALUES ('function', ?, ?, ?, ?)",
                         (name, crypto.encrypt_item(vault_key, "function", name, {**payload, "note": ""}), now(), now()))
    full = add_device(db, vault_key, "workstation", ["*"])
    limited = add_device(db, vault_key, "ci", ["variable:openai_*", "variable:name", "function:*"])

    server = uvicorn.Server(uvicorn.Config(create_app(settings), host="127.0.0.1", port=0, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    port = server.servers[0].sockets[0].getsockname()[1]

    monkeypatch.setenv("EGOROV_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("EGOROV_SERVER", raising=False)
    monkeypatch.delenv("EGOROV_PWD", raising=False)
    client_mod._clients.clear()
    client_mod._memory.clear()
    yield SimpleNamespace(url=f"http://127.0.0.1:{port}", full=full, limited=limited, db=db, vault_key=vault_key)
    server.should_exit = True
    thread.join(5)
    client_mod._clients.clear()
    client_mod._memory.clear()


def use(monkeypatch, live, token):
    monkeypatch.setenv("EGOROV_SERVER", live.url)
    monkeypatch.setenv("EGOROV_PWD", token)


def test_v0_style_usage_with_environment(live, monkeypatch):
    use(monkeypatch, live, live.full)
    from egorovsystem import Egorov, get_function, get_variable  # the names YTools imports
    assert get_variable("openai_keys") == "sk-live-123"
    assert Egorov["openai_keys"] == "sk-live-123"
    greet = Egorov["greet"]
    assert greet["type"] == "cmd" and greet["content"] == "echo hello-world"
    deploy = get_function("deploy")["content"]
    assert deploy == "echo AKIA SECRET us-east-1 bucket sk-live-123 ${HOME}"
    with pytest.raises(RuntimeError):  # v0 code caught RuntimeError
        get_variable("missing")


def test_scopes_limit_reads_and_substitution(live, monkeypatch):
    use(monkeypatch, live, live.limited)
    assert egorovsystem.get_variable("openai_keys") == "sk-live-123"
    with pytest.raises(egorovsystem.NotFoundError):
        egorovsystem.get_variable("aws_access")
    content = egorovsystem.get_function("deploy")["content"]
    assert content == "echo ${aws_access} sk-live-123 ${HOME}"


def test_explicit_arguments_and_memory_override(live):
    assert egorovsystem.get_variable("name", server_ip=live.url, password=live.full) == "world"
    egorovsystem.set_variable("name", "override")
    assert egorovsystem.get_variable("name") == "override"
    assert egorovsystem.get_function("greet", server_ip=live.url, password=live.full)["content"] == "echo hello-override"


def test_bad_tokens_and_addresses(live, monkeypatch):
    forged = str(crypto.DeviceToken(crypto.parse_device_token(live.full).device_id, os.urandom(32)))
    use(monkeypatch, live, forged)
    with pytest.raises(egorovsystem.AuthError):
        egorovsystem.get_variable("name")
    use(monkeypatch, live, "abcdefghij")  # a v0 password
    with pytest.raises(egorovsystem.AuthError, match="device token"):
        egorovsystem.get_variable("name")
    assert client_mod.normalize_server("egorov.example.com") == "https://egorov.example.com"
    with pytest.raises(egorovsystem.EgorovError, match="plain HTTP"):
        client_mod.normalize_server("http://egorov.example.com")


def test_egolocal(live, monkeypatch):
    with pytest.raises(egorovsystem.EgorovError, match="egolocal is not running"):
        egorovsystem.get_variable("name", port=1)

    proxy = LocalProxy(client_mod.RemoteClient(live.url, live.full), port=0, quiet=True)
    proxy.start_in_thread()
    try:
        port = proxy.port
        assert egorovsystem.get_variable("openai_keys", port=port) == "sk-live-123"
        assert egorovsystem.get_function("greet", port=port)["content"] == "echo hello-world"
        assert proxy.token_path.read_text().strip() == proxy.token
        if sys.platform != "win32":
            assert stat.S_IMODE(proxy.token_path.stat().st_mode) == 0o600

        base = f"http://127.0.0.1:{port}"
        session = requests.Session()
        session.trust_env = False
        good = {"X-Egorov-Local": proxy.token}
        assert session.get(f"{base}/v1/items/variable/name").status_code == 401
        browser = session.get(f"{base}/v1/items/variable/name", headers={**good, "Origin": "https://evil.example"})
        assert browser.status_code == 403
        assert "access-control-allow-origin" not in {k.lower() for k in browser.headers}
        rebinding = session.get(f"{base}/v1/items/variable/name", headers={**good, "Host": f"evil.example:{port}"})
        assert rebinding.status_code == 403
        old = session.post(f"{base}/ask_variable/name")
        assert old.status_code == 410 and old.json()["errorflag"] is True
        assert session.get(f"{base}/v1/items/variable/nope", headers=good).status_code == 404
    finally:
        proxy.shutdown()
    assert not proxy.token_path.exists()


def test_ego_runs_functions(live, monkeypatch, capfd):
    use(monkeypatch, live, live.full)
    assert run_command.main(["greet"]) == 0
    assert "hello-world" in capfd.readouterr().out

    assert run_command.main(["pyfn", "x y", "z"]) == 0
    assert "py ['x y', 'z'] world" in capfd.readouterr().out

    assert run_command.main(["fail"]) == 3

    run_command.main(["--show", "deploy"])
    assert "sk-live-123" in capfd.readouterr().out
    run_command.main(["--list"])
    listing = capfd.readouterr().out
    assert "function  greet" in listing and "variable  openai_keys" in listing
    run_command.main(["--whoami"])
    assert "workstation" in capfd.readouterr().out

    with pytest.raises(SystemExit) as exc:
        run_command.run(["no_such_function"])
    assert exc.value.code == 1
    assert "not found" in capfd.readouterr().err


def test_granting_a_function_grants_its_variables(live, monkeypatch):
    # The admin UI records which variables each function uses; a device that
    # may run a function can then read exactly those variables.
    with live.db.write() as conn:
        conn.execute("UPDATE items SET refs = ? WHERE kind = 'function' AND name = 'greet'", (json.dumps(["name"]),))
    only_greet = add_device(live.db, live.vault_key, "only-greet", ["function:greet"])
    use(monkeypatch, live, only_greet)
    assert egorovsystem.get_function("greet")["content"] == "echo hello-world"
    assert egorovsystem.get_variable("name") == "world"
    with pytest.raises(egorovsystem.NotFoundError):
        egorovsystem.get_variable("openai_keys")
    names = [(i["kind"], i["name"]) for i in client_mod.get_client().list_items()]
    assert names == [("function", "greet"), ("variable", "name")]
