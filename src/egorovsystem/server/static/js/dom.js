// DOM helpers. The CSP forbids turning strings into HTML (Trusted Types with
// no policy), so every node is built with createElement and textContent.

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
};

export function icon(name, label) {
  const svg = s("svg", { viewBox: "0 0 24 24", class: "icon", "aria-hidden": label ? "false" : "true", focusable: "false" });
  if (label) svg.setAttribute("aria-label", label);
  for (const d of ICONS[name] || []) svg.append(s("path", { d }));
  return svg;
}

// The wax seal on the bookplate: a scalloped disc with the monogram.
export function seal(size = 84) {
  const points = [];
  const lobes = 18;
  for (let i = 0; i <= lobes * 8; i++) {
    const angle = (i / (lobes * 8)) * Math.PI * 2;
    const r = 40 + 2.2 * Math.cos(angle * lobes) + 0.8 * Math.sin(angle * 7);
    points.push(`${(50 + r * Math.cos(angle)).toFixed(2)},${(50 + r * Math.sin(angle)).toFixed(2)}`);
  }
  return s("svg", { viewBox: "0 0 100 100", width: size, height: size, class: "seal", "aria-hidden": "true" },
    s("polygon", { points: points.join(" "), class: "seal-wax" }),
    s("circle", { cx: 50, cy: 50, r: 31, class: "seal-ring" }),
    s("circle", { cx: 50, cy: 50, r: 27.5, class: "seal-ring thin" }),
    s("text", { x: 50, y: 50, class: "seal-letter", "text-anchor": "middle", "dominant-baseline": "central" }, "E"));
}

// ------------------------------------------------------------ feedback

let toastHost;

export function toast(message, kind = "info") {
  if (!toastHost) {
    toastHost = h("div", { class: "toasts", role: "status", "aria-live": "polite" });
    document.body.append(toastHost);
  }
  const node = h("div", { class: ["toast", `toast-${kind}`] }, message);
  toastHost.append(node);
  setTimeout(() => node.classList.add("leaving"), 3600);
  setTimeout(() => node.remove(), 4000);
}

// A modal built on <dialog>. `build(close)` returns the body; `close(value)`
// resolves the promise. Escape and the backdrop close with undefined.
export function modal({ title, wide = false, build, dismissable = true }) {
  return new Promise((resolve) => {
    const dialog = h("dialog", { class: ["modal", wide && "modal-wide"], "aria-label": title });
    let done = false;
    const close = (value) => {
      if (done) return;
      done = true;
      dialog.close();
      dialog.remove();
      resolve(value);
    };
    const header = h("header", { class: "modal-head" },
      h("h2", null, title),
      dismissable && h("button", { class: "icon-button", type: "button", onclick: () => close(undefined) }, icon("x", "关闭")));
    dialog.append(header, build(close));
    dialog.addEventListener("cancel", (event) => {
      event.preventDefault();
      if (dismissable) close(undefined);
    });
    // No close on backdrop clicks: a stray click must not discard a half-filled form.
    document.body.append(dialog);
    dialog.showModal();
    const first = dialog.querySelector("[autofocus], input, textarea, select, .modal-body button");
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
