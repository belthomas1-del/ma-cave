const CACHE_NAME = "ma-cave-v1";
const ASSETS = [
  "/index.html",
  "/manifest.json",
  "https://fonts.googleapis.com/css2?family=Cormorant+Garamond:wght@300;400;500;600;700&display=swap"
];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE_NAME).then(c => c.addAll(ASSETS)));
  self.skipWaiting();
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (e) => {
  // Network first for API calls, cache first for assets
  if (e.request.url.includes("/search") || e.request.url.includes("/wine")) {
    e.respondWith(fetch(e.request).catch(() => new Response(JSON.stringify({error:"offline",results:[]}), {headers:{"Content-Type":"application/json"}})));
  } else {
    e.respondWith(
      caches.match(e.request).then(r => r || fetch(e.request).then(resp => {
        const clone = resp.clone();
        caches.open(CACHE_NAME).then(c => c.put(e.request, clone));
        return resp;
      }))
    );
  }
});
