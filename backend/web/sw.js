// Minimal service worker: makes the app installable. Chat is live-only,
// so there is deliberately no offline caching.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", e => e.waitUntil(clients.claim()));
self.addEventListener("fetch", () => {});
