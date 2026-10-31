// Force l'installation immédiate du nouveau Service Worker
self.addEventListener('install', () => self.skipWaiting());

// Prend le contrôle de la page immédiatement après l'activation
self.addEventListener('activate', (event) => event.waitUntil(self.clients.claim()));

// Chemins relatifs à l'emplacement du site (fonctionne aussi dans un sous-dossier GitHub Pages : /mon-depot/)
const ICON = new URL('icon.png', self.registration.scope).href;

// Données des lieux (parks.json + data/<parc>/*.json) : réseau d'abord (toujours la dernière version du JSON),
// copie en cache utilisée seulement si le réseau est indisponible.
// Le reste des requêtes (API temps réel, images, etc.) garde son comportement par défaut.
const DATA_CACHE = 'dlp-data-v2';
const SCOPE_URL = new URL(self.registration.scope);
const DATA_PATH = new RegExp('^' + SCOPE_URL.pathname.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') +
    '(parks\\.json|data/[^/]+/(attractions|restaurants|shows)\\.json)$');
const isDataRequest = (request) => {
    const url = new URL(request.url);
    return request.method === 'GET' && url.origin === SCOPE_URL.origin && DATA_PATH.test(url.pathname);
};

// Supprime les anciens caches de données (v1 : fichiers à la racine)
self.addEventListener('activate', (event) => event.waitUntil(
    caches.keys().then((keys) => Promise.all(keys.filter((k) => k.startsWith('dlp-data-') && k !== DATA_CACHE).map((k) => caches.delete(k))))
));

self.addEventListener('fetch', (event) => {
    const { request } = event;
    if (!isDataRequest(request)) return;

    event.respondWith((async () => {
        const cache = await caches.open(DATA_CACHE);
        try {
            const response = await fetch(request);
            if (response.ok) cache.put(request, response.clone());
            return response;
        } catch (error) {
            const cached = await cache.match(request);
            if (cached) return cached;
            throw error;
        }
    })());
});

// 1. RÉCEPTION D'UN PUSH (application fermée ou téléphone verrouillé)
self.addEventListener('push', (event) => {
    let data = {};
    if (event.data) {
        try { data = event.data.json(); }
        catch { data = { body: event.data.text() }; }
    }

    const options = {
        body: data.body || 'Un de vos objectifs de temps d’attente ou de spectacle est atteint !',
        icon: ICON,
        badge: ICON,
        tag: data.tag || 'dlp-alert',   // même tag = remplace la notification précédente au lieu d'en empiler une seconde
        renotify: true,
        vibrate: [200, 100, 200],
        data: { id: data.id || null },
    };

    // Chaque push DOIT afficher une notification (obligatoire sur iOS, sous peine de perdre l'abonnement)
    event.waitUntil(self.registration.showNotification(data.title || 'Alerte DLP Companion', options));
});

// 2. CLIC SUR LA NOTIFICATION : ouvre l'app sur la fiche concernée
self.addEventListener('notificationclick', (event) => {
    event.notification.close();
    const id = event.notification.data?.id;

    event.waitUntil((async () => {
        const wins = await clients.matchAll({ type: 'window', includeUncontrolled: true });
        const target = wins.find((w) => w.url.startsWith(self.registration.scope)) || wins[0];
        if (target) {
            if (id) target.postMessage({ type: 'open-item', id });
            target.postMessage({ type: 'refresh-data' });
            return target.focus();
        }
        // Application totalement fermée : on l'ouvre directement sur la fiche (#id)
        return clients.openWindow(self.registration.scope + (id ? '#' + encodeURIComponent(id) : ''));
    })());
});
