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

// Items stay in the order the server keeps: by kind, then by position
// (newest first unless the admin dragged them around).
function sortItems() {
  vault.items.sort((a, b) => (a.kind === b.kind ? (a.position ?? 0) - (b.position ?? 0) : a.kind.localeCompare(b.kind)));
}

export async function saveItem(kind, originalName, name, payload) {
  const blob = await c.encryptItem(session.vaultKey, kind, name, payload);
  // Functions tell the server which variable names they use, so that granting a
  // function to a device also grants those variables. Values stay encrypted.
  const extra = kind === "function" ? { refs: references(payload.content) } : {};
  const row = originalName
    ? await api("PUT", `/items/${kind}/${encodeURIComponent(originalName)}`, { blob, new_name: name, ...extra })
    : await api("POST", "/items", { kind, name, blob, ...extra });
  const decrypted = { ...row, payload, error: null };
  const index = vault.items.findIndex((item) => item.kind === kind && item.name === (originalName || name));
  if (index >= 0) vault.items[index] = decrypted;
  else vault.items.push(decrypted);
  sortItems();
  return decrypted;
}

// Save a new order for one kind. Applied locally at once; reloaded on failure.
export async function reorder(kind, names) {
  const byName = new Map(itemsOf(kind).map((item) => [item.name, item]));
  names.forEach((name, index) => { byName.get(name).position = index + 1; });
  sortItems();
  try {
    await api("POST", "/items/order", { kind, names });
  } catch (error) {
    await loadItems();
    throw error;
  }
}

const sameList = (a, b) => Array.isArray(a) && a.length === b.length && a.every((x, i) => x === b[i]);

// Functions saved before the server tracked references have none recorded.
// Fill them in quietly after every sign-in; it costs one request when needed.
export async function syncRefs() {
  const stale = itemsOf("function").filter((fn) => fn.payload && !sameList(fn.refs, references(fn.payload.content)));
  if (!stale.length) return 0;
  await api("POST", "/items/refs", { items: stale.map((fn) => ({ name: fn.name, refs: references(fn.payload.content) })) });
  for (const fn of stale) fn.refs = references(fn.payload.content);
  return stale.length;
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

// Variables a function uses, from its decrypted content when available.
export function functionRefs(fn) {
  return fn.payload ? references(fn.payload.content) : fn.refs || [];
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
