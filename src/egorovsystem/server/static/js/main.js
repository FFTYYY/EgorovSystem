import { api, setSignedOutHandler } from "./api.js";
import { clear, h, icon, seal, toast } from "./dom.js";
import { applySession, isUnlocked, itemsOf, loadItems, lock, session, syncRefs, watchIdle } from "./state.js";
import { renderFatal, renderLogin, renderUninitialized, renderUnlock } from "./views/login.js";
import { renderOverview } from "./views/overview.js";
import { renderVariables } from "./views/variables.js";
import { renderFunctions } from "./views/functions.js";
import { renderDevices } from "./views/devices.js";
import { renderAudit } from "./views/audit.js";
import { renderSettings } from "./views/settings.js";

const root = document.getElementById("app");

const ROUTES = [
  { path: "overview", label: "总览", render: renderOverview },
  { path: "variables", label: "变量", render: renderVariables, count: () => itemsOf("variable").length },
  { path: "functions", label: "函数", render: renderFunctions, count: () => itemsOf("function").length },
  { path: "devices", label: "设备", render: renderDevices, count: () => deviceCount },
  { path: "audit", label: "记录", render: renderAudit },
  { path: "settings", label: "设置", render: renderSettings },
];

let deviceCount = null;
let page = null;
let navLinks = new Map();
let renderToken = 0;

function currentRoute() {
  const [path, query = ""] = location.hash.replace(/^#\/?/, "").split("?");
  const route = ROUTES.find((r) => r.path === path) || ROUTES[0];
  return { route, params: new URLSearchParams(query) };
}

function refreshNav() {
  for (const route of ROUTES) {
    const link = navLinks.get(route.path);
    if (!link) continue;
    const count = link.querySelector(".toc-count");
    if (count && route.count) count.textContent = route.count() ?? "";
  }
}

const ctx = {
  refreshNav,
  setDeviceCount(n) { deviceCount = n; refreshNav(); },
  go(path) { location.hash = `#/${path}`; },
};

function renderShell() {
  navLinks = new Map();
  const nav = h("nav", { class: "toc", "aria-label": "目录" },
    ROUTES.map((route) => {
      const link = h("a", { class: "toc-item", href: `#/${route.path}` },
        h("span", { class: "toc-label" }, route.label),
        route.count && h("span", { class: "toc-leader", "aria-hidden": "true" }),
        route.count && h("span", { class: "toc-count" }, ""));
      navLinks.set(route.path, link);
      return link;
    }));
  page = h("main", { class: "page", id: "page", tabindex: "-1" });
  const shell = h("div", { class: "shell" },
    h("aside", { class: "sidebar" },
      h("a", { class: "brand", href: "#/overview" }, seal(34), h("span", { class: "brand-name" }, "Egorov")),
      nav,
      h("div", { class: "sidebar-foot" },
        h("p", { class: "who" }, h("span", { class: "who-label" }, "管理员"), h("span", { class: "who-name" }, session.username)),
        h("div", { class: "sidebar-actions" },
          h("button", { class: "button button-quiet", type: "button", onclick: () => lockNow() }, icon("lock"), "锁定"),
          h("button", { class: "button button-quiet", type: "button", onclick: () => signOut() }, icon("out"), "退出")))),
    page);
  clear(root).append(shell);
  refreshNav();
}

async function route() {
  if (!isUnlocked() || !page) return;
  const { route: current, params } = currentRoute();
  for (const [path, link] of navLinks) {
    if (path === current.path) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  }
  document.title = `${current.label} · Egorov`;
  const token = ++renderToken;
  clear(page);
  try {
    await current.render(page, { ...ctx, params, isCurrent: () => token === renderToken });
  } catch (error) {
    if (token !== renderToken) return;
    clear(page).append(h("div", { class: "page-error" }, h("p", null, error.message),
      h("button", { class: "button", type: "button", onclick: () => route() }, "重试")));
  }
  page.focus({ preventScroll: true });
}

async function enterApp() {
  await loadItems();
  // Record which variables each function uses (functions saved before this
  // existed have none), so function grants also cover those variables.
  syncRefs().catch(() => {});
  renderShell();
  watchIdle(() => {
    lock();
    showUnlock("闲置超过 15 分钟，保险库已自动锁定。");
  });
  api("GET", "/devices").then(({ devices }) => ctx.setDeviceCount(devices.length)).catch(() => {});
  await route();
}

function showLogin(message) {
  page = null;
  renderLogin(root, { message, onDone: enterApp });
}

function showUnlock(message) {
  page = null;
  renderUnlock(root, { message, onDone: enterApp, onSignOut: () => signOut() });
}

function lockNow() {
  lock();
  showUnlock();
}

async function signOut() {
  try {
    await api("POST", "/logout");
  } catch {
    // The session may already be gone; signing out locally is what matters.
  }
  lock();
  showLogin();
  toast("已退出登录。");
}

async function boot() {
  setSignedOutHandler(() => {
    if (!session.username) return;
    lock();
    session.username = null;
    showLogin("登录已过期，请重新登录。");
  });
  window.addEventListener("hashchange", () => route());
  let status;
  try {
    status = await api("GET", "/status");
  } catch (error) {
    renderFatal(root, error.message);
    return;
  }
  if (!status.initialized) {
    renderUninitialized(root);
    return;
  }
  if (status.authenticated) {
    try {
      applySession(await api("GET", "/session"));
      showUnlock();
      return;
    } catch {
      // fall through to a fresh sign-in
    }
  }
  showLogin();
}

boot();
