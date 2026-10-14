// SileroRead Service Worker
// Минимальный SW: кэширует статику, но не трогает /synthesize

const CACHE_NAME = 'silero-read-v1';
const STATIC_ASSETS = [
    '/',
    '/manifest.json',
];

// Установка: кэшируем базовые ресурсы
self.addEventListener('install', (event) => {
    console.log('[SW] Installing...');
    event.waitUntil(
        caches.open(CACHE_NAME).then((cache) => {
            return cache.addAll(STATIC_ASSETS).catch(err => {
                console.warn('[SW] Cache addAll failed:', err);
            });
        })
    );
    self.skipWaiting();
});

// Активация: чистим старые кэши
self.addEventListener('activate', (event) => {
    console.log('[SW] Activating...');
    event.waitUntil(
        caches.keys().then((keys) => {
            return Promise.all(
                keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k))
            );
        })
    );
    self.clients.claim();
});

// Fetch: не трогаем POST/synthesize и audio, остальное — network-first
self.addEventListener('fetch', (event) => {
    const url = new URL(event.request.url);

    // Не трогаем POST-запросы и аудио — они должны идти на сервер
    if (event.request.method !== 'GET') return;
    if (url.pathname.startsWith('/synthesize')) return;
    if (url.pathname.startsWith('/static/audio/')) return;

    // Остальное — network first, cache fallback
    event.respondWith(
        fetch(event.request)
            .then((response) => {
                // Кэшируем успешные GET-ответы
                if (response.ok && url.origin === self.location.origin) {
                    const clone = response.clone();
                    caches.open(CACHE_NAME).then(cache => cache.put(event.request, clone));
                }
                return response;
            })
            .catch(() => {
                return caches.match(event.request).then(cached => {
                    return cached || new Response('Offline', { status: 503 });
                });
            })
    );
});