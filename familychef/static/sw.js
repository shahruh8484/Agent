// FamilyChef service worker: app shell offline, API always from network.
const CACHE = "familychef-v8";
const SHELL = [
  "/", "/static/style.css", "/static/i18n.js", "/static/auth.js", "/static/plus.js", "/static/app.js", "/static/icon.svg", "/static/fonts/manrope-cyrillic-wght-normal.woff2", "/static/fonts/manrope-latin-wght-normal.woff2",
  "/static/icon-192.png", "/static/manifest.webmanifest", "/privacy",
];

self.addEventListener("install", e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", e => {
  e.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", e => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin) return;
  if (url.pathname.startsWith("/api/") &&
      !["/api/regions", "/api/ingredients"].includes(url.pathname)) {
    return;  // personal data (/api/me, /api/plans, auth) is never cached
  }
  if (url.pathname.startsWith("/api/")) {
    // Regions/ingredients: network first, cached copy when offline.
    e.respondWith(
      fetch(e.request)
        .then(res => { const copy = res.clone(); caches.open(CACHE).then(c => c.put(e.request, copy)); return res; })
        .catch(() => caches.match(e.request))
    );
    return;
  }
  // Shell: network first so updates show up immediately, cache as fallback.
  e.respondWith(
    fetch(e.request)
      .then(res => { const copy = res.clone(); caches.open(CACHE).then(c => c.put(e.request, copy)); return res; })
      .catch(() => caches.match(e.request).then(r => r || caches.match("/")))
  );
});
