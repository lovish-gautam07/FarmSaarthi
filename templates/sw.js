// FarmSaarthi service worker: lets the app open without internet.
// The page saves the last analysis on the phone itself; this file keeps the
// page, styles, icons and language text available offline.
const VERSION = "{{ version }}";
const CACHE = `farmsaarthi-${VERSION}`;
const PRECACHE = [
    "/",
    "/static/styles.css?v={{ version }}",
    "/manifest.webmanifest",
    "/static/icons/icon-192.png?v={{ version }}",
    "/static/icons/favicon.png?v={{ version }}",
];

self.addEventListener("install", (event) => {
    event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(PRECACHE)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
    event.waitUntil(
        caches.keys()
            .then((keys) => Promise.all(keys.filter((key) => key.startsWith("farmsaarthi-") && key !== CACHE).map((key) => caches.delete(key))))
            .then(() => self.clients.claim())
    );
});

async function networkFirst(request, fallbackUrl) {
    const cache = await caches.open(CACHE);
    try {
        const response = await fetch(request);
        if (response.ok) {
            cache.put(request, response.clone());
        }
        return response;
    } catch (error) {
        const cached = await cache.match(request) || (fallbackUrl && await cache.match(fallbackUrl));
        if (cached) {
            return cached;
        }
        throw error;
    }
}

async function cacheFirst(request) {
    const cache = await caches.open(CACHE);
    const cached = await cache.match(request);
    if (cached) {
        return cached;
    }
    const response = await fetch(request);
    if (response.ok) {
        cache.put(request, response.clone());
    }
    return response;
}

self.addEventListener("fetch", (event) => {
    const request = event.request;
    const url = new URL(request.url);
    if (request.method !== "GET" || url.origin !== self.location.origin) {
        return;  // analyses, uploads, read-aloud and photo links always go to the network
    }
    if (request.mode === "navigate") {
        event.respondWith(networkFirst(request, "/"));
    } else if (url.pathname.startsWith("/static/")) {
        event.respondWith(cacheFirst(request));
    } else if (url.pathname.startsWith("/i18n/") || url.pathname === "/manifest.webmanifest") {
        event.respondWith(networkFirst(request));
    }
});
