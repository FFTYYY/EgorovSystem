// Service worker for the installed admin app. Served at /admin/sw.js, so it
// controls /admin/ and nothing above it.
//
// It never touches the API: /api/ requests go to the network untouched and
// are never stored. Interface files are fetched from the network first and
// the stored copy is used only when the network fails, so an update on the
// server takes effect on the next load, and an offline launch still opens
// the app (which then says it cannot reach the server).

// The server writes the current asset directory (assets/<hash>/) into the
// next line when it serves this file, so every release gets its own cache.
const ASSETS = "static/";
const CACHE = `egorov-${ASSETS.replace(/[^A-Za-z0-9]+/g, "-")}`;

// Everything the admin page loads. tests/test_admin_ui.py checks that this
// list matches the files on disk.
const FILES = [
  "static/app.css",
  "static/mobile.css",
  "static/icons/favicon-32.png",
  "static/icons/favicon-64.png",
  "static/icons/mascot-96.webp",
  "static/icons/mascot-320.webp",
  "static/manifest.webmanifest",
  "static/fonts/eb-garamond.woff2",
  "static/fonts/eb-garamond-italic.woff2",
  "static/fonts/im-fell-english.woff2",
  "static/fonts/im-fell-english-italic.woff2",
  "static/fonts/jetbrains-mono.woff2",
  "static/js/api.js",
  "static/js/crypto.js",
  "static/js/dom.js",
  "static/js/layout.js",
  "static/js/main.js",
  "static/js/mobile.js",
  "static/js/pwa.js",
  "static/js/shell.js",
  "static/js/sortable.js",
  "static/js/state.js",
  "static/js/swipe.js",
  "static/js/touchsort.js",
  "static/js/update.js",
  "static/js/views/audit.js",
  "static/js/views/catalog.js",
  "static/js/views/devices.js",
  "static/js/views/functions.js",
  "static/js/views/login.js",
  "static/js/views/overview.js",
  "static/js/views/scopes.js",
  "static/js/views/settings.js",
  "static/js/views/variables.js",
];
const SHELL = ["./", ...FILES.map((file) => ASSETS + file.slice("static/".length))];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(caches.keys()
    .then((keys) => Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key))))
    .then(() => self.clients.claim()));
});

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  const scope = new URL(self.registration.scope);
  // Pages under /admin/ also call /api/; leave those alone.
  if (url.origin !== scope.origin || !url.pathname.startsWith(scope.pathname)) return;
  // Every page in the app is the same document, whatever its query string.
  const key = request.mode === "navigate" ? scope.href : request;
  event.respondWith(fetch(request)
    .then((response) => {
      if (response.ok && response.type === "basic") {
        const copy = response.clone();
        event.waitUntil(caches.open(CACHE).then((cache) => cache.put(key, copy)));
      }
      return response;
    })
    .catch(() => caches.match(key).then((hit) => hit || Response.error())));
});
