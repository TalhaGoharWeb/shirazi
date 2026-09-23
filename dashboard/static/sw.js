/* SHIRAZI dashboard service worker — Phase 7.
 * Cache-first for the app shell (HTML/CSS/JS/icons/i18n); API calls and
 * websockets always bypass the cache. This is an offline *shell* cache, not
 * offline functionality: without the desktop server there is nothing to
 * control, and the UI says so honestly (OFFLINE state). */
const CACHE = "shirazi-shell-v7";
const SHELL = [
  "/", "/login", "/static/crypto.js",
  "/manifest.webmanifest",
  "/icons/icon-192.png", "/icons/icon-512.png", "/icons/apple-touch-icon.png",
  "/i18n/en.json", "/i18n/ur.json", "/i18n/ar.json",
];

self.addEventListener("install", (e) => {
  e.waitUntil(
    caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then((ks) => Promise.all(ks.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  // API, websockets and uploads always hit the network.
  if (url.pathname.startsWith("/api/") || url.pathname.startsWith("/ws")
      || url.pathname.startsWith("/uploads")) {
    return;
  }
  e.respondWith(
    caches.match(e.request).then((hit) => hit || fetch(e.request))
  );
});
