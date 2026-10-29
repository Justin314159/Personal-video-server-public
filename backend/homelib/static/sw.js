// Service worker: caches the app shell only (never videos or API data),
// so the Home Screen app opens instantly and shows a friendly error offline.
const CACHE = 'homelib-shell-v5';
const SHELL = [
  '/', '/index.html', '/css/app.css', '/manifest.webmanifest',
  '/js/app.js', '/js/api.js', '/js/state.js', '/js/theme.js', '/js/router.js', '/js/layout.js', '/js/ui.js',
  '/js/screens/common.js', '/js/screens/profiles.js', '/js/screens/search.js', '/js/screens/foryou.js',
  '/js/screens/queue.js', '/js/screens/library.js', '/js/screens/playlist.js', '/js/screens/downloads.js', '/js/screens/settings.js', '/js/screens/player.js',
  '/js/screens/shorts.js', '/js/screens/appearance.js', '/js/screens/algorithm.js',
  '/js/screens/music.js', '/js/musicplayer.js', '/js/screens/shortcut.js',
  '/fonts/figtree-latin-400-normal.woff2', '/fonts/figtree-latin-600-normal.woff2', '/fonts/figtree-latin-700-normal.woff2',
  '/icons/apple-touch-icon.png', '/icons/icon-192.png',
];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET' || url.origin !== location.origin || url.pathname.startsWith('/api/')) return;
  // Network first (so updates show up), cache as fallback.
  e.respondWith(fetch(e.request).then((r) => {
    if (r.ok) { const copy = r.clone(); caches.open(CACHE).then((c) => c.put(e.request, copy)); }
    return r;
  }).catch(() => caches.match(e.request).then((r) => r || caches.match('/index.html'))));
});
