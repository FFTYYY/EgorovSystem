// Browser side of the Egorov v1 protocol (docs/PROTOCOL.md).
// Mirrors src/egorovsystem/crypto.py; tests/test_crypto_interop.py checks
// that both produce and accept the same data.

const enc = new TextEncoder();
const dec = new TextDecoder("utf-8", { fatal: true });
const subtle = globalThis.crypto.subtle;

export const PBKDF2_ITERATIONS = 600000;
const HKDF_SALT = new Uint8Array(32);
const INFO = {
  adminAuth: "egorov/v1/admin-auth",
  adminWrap: "egorov/v1/admin-wrap",
  deviceAuth: "egorov/v1/device-auth",
  deviceWrap: "egorov/v1/device-wrap",
  recoveryWrap: "egorov/v1/recovery-wrap",
};
const AAD_VAULT_KEY = "egorov/v1/vault-key";

export class CryptoError extends Error {}

// ------------------------------------------------------------ encoding

export function b64e(bytes) {
  let binary = "";
  for (const b of bytes) binary += String.fromCharCode(b);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export function b64d(text) {
  if (typeof text !== "string" || !/^[A-Za-z0-9_-]*$/.test(text) || text.length % 4 === 1) {
    throw new CryptoError("invalid base64url data");
  }
  const padded = text.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - (text.length % 4)) % 4);
  const binary = atob(padded);
  return Uint8Array.from(binary, (c) => c.charCodeAt(0));
}

export function randomBytes(n) {
  return globalThis.crypto.getRandomValues(new Uint8Array(n));
}

function hex(bytes) {
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

const BASE32 = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";

function base32(bytes) {
  let bits = 0, value = 0, out = "";
  for (const byte of bytes) {
    value = (value << 8) | byte;
    bits += 8;
    while (bits >= 5) {
      out += BASE32[(value >>> (bits - 5)) & 31];
      bits -= 5;
    }
  }
  if (bits > 0) out += BASE32[(value << (5 - bits)) & 31];
  return out;
}

// ------------------------------------------------------------ derivation

export async function hkdf(ikm, info) {
  const key = await subtle.importKey("raw", ikm, "HKDF", false, ["deriveBits"]);
  const bits = await subtle.deriveBits(
    { name: "HKDF", hash: "SHA-256", salt: HKDF_SALT, info: enc.encode(info) }, key, 256);
  return new Uint8Array(bits);
}

export function newKdfParams() {
  return { alg: "pbkdf2-sha256", iterations: PBKDF2_ITERATIONS, salt: b64e(randomBytes(16)) };
}

export async function deriveAdminKeys(password, kdf) {
  if (!kdf || kdf.alg !== "pbkdf2-sha256" || !Number.isInteger(kdf.iterations)) {
    throw new CryptoError("unsupported key derivation parameters");
  }
  const base = await subtle.importKey("raw", enc.encode(password.normalize("NFC")), "PBKDF2", false, ["deriveBits"]);
  const master = new Uint8Array(await subtle.deriveBits(
    { name: "PBKDF2", hash: "SHA-256", salt: b64d(kdf.salt), iterations: kdf.iterations }, base, 256));
  const keys = { auth: await hkdf(master, INFO.adminAuth), wrap: await hkdf(master, INFO.adminWrap) };
  master.fill(0);
  return keys;
}

export async function sha256Hex(bytes) {
  return hex(new Uint8Array(await subtle.digest("SHA-256", bytes)));
}

// ------------------------------------------------------------ AEAD blobs

async function aesKey(raw) {
  return subtle.importKey("raw", raw, "AES-GCM", false, ["encrypt", "decrypt"]);
}

export async function seal(keyRaw, plaintext, aad) {
  const nonce = randomBytes(12);
  const ciphertext = await subtle.encrypt(
    { name: "AES-GCM", iv: nonce, additionalData: enc.encode(aad), tagLength: 128 }, await aesKey(keyRaw), plaintext);
  return `v1.${b64e(nonce)}.${b64e(new Uint8Array(ciphertext))}`;
}

export async function unseal(keyRaw, blob, aad) {
  const parts = typeof blob === "string" ? blob.split(".") : [];
  if (parts.length !== 3 || parts[0] !== "v1") throw new CryptoError("unsupported blob format");
  const nonce = b64d(parts[1]);
  const ciphertext = b64d(parts[2]);
  if (nonce.length !== 12 || ciphertext.length < 16) throw new CryptoError("malformed blob");
  try {
    const plain = await subtle.decrypt(
      { name: "AES-GCM", iv: nonce, additionalData: enc.encode(aad), tagLength: 128 }, await aesKey(keyRaw), ciphertext);
    return new Uint8Array(plain);
  } catch {
    throw new CryptoError("decryption failed: wrong key or tampered data");
  }
}

export function newVaultKey() {
  return randomBytes(32);
}

export function wrapVaultKey(wrapKey, vaultKey) {
  return seal(wrapKey, vaultKey, AAD_VAULT_KEY);
}

export async function unwrapVaultKey(wrapKey, blob) {
  const key = await unseal(wrapKey, blob, AAD_VAULT_KEY);
  if (key.length !== 32) throw new CryptoError("vault key has the wrong length");
  return key;
}

export function itemAad(kind, name) {
  return `egorov/v1/item/${kind}/${name}`;
}

export function encryptItem(vaultKey, kind, name, payload) {
  return seal(vaultKey, enc.encode(JSON.stringify(payload)), itemAad(kind, name));
}

export async function decryptItem(vaultKey, kind, name, blob) {
  const plain = await unseal(vaultKey, blob, itemAad(kind, name));
  let payload;
  try {
    payload = JSON.parse(dec.decode(plain));
  } catch {
    throw new CryptoError("decrypted item is not valid JSON");
  }
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    throw new CryptoError("decrypted item is not an object");
  }
  return payload;
}

// ------------------------------------------------------------ devices and recovery

export async function provisionDevice(vaultKey) {
  const id = hex(randomBytes(6));
  const secret = randomBytes(32);
  const auth = await hkdf(secret, INFO.deviceAuth);
  const wrap = await hkdf(secret, INFO.deviceWrap);
  const result = {
    id,
    token: `ego1.${id}.${b64e(secret)}`,
    authHash: await sha256Hex(auth),
    wrappedVaultKey: await wrapVaultKey(wrap, vaultKey),
  };
  secret.fill(0);
  wrap.fill(0);
  return result;
}

export function formatRecoveryKey(raw) {
  return base32(raw).match(/.{1,4}/g).join("-");
}

export async function newRecovery(vaultKey) {
  const raw = randomBytes(32);
  const wrapped = await wrapVaultKey(await hkdf(raw, INFO.recoveryWrap), vaultKey);
  const text = formatRecoveryKey(raw);
  raw.fill(0);
  return { text, wrapped };
}

// Only used by the interop tests: derive what Python derives.
export async function deviceKeysFromToken(token) {
  const [, id, secretText] = token.split(".");
  const secret = b64d(secretText);
  return { id, auth: await hkdf(secret, INFO.deviceAuth), wrap: await hkdf(secret, INFO.deviceWrap) };
}

export async function recoveryWrapKey(text) {
  const cleaned = text.replace(/[\s-]/g, "").toUpperCase();
  let bits = 0, value = 0;
  const out = [];
  for (const ch of cleaned) {
    const index = BASE32.indexOf(ch);
    if (index < 0) throw new CryptoError("invalid recovery key");
    value = (value << 5) | index;
    bits += 5;
    if (bits >= 8) {
      out.push((value >>> (bits - 8)) & 255);
      bits -= 8;
    }
  }
  return hkdf(new Uint8Array(out), INFO.recoveryWrap);
}
