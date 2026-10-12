(() => {
    let raizSincronizada = null;
    let marcaServidor = null;
    let segundosServidor = null;
    let inicioReloj = 0;
    const clasesUrgencia = ['asignado-normal', 'asignado-alerta', 'asignado-urgente'];

    function ahoraServidor(raiz) {
        const marca = raiz.dataset.ahoraServidor;
        if (raiz !== raizSincronizada || marca !== marcaServidor) {
            raizSincronizada = raiz;
            marcaServidor = marca;
            const segundos = marca ? Number(marca) : NaN;
            segundosServidor = Number.isFinite(segundos) ? segundos : null;
            inicioReloj = performance.now();
        }
        if (segundosServidor === null) return null;
        return segundosServidor + Math.max(0, performance.now() - inicioReloj) / 1000;
    }

    function escribir(elemento, texto) {
        if (elemento && elemento.textContent !== texto) elemento.textContent = texto;
    }

    window.actualizarContadoresAsignados = function () {
        const raiz = document.getElementById('contenidoDinamico');
        if (!raiz) return;
        const ahora = ahoraServidor(raiz);

        raiz.querySelectorAll('[data-tarjeta-cobrador]').forEach(tarjeta => {
            const lista = tarjeta.querySelector('[data-lista-asignados]');
            if (!lista) return;
            let cantidad = 0;
            let vistos = 0;
            let centavos = 0;

            lista.querySelectorAll('[data-asignado-id]').forEach(fila => {
                const plazo = fila.dataset.venceEn;
                const vencimiento = plazo ? Number(plazo) : NaN;
                if (ahora !== null && Number.isFinite(vencimiento)) {
                    const restante = vencimiento - ahora;
                    if (restante <= 0) {
                        fila.remove();
                        return;
                    }

                    const segundos = Math.ceil(restante);
                    const horas = Math.floor(segundos / 3600);
                    const minutos = Math.floor((segundos % 3600) / 60);
                    const tiempo = [horas, minutos, segundos % 60]
                        .map(parte => String(parte).padStart(2, '0')).join(':');
                    escribir(fila.querySelector('[data-tiempo-asignado]'), tiempo);

                    const urgente = restante <= 600;
                    const alerta = restante <= 1800;
                    const clase = urgente ? 'asignado-urgente' : alerta ? 'asignado-alerta' : 'asignado-normal';
                    clasesUrgencia.forEach(nombre => fila.classList.toggle(nombre, nombre === clase));
                    escribir(fila.querySelector('[data-urgencia-asignado]'),
                        urgente ? 'Vence pronto' : alerta ? 'Por vencer' : 'Tiempo restante');
                }

                cantidad += 1;
                if (fila.dataset.visto === 'true') vistos += 1;
                const monto = Number(fila.dataset.monto);
                if (Number.isFinite(monto) && monto >= 0) centavos += Math.round(monto * 100);
            });

            escribir(tarjeta.querySelector('[data-asignados-count]'),
                `${cantidad} ${cantidad === 1 ? 'código' : 'códigos'}`);
            escribir(tarjeta.querySelector('[data-asignados-vistos]'), `${vistos} Vistos`);
            escribir(tarjeta.querySelector('[data-asignados-valor]'), `$${(centavos / 100).toFixed(2)}`);
            const vacio = tarjeta.querySelector('[data-sin-asignados]');
            if (vacio) {
                vacio.hidden = cantidad !== 0;
                vacio.classList.toggle('hidden', cantidad !== 0);
            }
        });
    };

    window.actualizarContadoresAsignados();
})();
