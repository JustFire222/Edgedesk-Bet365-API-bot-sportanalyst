const V = "edge-v1", SHELL = ["./", "index.html", "manifest.json", "icon-180.png"];
self.addEventListener("install", e => e.waitUntil(
  caches.open(V).then(c => c.addAll(SHELL)).then(() => self.skipWaiting())));
self.addEventListener("activate", e => e.waitUntil(
  caches.keys().then(k => Promise.all(k.filter(x => x !== V).map(x => caches.delete(x)))).then(() => clients.claim())));
self.addEventListener("fetch", e => {
  const u = new URL(e.request.url);
  if (e.request.method !== "GET" || u.origin !== location.origin) return;
  if (u.pathname.endsWith("pronostici.json")) { // dati: prima rete, poi cache offline
    e.respondWith(fetch(e.request).then(r => {
      if (r.ok) { const c = r.clone(); caches.open(V).then(x => x.put(e.request, c)); }
      return r;
    }).catch(() => caches.match(e.request)));
    return;
  }
  e.respondWith(caches.match(e.request).then(r => r || fetch(e.request))); // guscio: cache first
});
