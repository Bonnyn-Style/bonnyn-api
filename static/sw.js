const CACHE_NAME = 'bonnyn-cache-v14.22.0';
const ASSETS_TO_CACHE = [
    '/',
    '/landing',
    '/login',
    '/registro',
    '/static/manifest.json'
];

// 1. Instalación del Service Worker y almacenamiento en caché de archivos críticos
self.addEventListener('install', (event) => {
    event.waitUntil(
        caches.open(CACHE_NAME).then((cache) => {
            return cache.addAll(ASSETS_TO_CACHE);
        })
    );
    // Forzar al Service Worker a activarse de inmediato saltando la fase de espera
    self.skipWaiting();
});

// 2. Activación: Limpieza automática de cachés antiguos y obsoletos
self.addEventListener('activate', (event) => {
    event.waitUntil(
        caches.keys().then((keys) => {
            return Promise.all(
                keys.map((key) => {
                    if (key !== CACHE_NAME) {
                        console.log('[Bonnyn SW] Eliminando caché antiguo obsoleto:', key);
                        return caches.delete(key);
                    }
                })
            );
        })
    );
    // Tomar el control de todas las pestañas abiertas inmediatamente
    self.clients.claim();
});

// 3. Estrategia de intercepción de peticiones (Fetch)
self.addEventListener('fetch', (event) => {
    const url = new URL(event.request.url);

    // 🛡️ NUNCA cachear las peticiones a la API o pasarelas de pago (tiempo real estricto)
    if (url.pathname.startsWith('/api/')) {
        return;
    }

    event.respondWith(
        caches.match(event.request).then((cachedResponse) => {
            // Si está en caché, lo devolvemos, pero consultamos la red en segundo plano si es necesario
            if (cachedResponse) {
                return cachedResponse;
            }
            return fetch(event.request).then((response) => {
                return response;
            }).catch(() => {
                // Fallback opcional para páginas HTML en caso de estar completamente offline
                if (event.request.headers.get('accept').includes('text/html')) {
                    return caches.match('/landing');
                }
            });
        })
    );
});