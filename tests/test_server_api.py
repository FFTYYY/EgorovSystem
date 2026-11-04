import json
import sqlite3
import time

from starlette.testclient import TestClient

from egorovsystem import crypto
from egorovsystem.server import vault
from egorovsystem.server.auth import totp_code
from egorovsystem.server.db import Database

from conftest import PASSWORD, USERNAME, AdminSession, audit_entries, device_get


def test_status_and_security_headers(client):
    response = client.get("/api/v1/admin/status")
    assert response.json()["initialized"] is True
    assert response.json()["authenticated"] is False
    assert "require-trusted-types-for 'script'" in response.headers["content-security-policy"]
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-frame-options"] == "DENY"
    assert "server" not in response.headers
    assert client.get("/admin/static/index.html").headers["cache-control"] == "no-cache"
    assert client.get("/", follow_redirects=False).headers["location"] == "/admin/"


def test_prelogin_gives_stable_answers_for_unknown_users(client):
    first = client.post("/api/v1/admin/prelogin", json={"username": "mallory"}).json()["kdf"]
    second = client.post("/api/v1/admin/prelogin", json={"username": "mallory"}).json()["kdf"]
    other = client.post("/api/v1/admin/prelogin", json={"username": "eve"}).json()["kdf"]
    assert first == second
    assert first["salt"] != other["salt"]
    assert first["iterations"] == crypto.PBKDF2_ITERATIONS


def test_wrong_password_is_rejected_and_rate_limited(server, client):
    intruder = AdminSession(client, password="wrong password!")
    for _ in range(5):
        assert intruder.login(expect=401).json()["error"] == "invalid_credentials"
    response = intruder.login(expect=429)
    assert response.json()["retry_after"] > 0
    # The real password is also refused while the username is locked.
    AdminSession(client).login(expect=429)
    failures = [e for e in audit_entries(server) if e["action"] == "login" and not e["ok"]]
    assert len(failures) == 5


def test_attacker_cannot_lock_the_admin_out(server):
    attacker = AdminSession(TestClient(server.app, client=("198.51.100.66", 4000)), password="guessing wildly!")
    for _ in range(5):
        attacker.login(expect=401)
    attacker.login(expect=429)
    AdminSession(TestClient(server.app, client=("203.0.113.5", 4000))).login()


def test_admin_requires_session_and_csrf(admin, client):
    assert client.get("/api/v1/admin/items").status_code == 200
    blob = crypto.encrypt_item(admin.vault_key, "variable", "x", {"value": "1"})
    no_csrf = client.post("/api/v1/admin/items", json={"kind": "variable", "name": "x", "blob": blob})
    assert no_csrf.status_code == 403
    cross_site = client.post("/api/v1/admin/items", json={"kind": "variable", "name": "x", "blob": blob},
                             headers={**admin.headers, "Sec-Fetch-Site": "cross-site"})
    assert cross_site.status_code == 403
    anonymous = TestClient(client.app)
    assert anonymous.get("/api/v1/admin/items").status_code == 401


def test_item_lifecycle(admin):
    assert admin.create_variable("openai_keys", "sk-1", note="team key").status_code == 201
    assert admin.create_variable("openai_keys", "dup").status_code == 409
    assert admin.create_function("deploy", "echo ${openai_keys}").status_code == 201
    assert admin.post("/api/v1/admin/items", {"kind": "variable", "name": "x", "blob": "plain"}).status_code == 400
    assert admin.post("/api/v1/admin/items", {"kind": "variable", "name": "../x",
                                               "blob": "v1.AAAAAAAAAAAAAAAA.AAAAAAAAAAAAAAAAAAAAAA"}).status_code == 400

    items = admin.get("/api/v1/admin/items").json()["items"]
    assert [(i["kind"], i["name"]) for i in items] == [("function", "deploy"), ("variable", "openai_keys")]
    stored = next(i for i in items if i["name"] == "openai_keys")
    assert "sk-1" not in stored["blob"]
    assert crypto.decrypt_item(admin.vault_key, "variable", "openai_keys", stored["blob"])["note"] == "team key"

    renamed = crypto.encrypt_item(admin.vault_key, "variable", "openai", {"value": "sk-2"})
    response = admin.put("/api/v1/admin/items/variable/openai_keys", {"blob": renamed, "new_name": "openai"})
    assert response.status_code == 200 and response.json()["name"] == "openai"
    assert admin.delete("/api/v1/admin/items/variable/openai").status_code == 200
    assert admin.delete("/api/v1/admin/items/variable/openai").status_code == 404


def test_device_access_scopes_and_revocation(server, admin, client):
    admin.create_variable("openai_keys", "sk-1")
    admin.create_variable("aws_access", "AKIA")
    admin.create_function("deploy", "echo hi")
    token = admin.create_device("laptop", scopes=["variable:openai_*", "function:*"])

    me = device_get(client, token, "/api/v1/device/whoami").json()
    assert me["name"] == "laptop"
    vault_key = crypto.unwrap_vault_key(token.wrap_key, me["wrapped_vault_key"])
    assert vault_key == admin.vault_key

    item = device_get(client, token, "/api/v1/device/items/variable/openai_keys").json()
    assert crypto.decrypt_item(vault_key, "variable", "openai_keys", item["blob"])["value"] == "sk-1"
    assert device_get(client, token, "/api/v1/device/items/variable/aws_access").status_code == 404
    assert device_get(client, token, "/api/v1/device/items/variable/missing").status_code == 404
    names = [i["name"] for i in device_get(client, token, "/api/v1/device/items").json()["items"]]
    assert names == ["deploy", "openai_keys"]

    reads = [e for e in audit_entries(server) if e["action"] == "variable.read"]
    assert [(e["target"], e["ok"]) for e in reads] == [("openai_keys", 1), ("aws_access", 0), ("missing", 0)]

    devices = admin.get("/api/v1/admin/devices").json()["devices"]
    assert devices[0]["last_used_at"] is not None
    assert "auth_hash" not in devices[0] and "wrapped_vault_key" not in devices[0]

    assert admin.patch(f"/api/v1/admin/devices/{token.device_id}", {"enabled": False}).status_code == 200
    assert device_get(client, token, "/api/v1/device/whoami").status_code == 401
    assert admin.patch(f"/api/v1/admin/devices/{token.device_id}", {"enabled": True}).status_code == 200
    assert device_get(client, token, "/api/v1/device/whoami").status_code == 200
    assert admin.delete(f"/api/v1/admin/devices/{token.device_id}").status_code == 200
    assert device_get(client, token, "/api/v1/device/whoami").status_code == 401


def test_forged_and_expired_device_tokens(admin, client):
    token = admin.create_device("short", expires_at=int(time.time()) + 3600)
    forged = crypto.DeviceToken(token.device_id, crypto.new_recovery_key())
    assert device_get(client, forged, "/api/v1/device/whoami").status_code == 401
    with admin.client.app.state.db.write() as conn:
        conn.execute("UPDATE devices SET expires_at = 1 WHERE id = ?", (token.device_id,))
    assert device_get(client, token, "/api/v1/device/whoami").status_code == 401
    assert client.get("/api/v1/device/whoami", headers={"Authorization": "Bearer nonsense"}).status_code == 401


def test_device_failures_are_rate_limited(admin, client):
    token = admin.create_device()
    forged = crypto.DeviceToken(token.device_id, crypto.new_recovery_key())
    for _ in range(30):
        device_get(client, forged, "/api/v1/device/whoami")
    assert device_get(client, token, "/api/v1/device/whoami").status_code == 429


def test_totp_flow(admin, client):
    setup = admin.post("/api/v1/admin/totp/setup", {"current_auth_key": admin.current()})
    assert setup.status_code == 200
    secret = setup.json()["secret"]
    assert setup.json()["qr"].startswith("data:image/svg+xml")
    step = int(time.time() // 30)
    assert admin.post("/api/v1/admin/totp/enable", {"code": "12ab56"}).status_code == 400
    assert admin.post("/api/v1/admin/totp/enable", {"code": totp_code(secret, step)}).status_code == 200

    fresh = AdminSession(TestClient(client.app))
    assert fresh.login(expect=401).json()["error"] == "totp_required"
    # The step used to enable is spent; the next one works once.
    next_code = totp_code(secret, step + 1)
    fresh.login(totp_code=next_code)
    again = AdminSession(TestClient(client.app))
    assert again.login(totp_code=next_code, expect=401).json()["error"] == "invalid_totp"


def test_change_password_signs_out_other_sessions(admin, client):
    other = AdminSession(TestClient(client.app))
    other.login()
    new_password = "a completely new passphrase"
    kdf = crypto.new_kdf_params()
    keys = crypto.derive_admin_keys(new_password, kdf)
    response = admin.post("/api/v1/admin/password", {
        "current_auth_key": admin.current(), "kdf": kdf, "auth_key": crypto.b64e(keys.auth_key),
        "wrapped_vault_key": crypto.wrap_vault_key(keys.wrap_key, admin.vault_key),
    })
    assert response.status_code == 200, response.text
    assert other.get("/api/v1/admin/items").status_code == 401
    assert admin.get("/api/v1/admin/items").status_code == 200
    AdminSession(TestClient(client.app)).login(expect=401)
    newcomer = AdminSession(TestClient(client.app), password=new_password)
    newcomer.login()
    assert newcomer.vault_key == admin.vault_key


def test_sensitive_changes_need_the_current_password(admin):
    bad = crypto.b64e(crypto.new_vault_key())
    assert admin.post("/api/v1/admin/totp/setup", {"current_auth_key": bad}).status_code == 403
    weak_kdf = {**crypto.new_kdf_params(), "iterations": 1000}
    keys = crypto.derive_admin_keys("x", weak_kdf)
    response = admin.post("/api/v1/admin/password", {
        "current_auth_key": admin.current(), "kdf": weak_kdf, "auth_key": crypto.b64e(keys.auth_key),
        "wrapped_vault_key": crypto.wrap_vault_key(keys.wrap_key, admin.vault_key)})
    assert response.status_code == 400 and response.json()["error"] == "bad_kdf"


def test_vault_rotation_reencrypts_and_drops_devices(server, admin, client):
    admin.create_variable("a", "1")
    admin.create_function("f", "echo ${a}", ftype="cmd")
    token = admin.create_device()
    items = admin.get("/api/v1/admin/items").json()["items"]

    new_key = crypto.new_vault_key()
    recovery = crypto.new_recovery_key()
    reencrypted = []
    for item in items:
        payload = crypto.decrypt_item(admin.vault_key, item["kind"], item["name"], item["blob"])
        reencrypted.append({"kind": item["kind"], "name": item["name"],
                            "blob": crypto.encrypt_item(new_key, item["kind"], item["name"], payload)})
    body = {"current_auth_key": admin.current(), "items": reencrypted,
            "wrapped_vault_key": crypto.wrap_vault_key(admin.keys.wrap_key, new_key),
            "recovery_wrapped_vault_key": crypto.wrap_vault_key(crypto.recovery_wrap_key(recovery), new_key)}

    partial = {**body, "items": reencrypted[:1]}
    assert admin.post("/api/v1/admin/rotate", partial).status_code == 409
    response = admin.post("/api/v1/admin/rotate", body)
    assert response.status_code == 200 and response.json()["devices_removed"] == 1
    assert device_get(client, token, "/api/v1/device/whoami").status_code == 401

    relogin = AdminSession(TestClient(client.app))
    relogin.login()
    assert relogin.vault_key == new_key
    vault.reset_password(server.db, crypto.format_recovery_key(recovery), "after rotation passphrase", iterations=1000)
    assert vault.unlock(server.db, "after rotation passphrase") == new_key


def test_sessions_can_be_listed_and_revoked(admin, client):
    other = AdminSession(TestClient(client.app))
    other.login()
    sessions = admin.get("/api/v1/admin/sessions").json()["sessions"]
    assert len(sessions) == 2 and sum(s["current"] for s in sessions) == 1
    target = next(s for s in sessions if not s["current"])
    assert admin.delete(f"/api/v1/admin/sessions/{target['id']}").status_code == 200
    assert other.get("/api/v1/admin/items").status_code == 401
    assert admin.post("/api/v1/admin/logout").status_code == 200
    assert admin.get("/api/v1/admin/items").status_code == 401


def test_idle_sessions_expire(server, admin):
    with server.db.write() as conn:
        conn.execute("UPDATE sessions SET last_seen_at = last_seen_at - 3600")
    assert admin.get("/api/v1/admin/items").status_code == 401


def test_audit_listing_and_filters(admin):
    admin.create_variable("a", "1")
    admin.create_variable("b", "2")
    page = admin.get("/api/v1/admin/audit?limit=2").json()
    assert len(page["entries"]) == 2 and page["next_before"]
    rest = admin.get(f"/api/v1/admin/audit?before={page['next_before']}").json()["entries"]
    assert all(entry["id"] < page["next_before"] for entry in rest)
    creates = admin.get("/api/v1/admin/audit?action=variable.").json()["entries"]
    assert {e["target"] for e in creates} == {"a", "b"}
    overview = admin.get("/api/v1/admin/overview").json()
    assert overview["counts"]["variables"] == 2 and overview["account"]["username"] == USERNAME


def test_body_limit(admin):
    huge = "v1.AAAAAAAAAAAAAAAA." + "A" * (3 * 1024 * 1024)
    response = admin.post("/api/v1/admin/items", {"kind": "variable", "name": "big", "blob": huge})
    assert response.status_code == 413


def test_proxy_headers_are_ignored_from_untrusted_peers(server, client):
    client.post("/api/v1/admin/login", json={"username": "x", "auth_key": "AAAA"},
                headers={"CF-Connecting-IP": "203.0.113.9"})
    assert audit_entries(server)[-1]["ip"] == "testclient"
    trusted = TestClient(server.app, client=("127.0.0.1", 40000))
    trusted.post("/api/v1/admin/login", json={"username": "x", "auth_key": "AAAA"},
                 headers={"CF-Connecting-IP": "203.0.113.9", "X-Forwarded-Proto": "https"})
    assert audit_entries(server)[-1]["ip"] == "203.0.113.9"
    assert "strict-transport-security" in trusted.get("/healthz", headers={"X-Forwarded-Proto": "https"}).headers


def test_plain_http_visitors_are_redirected_to_https(server, client):
    tunnel = TestClient(server.app, client=("127.0.0.1", 40000))
    via_http = tunnel.post("/get/variable/x?a=1", headers={"Host": "egorov.example.org", "CF-Visitor": '{"scheme":"http"}'},
                           follow_redirects=False)
    assert via_http.status_code == 308
    assert via_http.headers["location"] == "https://egorov.example.org/get/variable/x?a=1"
    assert "content-security-policy" in via_http.headers
    xfp = tunnel.get("/admin/", headers={"Host": "egorov.example.org", "X-Forwarded-Proto": "http"}, follow_redirects=False)
    assert xfp.status_code == 308
    # Visitors already on https, local health checks and untrusted peers are left alone.
    assert tunnel.get("/healthz", headers={"CF-Visitor": '{"scheme":"https"}'}).status_code == 200
    assert tunnel.get("/healthz").status_code == 200
    assert client.get("/healthz", headers={"CF-Visitor": '{"scheme":"http"}'}, follow_redirects=False).status_code == 200


def test_old_clients_get_an_upgrade_message(client):
    response = client.post("/get/variable/openai_keys", json={"password": "abc"})
    assert response.status_code == 410
    assert response.json()["errorflag"] is True
    assert "pip install -U egorovsystem" in response.json()["errormsg"]


def test_reset_password_with_recovery_key(server, client):
    vault.reset_password(server.db, server.recovery, "brand new passphrase 1", iterations=1000)
    AdminSession(client).login(expect=401)
    AdminSession(TestClient(client.app), password="brand new passphrase 1").login()


def test_import_legacy_database(server, tmp_path):
    legacy = tmp_path / "old.sqlite3"
    conn = sqlite3.connect(legacy)
    conn.executescript("""
        CREATE TABLE server_variable (id INTEGER PRIMARY KEY, key TEXT, val TEXT);
        CREATE TABLE server_function (id INTEGER PRIMARY KEY, key TEXT, val TEXT, type TEXT);
        CREATE TABLE server_password (id INTEGER PRIMARY KEY, key TEXT, val TEXT);
        INSERT INTO server_variable (key, val) VALUES ('openai_keys', 'sk-legacy'), ('bad name', 'x');
        INSERT INTO server_function (key, val, type) VALUES ('hello', 'echo ${openai_keys}', 'cmd'),
                                                           ('py', 'print(1)', 'python');
        INSERT INTO server_password (key, val) VALUES ('laptop', 'abcdefghij');
    """)
    conn.commit()
    conn.close()

    vault_key = vault.unlock(server.db, PASSWORD)
    dry = vault.import_legacy(server.db, vault_key, legacy, dry_run=True)
    assert len(dry.imported) == 3
    with server.db.read() as c:
        assert c.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 0

    report = vault.import_legacy(server.db, vault_key, legacy)
    assert report.skipped_invalid == [("variable", "bad name")]
    with server.db.read() as c:
        rows = {(r["kind"], r["name"]): r["blob"] for r in c.execute("SELECT * FROM items")}
    assert crypto.decrypt_item(vault_key, "function", "py", rows[("function", "py")]) == {
        "type": "python", "content": "print(1)", "note": ""}
    with server.db.read() as c:
        refs = {r["name"]: r["refs"] for r in c.execute("SELECT name, refs FROM items WHERE kind = 'function'")}
    assert json.loads(refs["hello"]) == ["openai_keys"] and json.loads(refs["py"]) == []
    again = vault.import_legacy(server.db, vault_key, legacy)
    assert len(again.skipped_existing) == 3 and not again.imported


def test_unlock_rejects_wrong_password(server):
    try:
        vault.unlock(server.db, "nope nope nope")
    except vault.VaultError as exc:
        assert "wrong password" in str(exc)
    else:
        raise AssertionError("expected VaultError")


def test_database_file_holds_no_plaintext(server, admin):
    admin.create_variable("openai_keys", "sk-very-secret-value", note="secret note text")
    admin.create_function("deploy", "rsync --password=hunter2-secret")
    data = b"".join(p.read_bytes() for p in server.settings.data_dir.iterdir() if p.is_file())
    for needle in (b"sk-very-secret-value", b"secret note text", b"hunter2-secret", PASSWORD.encode()):
        assert needle not in data


def test_backup_is_a_working_copy(server, admin, tmp_path):
    admin.create_variable("a", "1")
    destination = tmp_path / "backup" / "copy.sqlite3"
    server.db.backup_to(destination)
    with Database(destination).read() as conn:
        assert conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 1


# ---------------------------------------------------------------- schema 2: order and refs

def test_migration_adds_columns_without_touching_data(server, admin, client):
    admin.create_variable("zeta", "1")
    admin.create_variable("alpha", "2")
    token = admin.create_device("laptop")
    with server.db.read() as conn:
        before = conn.execute("SELECT kind, name, blob FROM items ORDER BY rowid").fetchall()
        devices_before = conn.execute("SELECT id, auth_hash, wrapped_vault_key FROM devices").fetchall()
    # Turn the database back into a schema 1 database, as on a server running 1.0.0.
    with server.db.write() as conn:
        conn.execute("ALTER TABLE items DROP COLUMN position")
        conn.execute("ALTER TABLE items DROP COLUMN refs")
        conn.execute("DROP TABLE trusted_browsers")
    with server.db.write() as conn:
        conn.execute("UPDATE meta SET value = '1' WHERE key = 'schema_version'")
    server.db.migrate()
    server.db.migrate()  # running it twice is harmless
    with server.db.read() as conn:
        after = conn.execute("SELECT kind, name, blob FROM items ORDER BY position").fetchall()
        devices_after = conn.execute("SELECT id, auth_hash, wrapped_vault_key FROM devices").fetchall()
        version = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()[0]
    assert [tuple(r) for r in after] == [tuple(r) for r in reversed(before)]  # newest first
    assert [tuple(r) for r in devices_after] == [tuple(r) for r in devices_before]
    assert version == "4"
    assert device_get(client, token, "/api/v1/device/items/variable/zeta").status_code == 200
    with server.db.read() as conn:
        assert conn.execute("SELECT COUNT(*) FROM trusted_browsers").fetchone()[0] == 0


def test_upgrading_from_schema_2_lists_newest_first_once(server, admin):
    for name in ("oldest", "middle", "newest"):
        admin.create_variable(name, "x")
    names = lambda: [i["name"] for i in admin.get("/api/v1/admin/items").json()["items"] if i["kind"] == "variable"]
    # Schema 2 kept the oldest first.
    with server.db.write() as conn:
        conn.execute("UPDATE items SET position = rowid")
        conn.execute("UPDATE meta SET value = '2' WHERE key = 'schema_version'")
    assert names() == ["oldest", "middle", "newest"]
    server.db.migrate()
    assert names() == ["newest", "middle", "oldest"]
    # Later starts keep whatever order the admin chose.
    assert admin.post("/api/v1/admin/items/order", {"kind": "variable", "names": ["middle", "newest", "oldest"]}).status_code == 200
    server.db.migrate()
    assert names() == ["middle", "newest", "oldest"]


def test_new_items_go_first_and_can_be_reordered(admin, client):
    for name in ("zeta", "alpha", "mid"):
        admin.create_variable(name, "x")
    names = lambda: [i["name"] for i in admin.get("/api/v1/admin/items").json()["items"] if i["kind"] == "variable"]
    assert names() == ["mid", "alpha", "zeta"]
    assert admin.post("/api/v1/admin/items/order", {"kind": "variable", "names": ["zeta", "mid", "alpha"]}).status_code == 200
    assert names() == ["zeta", "mid", "alpha"]
    admin.create_variable("newest", "x")
    assert names()[0] == "newest"
    assert admin.post("/api/v1/admin/items/order", {"kind": "variable", "names": ["mid", "zeta"]}).status_code == 409
    token = admin.create_device("laptop")
    listed = [i["name"] for i in device_get(client, token, "/api/v1/device/items").json()["items"]]
    assert listed == ["newest", "zeta", "mid", "alpha"]


def test_granting_a_function_grants_the_variables_it_references(admin, client):
    admin.create_variable("key-a", "secret-a")
    admin.create_variable("other", "secret-b")
    blob = crypto.encrypt_item(admin.vault_key, "function", "f", {"type": "cmd", "content": "use ${key-a}", "note": ""})
    assert admin.post("/api/v1/admin/items", {"kind": "function", "name": "f", "blob": blob, "refs": ["key-a"]}).status_code == 201
    token = admin.create_device("f-only", scopes=["function:f"])
    assert device_get(client, token, "/api/v1/device/items/variable/key-a").status_code == 200
    assert device_get(client, token, "/api/v1/device/items/variable/other").status_code == 404
    listed = [(i["kind"], i["name"]) for i in device_get(client, token, "/api/v1/device/items").json()["items"]]
    assert listed == [("function", "f"), ("variable", "key-a")]
    # Dropping the reference drops the grant.
    assert admin.put("/api/v1/admin/items/function/f", {"blob": blob, "refs": []}).status_code == 200
    assert device_get(client, token, "/api/v1/device/items/variable/key-a").status_code == 404
    # The UI can also fill in refs for functions saved before refs existed.
    response = admin.post("/api/v1/admin/items/refs", {"items": [{"name": "f", "refs": ["key-a", "other"]}]})
    assert response.status_code == 200 and response.json()["updated"] == 1
    assert device_get(client, token, "/api/v1/device/items/variable/other").status_code == 200
    stored = next(i for i in admin.get("/api/v1/admin/items").json()["items"] if i["name"] == "f")
    assert stored["refs"] == ["key-a", "other"]


def test_bad_refs_are_rejected(admin):
    blob = crypto.encrypt_item(admin.vault_key, "function", "g", {"type": "cmd", "content": "", "note": ""})
    bad = admin.post("/api/v1/admin/items", {"kind": "function", "name": "g", "blob": blob, "refs": ["../etc"]})
    assert bad.status_code == 400 and bad.json()["error"] == "bad_refs"
    vblob = crypto.encrypt_item(admin.vault_key, "variable", "v", {"value": "1"})
    assert admin.post("/api/v1/admin/items", {"kind": "variable", "name": "v", "blob": vblob, "refs": []}).status_code == 400
    assert admin.post("/api/v1/admin/items/refs", {"items": [{"name": "g", "refs": "nope"}]}).status_code == 400


def _enable_totp(admin):
    secret = admin.post("/api/v1/admin/totp/setup", {"current_auth_key": admin.current()}).json()["secret"]
    step = int(time.time() // 30)
    assert admin.post("/api/v1/admin/totp/enable", {"code": totp_code(secret, step)}).status_code == 200
    return secret, step


def test_a_browser_that_passed_totp_needs_only_the_password_for_48_hours(admin, client, server):
    secret, step = _enable_totp(admin)
    browser = TestClient(client.app)
    first = AdminSession(browser)
    assert first.login(expect=401).json()["remember_seconds"] == 48 * 3600
    first.login(totp_code=totp_code(secret, step + 1))
    assert browser.cookies.get("egorov-trust")
    # The same browser signs in again with the password alone ...
    AdminSession(browser).login()
    assert audit_entries(server)[-1]["detail"] == {"totp": "remembered"}
    # ... other browsers still need the code ...
    assert AdminSession(TestClient(client.app)).login(expect=401).json()["error"] == "totp_required"
    # ... and so does this one once the 48 hours are over, however often it signed in.
    with server.db.write() as conn:
        conn.execute("UPDATE trusted_browsers SET expires_at = ?", (int(time.time()) - 1,))
    assert AdminSession(browser).login(expect=401).json()["error"] == "totp_required"


def test_trust_needs_the_password_and_a_cookie_the_server_issued(admin, client):
    secret, step = _enable_totp(admin)
    browser = TestClient(client.app)
    AdminSession(browser).login(totp_code=totp_code(secret, step + 1))
    AdminSession(browser, password="not the password").login(expect=401)
    forged = TestClient(client.app)
    forged.cookies.set("egorov-trust", "x" * 43)
    assert AdminSession(forged).login(expect=401).json()["error"] == "totp_required"


def test_enabling_totp_trusts_the_browser_that_did_it(admin, client):
    _enable_totp(admin)
    assert client.cookies.get("egorov-trust")
    AdminSession(client).login()
    account = admin.get("/api/v1/admin/overview").json()["account"]
    assert account["trusted_browsers"] == 1 and account["trusted_until"] > time.time() + 47 * 3600


def test_security_changes_make_every_browser_enter_a_code_again(admin, client):
    secret, step = _enable_totp(admin)
    other = TestClient(client.app)
    AdminSession(other).login(totp_code=totp_code(secret, step + 1))
    # Signing out other sessions also drops their trust, but keeps this browser's.
    assert admin.post("/api/v1/admin/sessions/revoke-others").status_code == 200
    assert AdminSession(other).login(expect=401).json()["error"] == "totp_required"
    AdminSession(client).login()
    # "Make every browser verify again" includes this one.
    admin = AdminSession(client)
    admin.login()
    assert admin.post("/api/v1/admin/totp/forget").json()["forgotten"] == 1
    assert AdminSession(TestClient(client.app, cookies=dict(client.cookies))).login(expect=401).json()["error"] == "totp_required"


def test_password_change_forgets_trusted_browsers(admin, client, server):
    _enable_totp(admin)
    new_password = "a completely new passphrase"
    kdf = crypto.new_kdf_params()
    keys = crypto.derive_admin_keys(new_password, kdf)
    response = admin.post("/api/v1/admin/password", {
        "current_auth_key": admin.current(), "kdf": kdf, "auth_key": crypto.b64e(keys.auth_key),
        "wrapped_vault_key": crypto.wrap_vault_key(keys.wrap_key, admin.vault_key)})
    assert response.status_code == 200
    with server.db.read() as conn:
        assert conn.execute("SELECT COUNT(*) FROM trusted_browsers").fetchone()[0] == 0


def test_cli_resets_forget_trusted_browsers(admin, server):
    from egorovsystem.server import vault as vault_module

    _enable_totp(admin)
    vault_module.revoke_sessions(server.db)
    with server.db.read() as conn:
        assert conn.execute("SELECT COUNT(*) FROM trusted_browsers").fetchone()[0] == 0
