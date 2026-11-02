"""Cryptographic building blocks shared by every part of Egorov.

The same scheme is implemented in JavaScript for the admin UI
(``server/static/js/crypto.js``). docs/PROTOCOL.md is the specification both
implementations follow; tests check that they agree byte for byte.

Key hierarchy
-------------
* vault key: 32 random bytes. Encrypts every variable and function.
* admin master key: PBKDF2-SHA256(password, salt, 600k). Split with HKDF into
  an auth key (sent to the server, which stores only a scrypt hash of it) and
  a wrap key (never leaves the browser) that encrypts the vault key.
* device token: 32 random bytes per device, split the same way. The server
  sees only the auth half.
* recovery key: 32 random bytes shown once at init. Its HKDF-derived wrap key
  also encrypts the vault key, so a forgotten password can be reset.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import re
import unicodedata
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

BLOB_VERSION = "v1"
PBKDF2_ALG = "pbkdf2-sha256"
PBKDF2_ITERATIONS = 600_000
PBKDF2_MIN_ITERATIONS = 600_000
PBKDF2_MAX_ITERATIONS = 10_000_000
SALT_BYTES = 16
KEY_BYTES = 32
NONCE_BYTES = 12

HKDF_SALT = bytes(32)
INFO_ADMIN_AUTH = b"egorov/v1/admin-auth"
INFO_ADMIN_WRAP = b"egorov/v1/admin-wrap"
INFO_DEVICE_AUTH = b"egorov/v1/device-auth"
INFO_DEVICE_WRAP = b"egorov/v1/device-wrap"
INFO_RECOVERY_WRAP = b"egorov/v1/recovery-wrap"
AAD_VAULT_KEY = b"egorov/v1/vault-key"

TOKEN_PREFIX = "ego1"
DEVICE_ID_RE = re.compile(r"^[0-9a-f]{12}$")
ITEM_KINDS = ("variable", "function")


class CryptoError(Exception):
    """Raised when data cannot be decoded, authenticated or decrypted."""


# ---------------------------------------------------------------- encoding

_B64URL_RE = re.compile(r"^[A-Za-z0-9_-]*$")


def b64e(data: bytes) -> str:
    """Unpadded base64url, the only binary encoding used on the wire."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64d(text: str) -> bytes:
    if not isinstance(text, str) or not _B64URL_RE.match(text) or len(text) % 4 == 1:
        raise CryptoError("invalid base64url data")
    try:
        return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except (binascii.Error, ValueError) as exc:  # pragma: no cover - guarded by regex
        raise CryptoError("invalid base64url data") from exc


# ---------------------------------------------------------------- derivation

def hkdf_sha256(ikm: bytes, info: bytes, length: int = KEY_BYTES, salt: bytes = HKDF_SALT) -> bytes:
    """RFC 5869 HKDF with SHA-256."""
    prk = hmac.new(salt, ikm, hashlib.sha256).digest()
    okm = b""
    block = b""
    counter = 1
    while len(okm) < length:
        block = hmac.new(prk, block + info + bytes([counter]), hashlib.sha256).digest()
        okm += block
        counter += 1
    return okm[:length]


def normalize_password(password: str) -> bytes:
    return unicodedata.normalize("NFC", password).encode("utf-8")


def new_kdf_params(iterations: int = PBKDF2_ITERATIONS) -> dict:
    return {"alg": PBKDF2_ALG, "iterations": iterations, "salt": b64e(os.urandom(SALT_BYTES))}


def validate_kdf_params(kdf: object, *, min_iterations: int = PBKDF2_MIN_ITERATIONS) -> dict:
    """Check KDF parameters that came from outside (a client or the database)."""
    if not isinstance(kdf, dict) or kdf.get("alg") != PBKDF2_ALG:
        raise CryptoError("unsupported key derivation parameters")
    iterations = kdf.get("iterations")
    if not isinstance(iterations, int) or isinstance(iterations, bool):
        raise CryptoError("iterations must be an integer")
    if not min_iterations <= iterations <= PBKDF2_MAX_ITERATIONS:
        raise CryptoError("iterations out of range")
    salt = b64d(kdf.get("salt", ""))
    if len(salt) != SALT_BYTES:
        raise CryptoError("salt must be 16 bytes")
    return {"alg": PBKDF2_ALG, "iterations": iterations, "salt": kdf["salt"]}


@dataclass(frozen=True)
class AdminKeys:
    auth_key: bytes
    wrap_key: bytes


def derive_admin_keys(password: str, kdf: dict) -> AdminKeys:
    salt = b64d(kdf["salt"])
    master = hashlib.pbkdf2_hmac("sha256", normalize_password(password), salt, int(kdf["iterations"]), KEY_BYTES)
    return AdminKeys(
        auth_key=hkdf_sha256(master, INFO_ADMIN_AUTH),
        wrap_key=hkdf_sha256(master, INFO_ADMIN_WRAP),
    )


# ---------------------------------------------------------------- AEAD blobs

def seal(key: bytes, plaintext: bytes, aad: bytes) -> str:
    """AES-256-GCM encrypt to ``v1.<nonce>.<ciphertext+tag>``."""
    nonce = os.urandom(NONCE_BYTES)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, aad)
    return f"{BLOB_VERSION}.{b64e(nonce)}.{b64e(ciphertext)}"


def unseal(key: bytes, blob: str, aad: bytes) -> bytes:
    if not isinstance(blob, str):
        raise CryptoError("blob must be a string")
    parts = blob.split(".")
    if len(parts) != 3 or parts[0] != BLOB_VERSION:
        raise CryptoError("unsupported blob format")
    nonce, ciphertext = b64d(parts[1]), b64d(parts[2])
    if len(nonce) != NONCE_BYTES or len(ciphertext) < 16:
        raise CryptoError("malformed blob")
    try:
        return AESGCM(key).decrypt(nonce, ciphertext, aad)
    except InvalidTag as exc:
        raise CryptoError("decryption failed: wrong key or tampered data") from exc


def is_blob(value: object, max_length: int = 1_500_000) -> bool:
    """Cheap structural check the server uses before storing a blob."""
    if not isinstance(value, str) or len(value) > max_length:
        return False
    parts = value.split(".")
    if len(parts) != 3 or parts[0] != BLOB_VERSION:
        return False
    try:
        return len(b64d(parts[1])) == NONCE_BYTES and len(b64d(parts[2])) >= 16
    except CryptoError:
        return False


def new_vault_key() -> bytes:
    return os.urandom(KEY_BYTES)


def wrap_vault_key(wrap_key: bytes, vault_key: bytes) -> str:
    return seal(wrap_key, vault_key, AAD_VAULT_KEY)


def unwrap_vault_key(wrap_key: bytes, blob: str) -> bytes:
    vault_key = unseal(wrap_key, blob, AAD_VAULT_KEY)
    if len(vault_key) != KEY_BYTES:
        raise CryptoError("vault key has the wrong length")
    return vault_key


def item_aad(kind: str, name: str) -> bytes:
    """Binds a ciphertext to its kind and name so the server cannot swap items."""
    return f"egorov/v1/item/{kind}/{name}".encode("utf-8")


def encrypt_item(vault_key: bytes, kind: str, name: str, payload: dict) -> str:
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return seal(vault_key, data, item_aad(kind, name))


def decrypt_item(vault_key: bytes, kind: str, name: str, blob: str) -> dict:
    data = unseal(vault_key, blob, item_aad(kind, name))
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CryptoError("decrypted item is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise CryptoError("decrypted item is not an object")
    return payload


# ---------------------------------------------------------------- devices

@dataclass(frozen=True)
class DeviceToken:
    """The secret a device keeps in EGOROV_PWD: ``ego1.<id>.<secret>``."""

    device_id: str
    secret: bytes

    def __str__(self) -> str:
        return f"{TOKEN_PREFIX}.{self.device_id}.{b64e(self.secret)}"

    def __repr__(self) -> str:  # never print the secret by accident
        return f"DeviceToken(device_id={self.device_id!r}, secret=<hidden>)"

    @property
    def auth_key(self) -> bytes:
        return hkdf_sha256(self.secret, INFO_DEVICE_AUTH)

    @property
    def wrap_key(self) -> bytes:
        return hkdf_sha256(self.secret, INFO_DEVICE_WRAP)

    def authorization(self) -> str:
        return f"Bearer {self.device_id}.{b64e(self.auth_key)}"


def new_device_id() -> str:
    return os.urandom(6).hex()


def new_device_token(device_id: str | None = None) -> DeviceToken:
    return DeviceToken(device_id or new_device_id(), os.urandom(KEY_BYTES))


def parse_device_token(text: str) -> DeviceToken:
    if not isinstance(text, str):
        raise CryptoError("device token must be a string")
    parts = text.strip().split(".")
    if len(parts) != 3 or parts[0] != TOKEN_PREFIX:
        raise CryptoError(
            "EGOROV_PWD is not an Egorov v1 device token (expected ego1.<id>.<secret>). "
            "Create one under Devices in the admin UI."
        )
    if not DEVICE_ID_RE.match(parts[1]):
        raise CryptoError("device token has an invalid id")
    secret = b64d(parts[2])
    if len(secret) != KEY_BYTES:
        raise CryptoError("device token secret has the wrong length")
    return DeviceToken(parts[1], secret)


def auth_hash(auth_key: bytes) -> str:
    """What the server stores for a device. The auth key has 256 bits of
    entropy, so a fast hash is enough here."""
    return hashlib.sha256(auth_key).hexdigest()


def provision_device(vault_key: bytes, device_id: str | None = None) -> tuple[DeviceToken, str, str]:
    """Create a device token plus the two values the server must store."""
    token = new_device_token(device_id)
    return token, auth_hash(token.auth_key), wrap_vault_key(token.wrap_key, vault_key)


# ---------------------------------------------------------------- recovery

def new_recovery_key() -> bytes:
    return os.urandom(KEY_BYTES)


def format_recovery_key(raw: bytes) -> str:
    text = base64.b32encode(raw).decode("ascii").rstrip("=")
    return "-".join(text[i:i + 4] for i in range(0, len(text), 4))


def parse_recovery_key(text: str) -> bytes:
    cleaned = re.sub(r"[\s-]", "", text or "").upper()
    try:
        raw = base64.b32decode(cleaned + "=" * (-len(cleaned) % 8))
    except (binascii.Error, ValueError) as exc:
        raise CryptoError("recovery key is not valid") from exc
    if len(raw) != KEY_BYTES:
        raise CryptoError("recovery key has the wrong length")
    return raw


def recovery_wrap_key(raw: bytes) -> bytes:
    return hkdf_sha256(raw, INFO_RECOVERY_WRAP)
