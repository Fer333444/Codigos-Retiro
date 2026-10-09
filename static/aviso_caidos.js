(() => {
  const dialogo = document.getElementById('aviso-caidos-diario');
  if (!dialogo || dialogo.dataset.iniciado) return;
  dialogo.dataset.iniciado = '1';
  let csrf = '';
  const error = document.getElementById('avc-error');
  const cerrar = () => dialogo.close();
  document.getElementById('avc-cerrar').addEventListener('click', cerrar);
  document.getElementById('avc-entendido').addEventListener('click', cerrar);
  async function post(url, datos) {
    const respuesta = await fetch(url, {method:'POST', credentials:'same-origin',
      headers:{'Content-Type':'application/x-www-form-urlencoded'},
      body:new URLSearchParams({csrf_token:csrf, ...datos})});
    if (!respuesta.ok) throw new Error('No se pudo guardar. Recarga o vuelve a intentarlo.');
    const resultado = await respuesta.json();
    if (!resultado.ok) throw new Error('No se confirmó el guardado.');
    return resultado;
  }
  dialogo.addEventListener('click', async evento => {
    const boton = evento.target.closest('button');
    const tarjeta = boton?.closest('[data-avc-clave]');
    if (!tarjeta) return;
    const confirmacion = tarjeta.querySelector('.avc-confirmacion');
    if (boton.classList.contains('avc-atender')) { confirmacion.hidden = false; return; }
    if (boton.classList.contains('avc-cancelar')) { confirmacion.hidden = true; return; }
    if (!boton.classList.contains('avc-confirmar')) return;
    boton.disabled = true;
    const resultado = tarjeta.querySelector('.avc-resultado');
    try {
      await post(tarjeta.querySelector('.avc-atender').dataset.url, {desde_aviso:'1'});
      confirmacion.hidden = true;
      tarjeta.querySelector('.avc-atender').hidden = true;
      resultado.textContent = 'Atendido. No volverá a incluirse como solicitud pendiente.';
      resultado.style.color = '';
    } catch (fallo) {
      resultado.textContent = fallo.message;
      resultado.style.color = '#b82c40';
      boton.disabled = false;
    }
    resultado.hidden = false;
  });
  async function cargar() {
    try {
      const respuesta = await fetch(dialogo.dataset.consulta, {credentials:'same-origin', cache:'no-store'});
      if (!respuesta.ok) return;
      const datos = await respuesta.json();
      if (!datos.mostrar) return;
      csrf = datos.csrf_token;
      // HTML del template del mismo servidor; todos los datos se escapan en Jinja.
      document.getElementById('avc-contenido').innerHTML = datos.html;
      dialogo.showModal();
      try { await post(dialogo.dataset.visto, {}); }
      catch (_) { error.textContent = 'No se pudo registrar la lectura del aviso. Puede aparecer nuevamente.'; error.hidden = false; }
    } catch (_) { /* Las solicitudes siguen disponibles en su apartado si falla la conexión. */ }
  }
  cargar();
})();
