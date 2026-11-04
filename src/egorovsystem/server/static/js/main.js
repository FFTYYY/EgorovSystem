import { api, setSignedOutHandler } from "./api.js";
import { clear, closeModals, h, toast } from "./dom.js";
import { isMobile, onLayoutChange } from "./layout.js";
import { initPwa } from "./pwa.js";
import { updateIfWaiting, watchForUpdates } from "./update.js";
import { buildShell } from "./shell.js";
import { applySession, isUnlocked, itemsOf, loadItems, lock, session, syncRefs, watchIdle } from "./state.js";
import { renderFatal, renderLogin, renderUninitialized, renderUnlock } from "./views/login.js";
import { renderOverview } from "./views/overview.js";
import { renderVariables } from "./views/variables.js";
import { renderFunctions } from "./views/functions.js";
import { renderDevices } from "./views/devices.js";
import { renderAudit } from "./views/audit.js";
import { renderSettings } from "./views/settings.js";

const root = document.getElementById("app");

// `tab`: has its own tab in the phone layout; the rest sit under "更多".
const ROUTES = [
  { path: "overview", label: "总览", icon: "book", tab: true, render: renderOverview },
  { path: "variables", label: "变量", icon: "key", tab: true, render: renderVariables, count: () => itemsOf("variable").length },
  { path: "functions", label: "函数", icon: "terminal", tab: true, render: renderFunctions, count: () => itemsOf("function").length },
  { path: "devices", label: "设备", icon: "laptop", tab: true, render: renderDevices, count: () => deviceCount },
  { path: "audit", label: "记录", icon: "ledger", render: renderAudit },
  { path: "settings", label: "设置", icon: "sliders", render: renderSettings },
];

let deviceCount = null;
let shell = null;
let page = null;
let shownPath = null;
let renderToken = 0;

function currentRoute() {
  const [path, query = ""] = location.hash.replace(/^#\/?/, "").split("?");
  const route = ROUTES.find((r) => r.path === path) || ROUTES[0];
  return { route, params: new URLSearchParams(query) };
}

function refreshNav() {
  if (shell) shell.refresh();
}

const ctx = {
  refreshNav,
  setDeviceCount(n) { deviceCount = n; refreshNav(); },
  go(path) { location.hash = `#/${path}`; },
};

function renderShell() {
  shell = buildShell(root, { routes: ROUTES, mobile: isMobile(), onLock: () => lockNow(), onSignOut: () => signOut() });
  page = shell.page;
}

async function route() {
  if (!isUnlocked() || !page) return;
  const { route: current, params } = currentRoute();
  shell.setCurrent(current.path);
  if (current.path !== shownPath) {
    shownPath = current.path;
    window.scrollTo(0, 0);
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
  watchIdle(() => lockNow("闲置超过 15 分钟，保险库已自动锁定。"));
  api("GET", "/devices").then(({ devices }) => ctx.setDeviceCount(devices.length)).catch(() => {});
  await route();
}

// Leave the unlocked app: dialogs may show decrypted values, so they go too.
function leaveApp() {
  closeModals();
  lock();
  shell = null;
  page = null;
  shownPath = null;
}

function showLogin(message) {
  renderLogin(root, { message, onDone: enterApp });
}

function showUnlock(message) {
  renderUnlock(root, { message, onDone: enterApp, onSignOut: () => signOut() });
}

function lockNow(message) {
  leaveApp();
  showUnlock(message);
  updateIfWaiting();
}

async function signOut() {
  try {
    await api("POST", "/logout");
  } catch {
    // The session may already be gone; signing out locally is what matters.
  }
  leaveApp();
  showLogin();
  toast("已退出登录。");
  updateIfWaiting();
}

async function boot() {
  initPwa();
  // Reloading costs nothing while locked; while unlocked it means typing the
  // password again, which is fine when the app was just brought back, but not
  // in the middle of editing.
  watchForUpdates({ safeToReload: (resumed) => !document.querySelector("dialog[open]") && (!isUnlocked() || resumed) });
  setSignedOutHandler(() => {
    if (!session.username) return;
    leaveApp();
    session.username = null;
    showLogin("登录已过期，请重新登录。");
  });
  window.addEventListener("hashchange", () => route());
  onLayoutChange(() => {
    if (!isUnlocked() || !page) return;
    renderShell();
    route();
  });
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
