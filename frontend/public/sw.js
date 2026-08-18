// Minimal cache-first service worker for the app shell — enough to make the
// PWA installable and resilient to a flaky connection, not a full offline
// story (the app is useless without a live connection to the SkyTrap backend
// anyway, since every screen after login talks to a real WebSocket/API).
const CACHE_NAME = "skytrap-shell-v1";
const SHELL_ASSETS = ["/", "/manifest.json", "/icons/icon.svg"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE_NAME).then((cache) => cache.addAll(SHELL_ASSETS)));
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key)))
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  if (event.request.method !== "GET") return;
  const url = new URL(event.request.url);
  // Never intercept API/WebSocket traffic — only the static shell.
  if (["/auth", "/turns", "/ws"].some((prefix) => url.pathname.startsWith(prefix))) return;

  event.respondWith(
    caches.match(event.request).then(
      (cached) =>
        cached ||
        fetch(event.request).then((response) => {
          const copy = response.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(event.request, copy));
          return response;
        })
    )
  );
});
