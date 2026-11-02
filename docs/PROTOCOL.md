# Egorov v1 protocol

This is the contract between the server, the Python client
(`src/egorovsystem/crypto.py`) and the browser admin UI
(`src/egorovsystem/server/static/js/crypto.js`). Both implementations must
produce byte-identical results; `tests/test_crypto_interop.py` checks this.

## Threat model

The server, Cloudflare and anyone holding a copy of the database must not be
able to read variable values, function contents or notes. Names, timestamps,
device metadata and the audit log are visible to the server.

Not covered: a fully compromised server that serves malicious JavaScript to the
admin UI could capture the master password the next time the admin signs in.
Strict CSP with Trusted Types, Cloudflare Access in front of `/admin`, and the
CLI tools reduce this risk but cannot remove it. Devices trust the commands they
download (by request of the project owner).

## Encodings

* Binary values on the wire use **unpadded base64url** (`b64`).
* Strings are UTF-8. Passwords are normalized to **NFC** before use.

## Primitives

| Name | Definition |
| --- | --- |
| `PBKDF2(p, salt, n)` | PBKDF2-HMAC-SHA256, 32-byte output |
| `HKDF(ikm, info)` | RFC 5869 HKDF-SHA256, salt = 32 zero bytes, 32-byte output |
| `SEAL(k, m, aad)` | AES-256-GCM, random 12-byte nonce, 16-byte tag |

A sealed blob is the string `v1.<b64 nonce>.<b64 ciphertext||tag>`.

## Keys

* **Vault key** `V`: 32 random bytes. Generated once at init (or rotation).
* **Admin**: `kdf = {"alg": "pbkdf2-sha256", "iterations": 600000, "salt": b64(16 random bytes)}`.
  `M = PBKDF2(password, salt, iterations)`,
  `auth = HKDF(M, "egorov/v1/admin-auth")`,
  `wrap = HKDF(M, "egorov/v1/admin-wrap")`.
  The server stores `scrypt(auth, salt16, N=2^14, r=8, p=1)` and
  `SEAL(wrap, V, "egorov/v1/vault-key")`.
* **Device**: secret `S` = 32 random bytes, id = 12 lowercase hex characters.
  Token string: `ego1.<id>.<b64 S>` (this is what `EGOROV_PWD` holds).
  `auth = HKDF(S, "egorov/v1/device-auth")`,
  `wrap = HKDF(S, "egorov/v1/device-wrap")`.
  The server stores `hex(SHA-256(auth))` and `SEAL(wrap, V, "egorov/v1/vault-key")`.
  Requests send `Authorization: Bearer <id>.<b64 auth>`.
* **Recovery key** `R`: 32 random bytes shown as base32 in groups of four.
  The server stores `SEAL(HKDF(R, "egorov/v1/recovery-wrap"), V, "egorov/v1/vault-key")`.

## Items

An item is identified by `kind` (`variable` or `function`) and `name`
(`[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}`).

```
blob = SEAL(V, utf8(json(payload)), "egorov/v1/item/<kind>/<name>")
variable payload: {"value": str, "note": str}
function payload: {"type": "cmd" | "python", "content": str, "note": str}
```

Binding the AAD to kind and name stops the server from swapping ciphertexts
between items. Renaming an item therefore re-encrypts it.

### `${variable}` substitution

Done by the client after decrypting a function. Each `${name}` whose name is a
valid item name is replaced, in a single pass, by that variable's value if the
device can read it. Unknown names are left untouched (so `${HOME}` still reaches
the shell). Substituted values are never expanded again.

## Device API

| Method and path | Response |
| --- | --- |
| `GET /api/v1/device/whoami` | `{id, name, scopes, wrapped_vault_key}` |
| `GET /api/v1/device/items` | `{items: [{kind, name, updated_at}]}` within scope |
| `GET /api/v1/device/items/<kind>/<name>` | `{kind, name, blob, updated_at, wrapped_vault_key}` |

Items outside the device's scopes return the same `404` as missing items.
Scopes are globs on names, optionally prefixed `variable:` or `function:`.

## Admin API

All admin calls use the `__Host-egorov` session cookie (HttpOnly, Secure,
SameSite=Strict). Every non-GET call also needs `X-CSRF-Token`. Sign-in:

1. `POST /api/v1/admin/prelogin {username}` returns `{kdf}`. Unknown usernames get
   stable fake parameters.
2. The browser derives `auth` and `wrap`.
3. `POST /api/v1/admin/login {username, auth_key: b64(auth), totp_code?}` returns
   `{csrf, kdf, wrapped_vault_key, ...}` and sets the cookie.
4. The browser unwraps `V` and keeps it in memory only. After a reload it calls
   `GET /api/v1/admin/session` and asks for the password again to unlock.

Sensitive calls (`password`, `recovery`, `totp/*`, `rotate`) also require
`current_auth_key`. The complete list of routes is at the bottom of
`src/egorovsystem/server/api_admin.py`.

## Old clients

`/get/...`, `/add_password/...` and `/delete_password/...` answer `410` with
`{"errorflag": true, "errormsg": "<upgrade instructions>"}` so v0 clients raise a
readable error.
