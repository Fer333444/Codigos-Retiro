(function () {
    'use strict';
    const bancos = { pichincha: 'Banco Pichincha', guayaquil: 'Banco Guayaquil' };

    function camposBanco(banco, campos) {
        if (banco === 'pichincha') return [campos.unico];
        if (banco === 'guayaquil') return [campos.envio, campos.retiro];
        return [];
    }

    function codigosValidos(banco, campos) {
        return camposBanco(banco, campos).every(input => input.readOnly
            ? input.value.trim().length > 0 : /^[0-9]{4}$/.test(input.value));
    }

    function configurar({ form, banco, monto, campos, validar }) {
        const origen = document.createElement('input');
        origen.type = 'hidden';
        origen.name = 'campos_manuales';
        form.appendChild(origen);
        const todos = [campos.unico, campos.envio, campos.retiro];
        const originales = new Map(todos.map(input => [input, input.getAttribute('maxlength')]));
        const anteriores = new Map();
        const ayudas = new Map();
        let dialogo = null;
        let aprobado = null;
        let enviado = false;

        todos.forEach(input => {
            const ayuda = document.createElement('small');
            ayuda.id = input.id + 'AyudaManual';
            ayuda.className = 'ayuda-codigo-manual';
            ayuda.hidden = true;
            ayuda.setAttribute('aria-live', 'polite');
            input.insertAdjacentElement('afterend', ayuda);
            input.setAttribute('aria-describedby', [input.getAttribute('aria-describedby'), ayuda.id].filter(Boolean).join(' '));
            ayudas.set(input, ayuda);
            anteriores.set(input, '');
            input.addEventListener('input', () => {
                if (esManual(input)) {
                    if (!/^[0-9]{0,4}$/.test(input.value)) {
                        input.value = anteriores.get(input) || '';
                        ayudas.get(input).textContent = 'Solo se permiten 4 dígitos. Revisa el código completo.';
                    } else {
                        anteriores.set(input, input.value);
                        ayudas.get(input).textContent = 'Escribe exactamente 4 dígitos.';
                    }
                }
                validar();
            });
            input.addEventListener('paste', event => {
                if (!esManual(input)) return;
                const texto = event.clipboardData.getData('text');
                const siguiente = input.value.slice(0, input.selectionStart) + texto + input.value.slice(input.selectionEnd);
                // No recortar un código de 6 dígitos: podría convertirse en otro código válido.
                if (!/^[0-9]{0,4}$/.test(siguiente)) {
                    event.preventDefault();
                    ayudas.get(input).textContent = 'El código pegado no es válido. Debe tener 4 dígitos, sin letras ni espacios.';
                }
            });
        });

        function esManual(input) {
            return camposBanco(banco.value, campos).includes(input) && !input.readOnly;
        }

        function actualizar() {
            todos.forEach(input => {
                const manual = esManual(input);
                const ayuda = ayudas.get(input);
                ayuda.hidden = !manual;
                if (manual) {
                    input.setAttribute('maxlength', '4');
                    input.setAttribute('minlength', '4');
                    input.setAttribute('pattern', '[0-9]{4}');
                    input.setAttribute('inputmode', 'numeric');
                    input.setAttribute('title', 'Escribe exactamente 4 dígitos.');
                    if (!ayuda.textContent) ayuda.textContent = 'Escribe exactamente 4 dígitos.';
                    if (/^[0-9]{0,4}$/.test(input.value)) anteriores.set(input, input.value);
                } else {
                    ['minlength', 'pattern', 'inputmode', 'title'].forEach(attr => input.removeAttribute(attr));
                    const max = originales.get(input);
                    max === null ? input.removeAttribute('maxlength') : input.setAttribute('maxlength', max);
                }
            });
            origen.value = todos.filter(esManual).map(input => input.name).join(',');
        }

        function firma() {
            return JSON.stringify([banco.value, monto.value, ...todos.map(input => [input.value, input.readOnly])]);
        }

        function cerrar() {
            if (!dialogo) return;
            const anterior = dialogo;
            dialogo = null;
            anterior.close();
            anterior.remove();
        }

        function pedirConfirmacion() {
            const firmaInicial = firma();
            const bancoInicial = bancos[banco.value];
            const montoInicial = '$' + Number(monto.value).toFixed(2);
            dialogo = document.createElement('dialog');
            dialogo.className = 'confirmacion-retiro';
            dialogo.setAttribute('aria-labelledby', 'tituloConfirmacionRetiro');
            dialogo.innerHTML = '<p class="paso-confirmacion"></p><h2 id="tituloConfirmacionRetiro"></h2>' +
                '<p class="mensaje-confirmacion"></p><dl><div><dt>Banco</dt><dd data-banco></dd></div>' +
                '<div><dt>Monto del retiro</dt><dd data-monto></dd></div></dl>' +
                '<div class="acciones-confirmacion"><button type="button" data-cancelar>Cancelar y corregir</button>' +
                '<button type="button" data-confirmar></button></div>';
            document.body.appendChild(dialogo);
            dialogo.querySelector('[data-banco]').textContent = bancoInicial;
            dialogo.querySelector('[data-monto]').textContent = montoInicial;
            let paso = 1;
            const dibujarPaso = () => {
                dialogo.querySelector('.paso-confirmacion').textContent = 'Confirmación ' + paso + ' de 2';
                dialogo.querySelector('h2').textContent = paso === 1 ? 'Revisa el banco y el monto' : '¿Confirmas el envío de este retiro?';
                dialogo.querySelector('.mensaje-confirmacion').textContent = paso === 1
                    ? 'Comprueba que estos datos coincidan con el retiro que generaste.'
                    : 'Esta es la última confirmación. Si hay un error, cancela para corregirlo.';
                dialogo.querySelector('[data-confirmar]').textContent = paso === 1 ? 'Sí, los datos son correctos' : 'Confirmar y enviar';
                // El segundo paso requiere una nueva acción, incluso con doble clic o Enter sostenido.
                dialogo.querySelector('[data-cancelar]').focus();
            };
            dialogo.addEventListener('cancel', event => { event.preventDefault(); cerrar(); });
            dialogo.querySelector('[data-cancelar]').addEventListener('click', cerrar);
            const confirmar = dialogo.querySelector('[data-confirmar]');
            confirmar.addEventListener('click', event => {
                if (event.detail > 1 || confirmar.disabled) return;
                if (firma() !== firmaInicial) { cerrar(); return; }
                if (paso === 1) {
                    paso = 2;
                    confirmar.disabled = true;
                    dibujarPaso();
                    setTimeout(() => { confirmar.disabled = false; }, 400);
                    return;
                }
                cerrar();
                if (!validar() || !form.reportValidity()) return;
                aprobado = firmaInicial;
                form.requestSubmit();
            });
            dialogo.showModal();
            dibujarPaso();
        }

        form.addEventListener('submit', event => {
            actualizar();
            if (enviado) { event.preventDefault(); event.stopImmediatePropagation(); return; }
            if (!origen.value) return;
            if (aprobado === firma()) { aprobado = null; enviado = true; return; }
            aprobado = null;
            event.preventDefault();
            event.stopImmediatePropagation();
            if (dialogo || !validar() || !form.reportValidity()) return;
            pedirConfirmacion();
        }, true);
        banco.addEventListener('change', actualizar);
        actualizar();
        return { actualizar };
    }

    window.RetirosManuales = { configurar, codigosValidos };
}());
