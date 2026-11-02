// What the admin UI holds in memory. The vault key lives only here and is
// wiped on lock, sign-out and after a period of inactivity.

import * as c from "./crypto.js";
import { api, ApiError, setCsrf } from "./api.js";

export const NAME_RE = /^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$/;
export const REF_RE = /\$\{([A-Za-z0-9_][A-Za-z0-9_.-]{0,127})\}/g;

export const session = {
  username: null,
  kdf: null,
  wrapped: null,
  totpEnabled: false,
  expiresAt: null,
  vaultKey: null,
};

export const vault = { items: [], loaded: false };

export function applySession(data) {
  session.username = data.username;
  session.kdf = data.kdf;
  session.wrapped = data.wrapped_vault_key;
  session.totpEnabled = Boolean(data.totp_enabled);
  session.expiresAt = data.expires_at;
  setCsrf(data.csrf);
}

export function isUnlocked() {
  return session.vaultKey !== null;
}

// Derive keys and open the vault. Throws ApiError("wrong_password") locally,
// without a server round trip, when the password does not unwrap the key.
export async function unlock(password) {
  const keys = await c.deriveAdminKeys(password, session.kdf);
  try {
    session.vaultKey = await c.unwrapVaultKey(keys.wrap, session.wrapped);
  } catch {
    throw new ApiError(403, "wrong_password", null);
  } finally {
    keys.wrap.fill(0);
  }
  return keys.auth;
}

export function lock() {
  if (session.vaultKey) session.vaultKey.fill(0);
  session.vaultKey = null;
  vault.items = [];
  vault.loaded = false;
}

// Re-derive the auth key for a sensitive change, checking it locally first.
export async function proveCurrentPassword(password) {
  const keys = await c.deriveAdminKeys(password, session.kdf);
  try {
    await c.unwrapVaultKey(keys.wrap, session.wrapped);
  } catch {
    throw new ApiError(403, "wrong_password", null);
  } finally {
    keys.wrap.fill(0);
  }
  return c.b64e(keys.auth);
}

async function decryptRow(row) {
  try {
    return { ...row, payload: await c.decryptItem(session.vaultKey, row.kind, row.name, row.blob), error: null };
  } catch (error) {
    return { ...row, payload: null, error: error.message };
  }
}

export async function loadItems() {
  const { items } = await api("GET", "/items");
  vault.items = await Promise.all(items.map(decryptRow));
  vault.loaded = true;
  return vault.items;
}

export function itemsOf(kind) {
  return vault.items.filter((item) => item.kind === kind);
}

export function findItem(kind, name) {
  return vault.items.find((item) => item.kind === kind && item.name === name);
}

export async function saveItem(kind, originalName, name, payload) {
  const blob = await c.encryptItem(session.vaultKey, kind, name, payload);
  const row = originalName
    ? await api("PUT", `/items/${kind}/${encodeURIComponent(originalName)}`, { blob, new_name: name })
    : await api("POST", "/items", { kind, name, blob });
  const decrypted = { ...row, payload, error: null };
  vault.items = vault.items.filter((item) => !(item.kind === kind && item.name === (originalName || name)));
  vault.items.push(decrypted);
  vault.items.sort((a, b) => (a.kind + a.name).localeCompare(b.kind + b.name));
  return decrypted;
}

export async function deleteItem(kind, name) {
  await api("DELETE", `/items/${kind}/${encodeURIComponent(name)}`);
  vault.items = vault.items.filter((item) => !(item.kind === kind && item.name === name));
}

export function references(content) {
  const names = [];
  for (const match of (content || "").matchAll(REF_RE)) {
    if (!names.includes(match[1])) names.push(match[1]);
  }
  return names;
}

export function functionsUsing(variableName) {
  return itemsOf("function").filter((fn) => fn.payload && references(fn.payload.content).includes(variableName));
}

// ------------------------------------------------------------ idle lock

const IDLE_LIMIT_MS = 15 * 60 * 1000;
let idleTimer = null;
let onIdle = () => {};

function resetIdle() {
  if (!isUnlocked()) return;
  clearTimeout(idleTimer);
  idleTimer = setTimeout(() => onIdle(), IDLE_LIMIT_MS);
}

export function watchIdle(handler) {
  onIdle = handler;
  for (const event of ["pointerdown", "keydown", "wheel", "touchstart"]) {
    window.addEventListener(event, resetIdle, { passive: true });
  }
  resetIdle();
}

export function touchIdle() {
  resetIdle();
}
