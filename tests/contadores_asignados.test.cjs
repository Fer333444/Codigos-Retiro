const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const script = fs.readFileSync(path.join(__dirname, '../static/contadores_asignados.js'), 'utf8');

function entorno(filas, marca = '2000000000') {
    let transcurrido = 0;
    const contador = { textContent: '' };
    const vistos = { textContent: '' };
    const valor = { textContent: '' };
    const clasesVacio = new Set(['hidden']);
    const vacio = {
        hidden: true,
        clases: clasesVacio,
        classList: { toggle(clase, activo) { activo ? clasesVacio.add(clase) : clasesVacio.delete(clase); } },
    };
    const lista = {
        filas: [],
        querySelectorAll() { return this.filas.filter(fila => !fila.eliminada); },
    };
    function nuevaFila({ monto, vence, visto = false }) {
        const tiempo = { textContent: vence ? '' : 'Sin vencimiento registrado' };
        const urgencia = { textContent: 'Tiempo restante' };
        const clases = new Set();
        return {
            dataset: { monto: String(monto), visto: String(visto), ...(vence === undefined ? {} : { venceEn: String(vence) }) },
            tiempo, urgencia, clases,
            classList: { toggle(clase, activo) { activo ? clases.add(clase) : clases.delete(clase); } },
            querySelector(selector) { return selector === '[data-tiempo-asignado]' ? tiempo : urgencia; },
            remove() { this.eliminada = true; },
        };
    }
    lista.filas = filas.map(nuevaFila);
    const tarjeta = { querySelector(selector) {
        return {
            '[data-lista-asignados]': lista,
            '[data-asignados-count]': contador,
            '[data-asignados-vistos]': vistos,
            '[data-asignados-valor]': valor,
            '[data-sin-asignados]': vacio,
        }[selector];
    } };
    const raiz = { dataset: { ahoraServidor: marca }, querySelectorAll: () => [tarjeta] };
    const contexto = {
        window: {},
        document: { getElementById: () => raiz },
        performance: { now: () => transcurrido },
        Date: { now() { throw new Error('No debe utilizar el reloj del equipo'); } },
        setInterval() { throw new Error('No debe crear un segundo intervalo'); },
    };
    vm.runInNewContext(script, contexto);
    return {
        lista, contador, vistos, valor, vacio, raiz,
        avanzar(segundos) { transcurrido += segundos * 1000; contexto.window.actualizarContadoresAsignados(); },
        refrescar(nuevasFilas, ahora) {
            lista.filas = nuevasFilas.map(nuevaFila);
            raiz.dataset.ahoraServidor = ahora;
            contexto.window.actualizarContadoresAsignados();
        },
    };
}

test('usa el reloj del servidor y cambia de urgencia hasta retirar el código vencido', () => {
    const vista = entorno([{ monto: 50, vence: 2000001801 }]);
    const fila = vista.lista.filas[0];
    assert.equal(fila.tiempo.textContent, '00:30:01');
    assert.ok(fila.clases.has('asignado-normal'));
    vista.avanzar(1);
    assert.equal(fila.tiempo.textContent, '00:30:00');
    assert.ok(fila.clases.has('asignado-alerta'));
    vista.avanzar(1200);
    assert.equal(fila.tiempo.textContent, '00:10:00');
    assert.ok(fila.clases.has('asignado-urgente'));
    assert.equal(fila.clases.size, 1);
    vista.avanzar(600);
    assert.equal(fila.eliminada, true);
    assert.equal(vista.contador.textContent, '0 códigos');
    assert.equal(vista.valor.textContent, '$0.00');
    assert.equal(vista.vacio.hidden, false);
    assert.equal(vista.vacio.clases.has('hidden'), false);
});

test('conserva códigos sin vencimiento y resincroniza al recibir contenido nuevo', () => {
    const vista = entorno([
        { monto: 50.10 },
        { monto: 0.20, vence: 2000000002, visto: true },
        { monto: 100, vence: 1999999999 },
    ]);
    assert.equal(vista.contador.textContent, '2 códigos');
    assert.equal(vista.valor.textContent, '$50.30');
    assert.equal(vista.vistos.textContent, '1 Vistos');
    assert.equal(vista.lista.filas[0].tiempo.textContent, 'Sin vencimiento registrado');
    vista.avanzar(3);
    assert.equal(vista.contador.textContent, '1 código');
    assert.equal(vista.valor.textContent, '$50.10');
    assert.equal(vista.vistos.textContent, '0 Vistos');
    assert.equal(vista.vacio.hidden, true);
    vista.refrescar([{ monto: 25, vence: 2000004000 }], '2000000400');
    assert.equal(vista.lista.filas[0].tiempo.textContent, '01:00:00');
    vista.avanzar(1);
    assert.equal(vista.lista.filas[0].tiempo.textContent, '00:59:59');
    assert.equal(vista.valor.textContent, '$25.00');
});

test('no elimina filas si falta la hora del servidor y se recupera con un refresco válido', () => {
    const vista = entorno([{ monto: 30, vence: 2000000000 }], '');
    vista.avanzar(10);
    assert.equal(vista.lista.filas[0].eliminada, undefined);
    assert.equal(vista.contador.textContent, '1 código');
    vista.refrescar([{ monto: 30, vence: 2000000000 }], '2000000001');
    assert.equal(vista.contador.textContent, '0 códigos');
    assert.equal(vista.vacio.hidden, false);
});
