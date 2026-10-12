// Force l'installation immédiate du nouveau Service Worker
self.addEventListener('install', (event) => {
    self.skipWaiting();
});

// Prend le contrôle de la page immédiatement après l'activation
self.addEventListener('activate', (event) => {
    event.waitUntil(self.clients.claim());
});

// 1. RÉCEPTION DU SIGNAL PUSH EN ARRIÈRE-PLAN
self.addEventListener('push', (event) => {
    // Affichage synchrone et immédiat (obligatoire pour iOS)
    const options = {
        body: "Un de vos objectifs de temps d'attente ou de spectacle est atteint !",
        icon: '/icon.png', // Chemin vers l'icône de votre PWA
        badge: '/icon.png', // Petite icône monochrome pour la barre d'état (Android)
        vibrate: [200, 100, 200] // Motif de vibration
    };
    
    event.waitUntil(
        self.registration.showNotification("Alerte DLP Companion", options)
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
