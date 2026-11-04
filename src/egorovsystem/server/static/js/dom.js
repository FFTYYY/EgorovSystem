// DOM helpers. The CSP forbids turning strings into HTML (Trusted Types with
// no policy), so every node is built with createElement and textContent.

import { isMobile } from "./layout.js";
import { swipeToDismiss } from "./swipe.js";

const SVG_NS = "http://www.w3.org/2000/svg";

function apply(el, props) {
  for (const [key, value] of Object.entries(props || {})) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") el.setAttribute("class", Array.isArray(value) ? value.filter(Boolean).join(" ") : value);
    else if (key.startsWith("on") && typeof value === "function") el.addEventListener(key.slice(2).toLowerCase(), value);
    else if (key === "value") el.value = value;
    else if (key === "checked") el.checked = Boolean(value);
    else if (key === "dataset") Object.assign(el.dataset, value);
    else el.setAttribute(key, value === true ? "" : String(value));
  }
}

function append(el, children) {
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
}

export function h(tag, props, ...children) {
  const el = document.createElement(tag);
  apply(el, props);
  append(el, children);
  return el;
}

export function s(tag, props, ...children) {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(props || {})) el.setAttribute(key, String(value));
  append(el, children);
  return el;
}

export function clear(el) {
  while (el.firstChild) el.removeChild(el.firstChild);
  return el;
}

// ------------------------------------------------------------ icons

const ICONS = {
  plus: ["M12 5v14", "M5 12h14"],
  copy: ["M9 9h10v10H9z", "M5 15V5h10"],
  eye: ["M2 12s3.6-6 10-6 10 6 10 6-3.6 6-10 6S2 12 2 12z", "M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z"],
  eyeOff: ["M3 3l18 18", "M10.6 6.1A10.6 10.6 0 0 1 12 6c6.4 0 10 6 10 6a17 17 0 0 1-3.2 3.9",
    "M6.6 6.6C3.9 8.3 2 12 2 12s3.6 6 10 6a10 10 0 0 0 4.4-1"],
  edit: ["M4 20h4L19 9l-4-4L4 16z", "M13.5 6.5l4 4"],
  trash: ["M4 7h16", "M9 7V4h6v3", "M6 7l1 13h10l1-13"],
  lock: ["M6 11h12v9H6z", "M8.5 11V8a3.5 3.5 0 0 1 7 0v3"],
  out: ["M14 4h5v16h-5", "M10 8l-4 4 4 4", "M6 12h10"],
  search: ["M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14z", "M20 20l-4-4"],
  check: ["M5 12.5l4.5 4.5L19 7.5"],
  x: ["M6 6l12 12", "M18 6L6 18"],
  play: ["M7 5l12 7-12 7z"],
  menu: ["M4 7h16", "M4 12h16", "M4 17h16"],
  terminal: ["M3 5h18v14H3z", "M7 10l3 2-3 2", "M12 15h5"],
  grip: ["M9 6h.01", "M15 6h.01", "M9 12h.01", "M15 12h.01", "M9 18h.01", "M15 18h.01"],
  dots: ["M5 12h.01", "M12 12h.01", "M19 12h.01"],
  book: ["M12 6.5C10 5 7 4.5 3.5 5v13c3.5-.5 6.5 0 8.5 1.5", "M12 6.5c2-1.5 5-2 8.5-1.5v13c-3.5-.5-6.5 0-8.5 1.5",
    "M12 6.5v13"],
  key: ["M8.5 19.5a4 4 0 1 0 0-8 4 4 0 0 0 0 8z", "M11.3 12.7L19.5 4.5", "M16 8l2.5 2.5", "M13.8 10.2l2 2"],
  laptop: ["M5 6h14v9H5z", "M3 18.5h18"],
  ledger: ["M6 3.5h8.5l3.5 3.5v13.5H6z", "M14 3.5V7.5h4", "M9 12h6", "M9 15.5h6"],
  sliders: ["M4 7h7", "M17 7h3", "M14 9.2a2.2 2.2 0 1 0 0-4.4 2.2 2.2 0 0 0 0 4.4z", "M4 17h3", "M13 17h7",
    "M10 19.2a2.2 2.2 0 1 0 0-4.4 2.2 2.2 0 0 0 0 4.4z"],
  chevron: ["M9.5 6l6 6-6 6"],
  install: ["M12 4v11", "M7.5 10.5L12 15l4.5-4.5", "M5 19.5h14"],
  sort: ["M8 4.5v15", "M4.5 8L8 4.5 11.5 8", "M16 19.5v-15", "M12.5 16l3.5 3.5 3.5-3.5"],
};

export function icon(name, label) {
  const svg = s("svg", { viewBox: "0 0 24 24", class: `icon icon-${name}`, "aria-hidden": label ? "false" : "true", focusable: "false" });
  if (label) svg.setAttribute("aria-label", label);
  for (const d of ICONS[name] || []) svg.append(s("path", { d }));
  return svg;
}

// The app's mark: the keeper's head. The URLs resolve next to this module,
// so they follow the fingerprinted asset directory it was loaded from.
const MASCOT_SMALL = new URL("../icons/mascot-96.webp", import.meta.url).href;
const MASCOT_LARGE = new URL("../icons/mascot-320.webp", import.meta.url).href;

export function mascot(size = 96) {
  return h("img", { class: "mascot", src: size > 48 ? MASCOT_LARGE : MASCOT_SMALL,
    srcset: `${MASCOT_SMALL} 96w, ${MASCOT_LARGE} 320w`, sizes: `${size}px`,
    width: size, height: size, alt: "", decoding: "async", draggable: "false" });
}

// ------------------------------------------------------------ hover tips

// One shared tip, fixed to the viewport so tables that scroll sideways do not
// clip it. Shown on hover and on keyboard focus.
let tipNode = null;

function hideTip() {
  if (tipNode) tipNode.hidden = true;
}

export function hoverTip(trigger, build) {
  const show = () => {
    if (!tipNode) {
      tipNode = h("div", { class: "tooltip", role: "tooltip" });
      document.body.append(tipNode);
      window.addEventListener("scroll", hideTip, { passive: true, capture: true });
      window.addEventListener("hashchange", hideTip);
    }
    clear(tipNode).append(...build());
    tipNode.hidden = false;
    const rect = trigger.getBoundingClientRect();
    const left = Math.max(12, Math.min(rect.left, window.innerWidth - tipNode.offsetWidth - 12));
    const below = rect.bottom + 6 + tipNode.offsetHeight < window.innerHeight;
    tipNode.style.left = `${left}px`;
    tipNode.style.top = `${below ? rect.bottom + 6 : rect.top - tipNode.offsetHeight - 6}px`;
  };
  trigger.addEventListener("pointerenter", show);
  trigger.addEventListener("focus", show);
  trigger.addEventListener("pointerleave", hideTip);
  trigger.addEventListener("blur", hideTip);
}

// ------------------------------------------------------------ feedback

let toastHost;

// The toasts live in a popover so that they show above an open dialog: every
// new toast re-opens it, which puts it on top of the top layer.
function raiseToasts() {
  if (typeof toastHost.showPopover !== "function") return;
  if (toastHost.matches(":popover-open")) toastHost.hidePopover();
  toastHost.showPopover();
}

export function toast(message, kind = "info") {
  if (!toastHost) {
    toastHost = h("div", { class: "toasts", role: "status", "aria-live": "polite", popover: "manual" });
    document.body.append(toastHost);
  }
  const node = h("div", { class: ["toast", `toast-${kind}`] }, message);
  toastHost.append(node);
  raiseToasts();
  setTimeout(() => node.classList.add("leaving"), 3600);
  setTimeout(() => {
    node.remove();
    if (!toastHost.children.length && typeof toastHost.hidePopover === "function" && toastHost.matches(":popover-open")) {
      toastHost.hidePopover();
    }
  }, 4000);
}

// Every open modal's close function, so that locking the vault can take
// decrypted values off the screen.
const openModals = new Set();

export function closeModals() {
  for (const close of Array.from(openModals)) close(undefined, false);
}

// Let a closing dialog play its leaving animation (phones slide sheets down),
// then remove it.
function removeDialog(dialog, animate) {
  let gone = false;
  const finish = () => {
    if (gone) return;
    gone = true;
    if (dialog.open) dialog.close();
    dialog.remove();
  };
  if (!animate) return finish();
  dialog.classList.add("is-leaving");
  const name = getComputedStyle(dialog).animationName;
  if (!name || name === "none") return finish();
  dialog.addEventListener("animationend", (event) => { if (event.target === dialog) finish(); });
  setTimeout(finish, 400);
}

function outside(event, el) {
  const r = el.getBoundingClientRect();
  return event.clientX < r.left || event.clientX > r.right || event.clientY < r.top || event.clientY > r.bottom;
}

// A modal built on <dialog>. `build(close)` returns the body; `close(value)`
// resolves the promise. Escape and the close button resolve with undefined.
// `heading` replaces the title text in the header (the title stays the label).
// A `sheet` only shows things, so a tap on the backdrop or a swipe down on its
// header also closes it. Forms never close that way: a stray tap must not
// discard a half-filled form.
export function modal({ title, heading, wide = false, sheet = false, build, dismissable = true }) {
  return new Promise((resolve) => {
    const dialog = h("dialog", { class: ["modal", wide && "modal-wide", sheet && "modal-sheet"], "aria-label": title });
    let done = false;
    const close = (value, animate = true) => {
      if (done) return;
      done = true;
      openModals.delete(close);
      removeDialog(dialog, animate);
      resolve(value);
    };
    const header = h("header", { class: "modal-head" },
      h("h2", null, heading || title),
      dismissable && h("button", { class: "icon-button", type: "button", onclick: () => close(undefined) }, icon("x", "关闭")));
    dialog.append(header, build(close));
    dialog.addEventListener("cancel", (event) => {
      event.preventDefault();
      if (dismissable) close(undefined);
    });
    if (sheet && dismissable) {
      dialog.addEventListener("click", (event) => {
        if (event.target === dialog && outside(event, dialog)) close(undefined);
      });
      swipeToDismiss(dialog, header, () => close(undefined, false));
    }
    openModals.add(close);
    document.body.append(dialog);
    dialog.showModal();
    // On phones, focusing a text field would pop up the keyboard before the
    // user asked for it, so only an explicit autofocus gets focus there.
    const first = dialog.querySelector("[autofocus]")
      || (isMobile() ? null : dialog.querySelector("input, textarea, select, .modal-body button"));
    if (first) first.focus();
  });
}

export function confirmAction({ title, message, confirmLabel, danger = false }) {
  return modal({
    title,
    build: (close) => h("div", { class: "modal-body" },
      Array.isArray(message) ? message.map((m) => h("p", null, m)) : h("p", null, message),
      h("div", { class: "modal-actions" },
        h("button", { class: "button", type: "button", onclick: () => close(false) }, "取消"),
        h("button", { class: ["button", danger ? "button-danger" : "button-primary"], type: "button", autofocus: true,
          onclick: () => close(true) }, confirmLabel))),
  }).then(Boolean);
}

export async function copyText(text, what = "内容") {
  try {
    await navigator.clipboard.writeText(text);
    toast(`已复制${what}`);
  } catch {
    toast("浏览器不允许写入剪贴板，请手动选择并复制。", "error");
  }
}

// ------------------------------------------------------------ forms

export function field(label, control, hint) {
  const id = control.id || `f-${Math.random().toString(36).slice(2, 9)}`;
  control.id = id;
  const hintNode = hint ? h("p", { class: "field-hint", id: `${id}-hint` }, hint) : null;
  if (hintNode) control.setAttribute("aria-describedby", hintNode.id);
  return h("div", { class: "field" }, h("label", { for: id }, label), control, hintNode);
}

export function formError() {
  const node = h("p", { class: "form-error", role: "alert" });
  node.hidden = true;
  return {
    node,
    show(message) { node.textContent = message; node.hidden = false; },
    hide() { node.hidden = true; },
  };
}

// Disable a button and swap its label while an async task runs.
export async function busy(button, label, task) {
  const original = Array.from(button.childNodes);
  button.disabled = true;
  button.classList.add("is-busy");
  clear(button).append(label);
  try {
    return await task();
  } finally {
    button.disabled = false;
    button.classList.remove("is-busy");
    clear(button).append(...original);
  }
}

// ------------------------------------------------------------ time

const pad = (n) => String(n).padStart(2, "0");

export function formatTime(ts) {
  if (!ts) return "—";
  const d = new Date(ts * 1000);
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function formatClock(ts) {
  if (!ts) return "—";
  const d = new Date(ts * 1000);
  return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function formatDate(ts) {
  if (!ts) return "—";
  const d = new Date(ts * 1000);
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

export function ago(ts) {
  if (!ts) return "从未";
  const seconds = Math.max(0, Date.now() / 1000 - ts);
  if (seconds < 60) return "刚刚";
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟前`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} 小时前`;
  if (seconds < 86400 * 30) return `${Math.floor(seconds / 86400)} 天前`;
  return formatDate(ts);
}

export function timeTag(ts) {
  return h("time", { datetime: ts ? new Date(ts * 1000).toISOString() : "", title: formatTime(ts) }, ago(ts));
}
