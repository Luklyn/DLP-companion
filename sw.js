self.addEventListener('install', (event) => self.skipWaiting());
self.addEventListener('activate', (event) => event.waitUntil(self.clients.claim()));

self.addEventListener('push', (event) => {
    const options = {
        body: "Une attraction a changé de statut (Panne/Réouverture), ou un temps d'attente est atteint !",
        icon: '/icon.png',
        badge: '/icon.png',
        vibrate: [200, 100, 200]
    };
    event.waitUntil(self.registration.showNotification("Alerte DLP Companion", options));
});

self.addEventListener('notificationclick', (event) => {
    event.notification.close();
    event.waitUntil(
        clients.matchAll({ type: 'window', includeUncontrolled: true }).then((windowClients) => {
            if (windowClients.length > 0) {
                windowClients[0].postMessage({ type: 'refresh-data' }); 
                return windowClients[0].focus();
            }
            if (clients.openWindow) return clients.openWindow('/');
        })
    );
});

// 2. COMPORTEMENT AU CLIC SUR LA NOTIFICATION
self.addEventListener('notificationclick', (event) => {
    // On ferme la notification système
    event.notification.close();

    // On réveille l'application
    event.waitUntil(
        clients.matchAll({ type: 'window', includeUncontrolled: true }).then((windowClients) => {
            // Si l'application est déjà ouverte (même en arrière-plan), on la ramène au premier plan
            if (windowClients.length > 0) {
                const client = windowClients[0];
                // Demande à l'application de rafraîchir ses données
                client.postMessage({ type: 'refresh-data' }); 
                if ('focus' in client) {
                    return client.focus();
                }
            }
            // Si l'application était totalement fermée, on l'ouvre
            if (clients.openWindow) {
                return clients.openWindow('/');
            }
        })
    );
});
