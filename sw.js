// Force l'installation immédiate du nouveau Service Worker
self.addEventListener('install', () => self.skipWaiting());

// Prend le contrôle de la page immédiatement après l'activation
self.addEventListener('activate', (event) => event.waitUntil(self.clients.claim()));

// Gardé pour l'installation en PWA
self.addEventListener('fetch', () => {});

// Chemins relatifs à l'emplacement du site (fonctionne aussi dans un sous-dossier GitHub Pages : /mon-depot/)
const ICON = new URL('icon.png', self.registration.scope).href;

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
