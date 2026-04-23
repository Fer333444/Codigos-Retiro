// static/sw.js
self.addEventListener('push', function(event) {
    const data = event.data ? event.data.json() : {};
    
    const options = {
        body: data.body || 'Tienes un nuevo retiro asignado.',
        icon: 'https://cdn-icons-png.flaticon.com/512/1828/1828640.png',
        badge: 'https://cdn-icons-png.flaticon.com/512/1828/1828640.png',
        vibrate: [200, 100, 200, 100, 200, 100, 200], // Vibración fuerte
    };

    event.waitUntil(
        self.registration.showNotification(data.title || '¡Nuevo Retiro! 💰', options)
    );
});

self.addEventListener('notificationclick', function(event) {
    event.notification.close();
    // Al tocar la notificación, abre la bandeja
    event.waitUntil(
        clients.openWindow('/') 
    );
});