self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (event) => event.waitUntil(self.clients.claim()));

// Réception silencieuse du signal Push envoyé par Cloudflare
self.addEventListener('push', function(event) {
    // Dans notre architecture Cloudflare simplifiée, le Worker envoie un signal vide.
    // Le téléphone se réveille et va lui-même chercher les données à jour.
    event.waitUntil(
        fetch('https://api.themeparks.wiki/v1/entity/e8d0207f-da8a-4048-bec8-117aa946b2c2/live')
        .then(response => response.json())
        .then(data => {
            // Affichage générique (le Cloudflare a déclenché l'alerte, donc un seuil est atteint)
            const options = {
                body: "Le temps d'attente a baissé ou une séance approche !",
                icon: '/icon.png', // Chemin vers votre icône PWA (si existante)
                badge: '/icon.png',
                vibrate: [200, 100, 200]
            };
            return self.registration.showNotification("DLP Companion - Alerte", options);
        })
        .catch(err => {
            console.error("Erreur de récupération des données au réveil :", err);
        })
    );
});

// Comportement au clic sur la notification native
self.addEventListener('notificationclick', (event) => {
    event.notification.close();
    const id = event.notification.data?.id;
    event.waitUntil((async () => {
        const wins = await clients.matchAll({ type: 'window', includeUncontrolled: true });
        if (wins.length) {
            if (id) wins[0].postMessage({ type: 'open-item', id });
            return wins[0].focus();
        }
        return clients.openWindow('./#' + encodeURIComponent(id || ''));
    })());
});