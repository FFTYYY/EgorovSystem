// Reload into a newer interface. An installed app (or a tab left open) keeps
// running the files it started with; after a release the server reports a
// different fingerprint in /status, and this module reloads the page when that
// costs nothing, or offers a button when the user is in the middle of something.

import { api } from "./api.js";
import { h, icon } from "./dom.js";

// The fingerprinted directory this module was loaded from, e.g. assets/3f2a.../
const RUNNING = (import.meta.url.match(/\/assets\/([0-9a-f]+)\//) || [])[1] || null;
const EVERY_MS = 30 * 60 * 1000;
const RELOADED_FOR = "egorov-reloaded-for";

let latest = null; // fingerprint of a newer interface, once seen
let bar = null;
let safeToReload = () => true;

function reload() {
  // At most one automatic reload per release, in case something keeps serving
  // the old files; after that the bar stays and the user decides.
  sessionStorage.setItem(RELOADED_FOR, latest);
  location.reload();
}

function showBar() {
  if (bar) return;
  bar = h("div", { class: "update-bar", role: "status" },
    h("span", null, "界面有新版本"),
    h("button", { class: "button button-primary", type: "button", onclick: () => location.reload() },
      icon("check"), "刷新"));
  document.body.append(bar);
}

async function check(resumed) {
  if (!latest) {
    let status;
    try {
      status = await api("GET", "/status");
    } catch {
      return; // offline or the server is restarting; try again later
    }
    if (!status.ui || status.ui === RUNNING) return;
    latest = status.ui;
    navigator.serviceWorker?.getRegistration().then((reg) => reg && reg.update()).catch(() => {});
  }
  const already = sessionStorage.getItem(RELOADED_FOR) === latest;
  if (!already && safeToReload(resumed)) reload();
  else showBar();
}

// safeToReload(resumed) says whether reloading now loses nothing the user
// cares about; `resumed` is true when the page just came back to the screen.
export function watchForUpdates(options) {
  if (!RUNNING) return; // served from unversioned paths (development)
  safeToReload = options.safeToReload;
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") check(true);
  });
  window.addEventListener("online", () => check(false));
  setInterval(() => { if (document.visibilityState === "visible") check(false); }, EVERY_MS);
}

// Called when the vault locks: with nothing open any more, take the update.
export function updateIfWaiting() {
  if (latest && sessionStorage.getItem(RELOADED_FOR) !== latest) reload();
}
