const CACHE = 'runescope-static-v2';
const STATIC_ASSETS = ['/static/icons/icon-192.png', '/static/icons/icon-512.png', '/offline'];

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
// no_store_when_signed_in middleware in app.py) -- this worker never caches HTML or API
// responses. The one exception is /offline itself, precached above so it's there to fall
// back to; everything else, GET or otherwise, goes to the network exactly as if there were
// no worker at all.
self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);
  if (event.request.method === 'GET' && url.pathname.startsWith('/static/icons/')) {
    event.respondWith(caches.match(event.request).then((cached) => cached || fetch(event.request)));
    return;
  }
  // A failed navigation (offline, DNS down, the server itself unreachable) is the one case
  // worth answering from the cache instead of letting the browser show its own error page --
  // every other request type (API calls, form posts) fails openly instead, since silently
  // handing back /offline's HTML for a JSON fetch would be worse than the network error was.
  if (event.request.mode === 'navigate') {
    event.respondWith(
      fetch(event.request).catch(() => caches.match('/offline'))
    );
  }
});
