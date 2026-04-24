self.addEventListener('push', function(event) {
    const data = event.data ? event.data.json() : {};
    
    const options = {
        body: data.body || 'Tienes un nuevo mensaje.',
        icon: 'https://cdn-icons-png.flaticon.com/512/1828/1828640.png',
        badge: 'https://cdn-icons-png.flaticon.com/512/1828/1828640.png',
        vibrate: [200, 100, 200, 100, 200, 100, 200],
        requireInteraction: true // Hace que la notificación no desaparezca sola
    };

    event.waitUntil(
        self.registration.showNotification(data.title || '¡Alerta Flujo ERP!', options)
    );
});

self.addEventListener('notificationclick', function(event) {
    event.notification.close();
    event.waitUntil( clients.openWindow('/') ); // Abre la app al tocarla
});