const CACHE = 'runescope-static-v1';
const STATIC_ASSETS = ['/static/icons/icon-192.png', '/static/icons/icon-512.png'];

self.addEventListener('install', (event) => {
  self.skipWaiting();
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(STATIC_ASSETS)));
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key))))
      .then(() => self.clients.claim())
  );
});

// Every page here is account-specific and already sent no-store (see the
// no_store_when_signed_in middleware in app.py) -- this worker only ever caches the static
// icon assets above and leaves everything else, HTML and API calls alike, to the network.
self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);
  if (event.request.method === 'GET' && url.pathname.startsWith('/static/icons/')) {
    event.respondWith(caches.match(event.request).then((cached) => cached || fetch(event.request)));
  }
});
