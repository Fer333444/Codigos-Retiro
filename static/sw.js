self.addEventListener('push', function(event) {
    let data = {};
    if (event.data) {
        try {
            data = event.data.json();
        } catch (e) {
            data = { title: 'Nueva Alerta', body: event.data.text() };
        }
    }

    const title = data.title || data.titulo || 'Alerta ERP';
    const options = {
        body: data.body || data.mensaje || 'Tienes un nuevo movimiento.',
        icon: data.icon || '/static/flujo-notificacion.png',
        badge: '/static/flujo-notificacion.png',
        vibrate: [300, 100, 300, 100, 300],
        requireInteraction: true,
        tag: Date.now().toString(),
        data: {
            url: data.url || '/'
        }
    };

    event.waitUntil(
        self.registration.showNotification(title, options)
    );
});

self.addEventListener('notificationclick', function(event) {
    event.notification.close();
    if (event.notification.data && event.notification.data.url) {
        event.waitUntil(
            clients.matchAll({ type: 'window' }).then(function(clientList) {
                for (let i = 0; i < clientList.length; i++) {
                    let client = clientList[i];
                    if (client.url === event.notification.data.url && 'focus' in client) {
                        return client.focus();
                    }
                }
                if (clients.openWindow) {
                    return clients.openWindow(event.notification.data.url);
                }
            })
        );
    }
});