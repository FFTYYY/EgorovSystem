"""The browser (static/js/crypto.js) and Python (crypto.py) must agree.

Runs crypto.js under Node's WebCrypto, which implements the same API as
browsers. Skipped when Node is not installed.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from egorovsystem import crypto

CRYPTO_JS = Path(__file__).resolve().parents[1] / "src" / "egorovsystem" / "server" / "static" / "js" / "crypto.js"
NODE = shutil.which("node")

SCRIPT = r"""
import * as c from %(module)s;
import { readFileSync } from "node:fs";

const input = JSON.parse(readFileSync(process.argv[2], "utf8"));
const out = {};

const keys = await c.deriveAdminKeys(input.password, input.kdf);
out.admin_auth = c.b64e(keys.auth);
out.admin_wrap = c.b64e(keys.wrap);
const vault = await c.unwrapVaultKey(keys.wrap, input.wrapped_vault_key);
out.vault = c.b64e(vault);
out.python_item = await c.decryptItem(vault, "variable", input.item_name, input.item_blob);

const device = await c.deviceKeysFromToken(input.device_token);
out.device_auth_hash = await c.sha256Hex(device.auth);
out.device_vault = c.b64e(await c.unwrapVaultKey(device.wrap, input.device_wrapped));
out.recovery_vault = c.b64e(await c.unwrapVaultKey(await c.recoveryWrapKey(input.recovery_text), input.recovery_wrapped));

out.js_item = await c.encryptItem(vault, "function", "deploy", { type: "python", content: "print('héllo ✓')", note: "备注" });
const provisioned = await c.provisionDevice(vault);
out.js_device = provisioned;
out.js_recovery = await c.newRecovery(vault);
const newKdf = c.newKdfParams();
const newKeys = await c.deriveAdminKeys("新的 passphrase ✓", newKdf);
out.js_kdf = newKdf;
out.js_admin_auth = c.b64e(newKeys.auth);
out.js_admin_wrapped = await c.wrapVaultKey(newKeys.wrap, vault);
let tampered = false;
try { await c.decryptItem(vault, "variable", "other_name", input.item_blob); } catch (e) { tampered = e instanceof c.CryptoError; }
out.rejects_wrong_name = tampered;
console.log(JSON.stringify(out));
"""


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_python_and_browser_crypto_agree(tmp_path):
    password = "pässwörd with ✓ and 中文"
    kdf = crypto.new_kdf_params(2000)
    keys = crypto.derive_admin_keys(password, kdf)
    vault_key = crypto.new_vault_key()
    token, auth_hash, device_wrapped = crypto.provision_device(vault_key)
    recovery = crypto.new_recovery_key()
    payload = {"value": "sk-✓-秘密", "note": "multi\nline"}
    fixture = {
        "password": password,
        "kdf": kdf,
        "wrapped_vault_key": crypto.wrap_vault_key(keys.wrap_key, vault_key),
        "item_name": "openai_keys",
        "item_blob": crypto.encrypt_item(vault_key, "variable", "openai_keys", payload),
        "device_token": str(token),
        "device_wrapped": device_wrapped,
        "recovery_text": crypto.format_recovery_key(recovery),
        "recovery_wrapped": crypto.wrap_vault_key(crypto.recovery_wrap_key(recovery), vault_key),
    }
    (tmp_path / "input.json").write_text(json.dumps(fixture), encoding="utf-8")
    script = tmp_path / "interop.mjs"
    script.write_text(SCRIPT % {"module": json.dumps(CRYPTO_JS.as_uri())}, encoding="utf-8")
    result = subprocess.run([NODE, str(script), str(tmp_path / "input.json")],
                            capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)

    # JavaScript reads what Python wrote.
    assert out["admin_auth"] == crypto.b64e(keys.auth_key)
    assert out["admin_wrap"] == crypto.b64e(keys.wrap_key)
    assert out["vault"] == crypto.b64e(vault_key)
    assert out["python_item"] == payload
    assert out["device_auth_hash"] == auth_hash
    assert out["device_vault"] == crypto.b64e(vault_key)
    assert out["recovery_vault"] == crypto.b64e(vault_key)
    assert out["rejects_wrong_name"] is True

    # Python reads what JavaScript wrote.
    assert crypto.decrypt_item(vault_key, "function", "deploy", out["js_item"]) == {
        "type": "python", "content": "print('héllo ✓')", "note": "备注"}
    js_token = crypto.parse_device_token(out["js_device"]["token"])
    assert js_token.device_id == out["js_device"]["id"]
    assert crypto.auth_hash(js_token.auth_key) == out["js_device"]["authHash"]
    assert crypto.unwrap_vault_key(js_token.wrap_key, out["js_device"]["wrappedVaultKey"]) == vault_key
    js_recovery = crypto.parse_recovery_key(out["js_recovery"]["text"])
    assert crypto.unwrap_vault_key(crypto.recovery_wrap_key(js_recovery), out["js_recovery"]["wrapped"]) == vault_key
    assert crypto.validate_kdf_params(out["js_kdf"]) == out["js_kdf"]
    js_keys = crypto.derive_admin_keys("新的 passphrase ✓", out["js_kdf"])
    assert crypto.b64e(js_keys.auth_key) == out["js_admin_auth"]
    assert crypto.unwrap_vault_key(js_keys.wrap_key, out["js_admin_wrapped"]) == vault_key
