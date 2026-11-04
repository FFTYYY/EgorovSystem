// Installing the admin UI as an app on a phone or desktop. The service worker
// (sw.js) only keeps a copy of the interface for when the network is gone.

import { h, modal, toast } from "./dom.js";

let deferred = null;

// Trusted Types are enforced, so register() needs a TrustedScriptURL. The CSP
// allows exactly this one policy, and it accepts nothing but the worker's URL.
function workerUrl() {
  const url = "sw.js";
  if (!window.trustedTypes) return url;
  const policy = window.trustedTypes.createPolicy("egorov-sw", {
    createScriptURL: (value) => {
      if (value !== url) throw new TypeError(`Refusing script URL ${value}`);
      return value;
    },
  });
  return policy.createScriptURL(url);
}

export function initPwa() {
  if ("serviceWorker" in navigator) {
    // sw.js is served at /admin/sw.js so that its scope covers the admin page.
    try {
      navigator.serviceWorker.register(workerUrl()).catch(() => {});
    } catch {
      // No service worker: the app still works, it just cannot open offline.
    }
  }
  // Chrome and Edge offer their own install prompt; keep the event so the
  // "install" buttons can bring it up again later.
  window.addEventListener("beforeinstallprompt", (event) => { deferred = event; });
  window.addEventListener("appinstalled", () => { deferred = null; });
}

export function isStandalone() {
  return window.matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;
}

function isIos() {
  return /iPhone|iPad|iPod/.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
}

// "prompt": the browser can show its install dialog. "ios": iOS installs from
// the share menu, which a page cannot open. null: installed, or not offered.
export function installMode() {
  if (isStandalone()) return null;
  if (deferred) return "prompt";
  if (isIos()) return "ios";
  return null;
}

function iosSteps() {
  return modal({
    title: "安装到主屏幕",
    sheet: true,
    build: () => h("div", { class: "modal-body" },
      h("ol", { class: "steps" },
        h("li", null, "点浏览器的分享按钮（方框和向上的箭头）。"),
        h("li", null, "选择“添加到主屏幕”，再点“添加”。")),
      h("p", { class: "field-hint" }, "之后从主屏幕打开，它会像应用一样全屏运行。")),
  });
}

// Install with whatever this browser offers: its own dialog, or the steps.
export async function install() {
  const mode = installMode();
  if (mode === "ios") return iosSteps();
  const event = deferred;
  if (!event) return undefined;
  deferred = null;
  await event.prompt();
  const { outcome } = await event.userChoice;
  if (outcome === "accepted") toast("已安装，可以从主屏幕打开了。");
  return undefined;
}
