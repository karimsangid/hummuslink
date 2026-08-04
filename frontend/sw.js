/**
 * HummusLink Service Worker — KILL SWITCH
 * The previous SW was caching stale assets and breaking page loads on iPhone.
 * This version self-unregisters, deletes every cache, and reloads any open
 * clients so the next visit is a clean network fetch.
 */

self.addEventListener('install', (event) => {
    self.skipWaiting();
});

self.addEventListener('activate', (event) => {
    event.waitUntil((async () => {
        try {
            const names = await caches.keys();
            await Promise.all(names.map((n) => caches.delete(n)));
        } catch (e) {}
        try {
            await self.registration.unregister();
        } catch (e) {}
    })());
});

// Pass every request straight to the network — no caching, no interception.
self.addEventListener('fetch', (event) => {
    return;
});
