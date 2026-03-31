/**
 * HummusLink Service Worker
 * Caches the app shell for offline/PWA support.
 * Passes through API and WebSocket requests.
 */

const CACHE_NAME = 'hummuslink-v1';
const APP_SHELL = [
    '/',
    '/styles.css',
    '/app.js',
    '/manifest.json',
];

// Install: cache the app shell
self.addEventListener('install', (event) => {
    event.waitUntil(
        caches.open(CACHE_NAME).then((cache) => {
            return cache.addAll(APP_SHELL);
        })
    );
    self.skipWaiting();
});

// Activate: clean up old caches
self.addEventListener('activate', (event) => {
    event.waitUntil(
        caches.keys().then((names) => {
            return Promise.all(
                names
                    .filter((name) => name !== CACHE_NAME)
                    .map((name) => caches.delete(name))
            );
        })
    );
    self.clients.claim();
});

// Fetch: serve from cache first, fall back to network
// Always pass through API requests and WebSocket upgrades
self.addEventListener('fetch', (event) => {
    const url = new URL(event.request.url);

    // Pass through API requests
    if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/ws/')) {
        return;
    }

    event.respondWith(
        caches.match(event.request).then((cached) => {
            if (cached) {
                // Return cached, but also update cache in background
                fetch(event.request).then((response) => {
                    if (response && response.status === 200) {
                        caches.open(CACHE_NAME).then((cache) => {
                            cache.put(event.request, response);
                        });
                    }
                }).catch(() => {});
                return cached;
            }
            return fetch(event.request).then((response) => {
                // Cache successful responses for app shell URLs
                if (response && response.status === 200) {
                    const responseClone = response.clone();
                    caches.open(CACHE_NAME).then((cache) => {
                        cache.put(event.request, responseClone);
                    });
                }
                return response;
            });
        })
    );
});
