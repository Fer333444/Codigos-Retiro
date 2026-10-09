(() => {
  const dialogo = document.getElementById('aviso-caidos-diario');
  if (!dialogo || dialogo.dataset.iniciado) return;
  dialogo.dataset.iniciado = '1';
  const contenido = document.getElementById('avc-contenido');
  const error = document.getElementById('avc-error');
  let csrf = '', filtro = 'todos', ultimoFoco = null;
  document.getElementById('avc-cerrar').addEventListener('click', () => dialogo.close());
  document.getElementById('avc-entendido').addEventListener('click', () => dialogo.close());
  dialogo.addEventListener('close', () => ultimoFoco?.focus());
  const normalizar = valor => String(valor || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();

  async function consultar(url) {
    const respuesta = await fetch(url, {credentials:'same-origin', cache:'no-store'});
    if (!respuesta.ok) throw new Error('No se pudo cargar el aviso. Consulta la bandeja de solicitudes.');
    return respuesta.json();
  }
  async function post(url, datos) {
    const respuesta = await fetch(url, {method:'POST', credentials:'same-origin',
      headers:{'Content-Type':'application/x-www-form-urlencoded'},
      body:new URLSearchParams({csrf_token:csrf, ...datos})});
    if (!respuesta.ok) throw new Error(respuesta.status === 409
      ? 'El código cambió de estado o ya tiene un pago para cruzar. Actualiza la bandeja.'
      : 'No se pudo guardar. Vuelve a intentarlo.');
    const resultado = await respuesta.json();
    if (!resultado.ok) throw new Error('No se confirmó el guardado.');
    return resultado;
  }
  function actualizarTotales(datos) {
    for (const [id, valor] of [['avc-total', datos.total_solicitudes],
      ['avc-usuarios', datos.total_usuarios], ['avc-monto', '$' + datos.total_monto]]) {
      const elemento = document.getElementById(id);
      if (elemento) elemento.textContent = valor;
    }
  }
  function filtrar() {
    const consulta = normalizar(document.getElementById('avc-buscar')?.value);
    let visibles = 0;
    contenido.querySelectorAll('[data-avc-clave]').forEach(tarjeta => {
      tarjeta.hidden = !(normalizar(tarjeta.dataset.avcBusqueda || tarjeta.textContent).includes(consulta)
        && (filtro === 'todos' || tarjeta.dataset.avcEstado === filtro));
      if (!tarjeta.hidden) visibles++;
    });
    contenido.querySelectorAll('.avc-grupo').forEach(grupo => {
      grupo.hidden = ![...grupo.querySelectorAll('[data-avc-clave]')].some(t => !t.hidden);
    });
    const vacio = document.getElementById('avc-sin-coincidencias');
    if (vacio) vacio.hidden = visibles > 0;
    const indicador = document.getElementById('avc-visible');
    if (indicador) indicador.textContent = `${visibles} código${visibles === 1 ? '' : 's'} en esta vista`;
  }
  dialogo.addEventListener('input', evento => { if (evento.target.id === 'avc-buscar') filtrar(); });
  dialogo.addEventListener('click', async evento => {
    const boton = evento.target.closest('button');
    if (!boton) return;
    if (boton.hasAttribute('data-avc-filtro')) {
      filtro = boton.dataset.avcFiltro;
      contenido.querySelectorAll('[data-avc-filtro]').forEach(b => b.setAttribute('aria-pressed', String(b === boton)));
      filtrar(); return;
    }
    const tarjeta = boton.closest('[data-avc-clave]');
    if (!tarjeta) return;
    const confirmacion = tarjeta.querySelector('.avc-confirmacion');
    if (boton.classList.contains('avc-atender')) {
      confirmacion.hidden = false; confirmacion.querySelector('.avc-confirmar').focus(); return;
    }
    if (boton.classList.contains('avc-cancelar')) {
      confirmacion.hidden = true; tarjeta.querySelector('.avc-atender').focus(); return;
    }
    if (!boton.classList.contains('avc-confirmar')) return;
    boton.disabled = true; boton.textContent = 'Guardando…';
    const resultado = tarjeta.querySelector('.avc-resultado');
    try {
      await post(tarjeta.querySelector('.avc-atender').dataset.url, {desde_aviso:'1'});
      confirmacion.hidden = true; tarjeta.querySelector('.avc-atender').hidden = true;
      tarjeta.classList.add('avc-atendido'); tarjeta.dataset.avcEstado = 'atendido';
      const grupo = tarjeta.closest('.avc-grupo');
      const cantidadGrupo = grupo?.querySelector('.avc-grupo-cantidad');
      if (cantidadGrupo) cantidadGrupo.textContent = [...grupo.querySelectorAll('[data-avc-clave]')]
        .filter(t => t.dataset.avcEstado !== 'atendido').length;
      resultado.textContent = 'Atendido. Esta solicitud ya no volverá a aparecer como pendiente.';
      resultado.classList.remove('avc-fallo'); resultado.hidden = false;
      resultado.tabIndex = -1; resultado.focus();
      try { actualizarTotales(await consultar(dialogo.dataset.consulta + '?abrir=1')); }
      catch (_) { /* El guardado ya se confirmó; los indicadores se actualizan al reabrir. */ }
    } catch (fallo) {
      resultado.textContent = fallo.message; resultado.classList.add('avc-fallo'); resultado.hidden = false;
      boton.disabled = false; boton.textContent = 'Confirmar atención';
    }
  });

  function resumenSinLibreria(datos) {
    // Respaldo local si no carga la librería del aviso original.
    return new Promise(resolve => {
      const aviso = document.createElement('dialog');
      aviso.setAttribute('aria-label', 'Resumen de Pendientes');
      aviso.style.cssText = 'border:0;border-radius:16px;padding:28px;width:min(500px,90vw);color:#334155;background:white;text-align:center;font:14px/1.6 system-ui;box-shadow:0 24px 80px #0006';
      const titulo = document.createElement('h2'); titulo.textContent = '⚠️ Resumen de Pendientes ⚠️';
      titulo.style.cssText = 'font-size:19px;font-weight:900;margin:0 0 20px';
      const conteo = document.createElement('p');
      conteo.textContent = `🔴 ${datos.cantidad_caidos} Código(s) No Salieron / Caídos\n⏱️ ${datos.cantidad_expirados} Código(s) Expirados`;
      conteo.style.whiteSpace = 'pre-line';
      const nota = document.createElement('p'); nota.textContent = 'Por favor, revisa el Historial y Deudas para mantener el sistema al día.';
      const historial = document.createElement('button'); historial.textContent = 'Ir a Historial';
      const entendido = document.createElement('button'); entendido.textContent = 'Entendido';
      for (const b of [historial, entendido]) b.style.cssText = 'border:0;border-radius:8px;padding:12px;margin:5px;background:#4f46e5;color:white;cursor:pointer';
      entendido.style.background = '#64748b';
      historial.onclick = () => aviso.close('historial'); entendido.onclick = () => aviso.close();
      aviso.addEventListener('close', () => { const navegar = aviso.returnValue === 'historial'; aviso.remove(); resolve(navegar); }, {once:true});
      aviso.append(titulo, conteo, nota, historial, entendido); document.body.append(aviso); aviso.showModal();
    });
  }
  async function mostrarResumenOriginal(datos) {
    if (!datos?.mostrar) return false;
    const hoy = new Date().toLocaleDateString();
    try { if (localStorage.getItem('alerta_pendientes_fecha') === hoy) return false; } catch (_) {}
    try { localStorage.setItem('alerta_pendientes_fecha', hoy); } catch (_) {}
    let irHistorial;
    if (window.Swal) {
      const resultado = await Swal.fire({
        icon:'warning', title:'⚠️ Resumen de Pendientes ⚠️',
        html:`<div class="text-left text-sm space-y-3 mt-2 font-bold text-slate-700">
          <p class="flex items-center gap-2"><span>🔴</span><span><b>${Number(datos.cantidad_caidos)}</b> Código(s) No Salieron / Caídos</span></p>
          <p class="flex items-center gap-2"><span>⏱️</span><span><b>${Number(datos.cantidad_expirados)}</b> Código(s) Expirados</span></p>
          <p class="text-xs font-medium text-slate-500 pt-2 border-t border-slate-200 italic">Por favor, revisa el Historial y Deudas para mantener el sistema al día.</p></div>`,
        confirmButtonText:'<i class="fas fa-history mr-1"></i> Ir a Historial', showCancelButton:true,
        cancelButtonText:'Entendido', confirmButtonColor:'#4f46e5', cancelButtonColor:'#64748b', allowOutsideClick:true,
        customClass:{popup:'rounded-2xl erp-resumen-original', title:'text-lg font-black',
          confirmButton:'font-black uppercase tracking-wider text-xs px-5 py-3', cancelButton:'font-black uppercase tracking-wider text-xs px-5 py-3'}
      });
      irHistorial = resultado.isConfirmed;
    } else irHistorial = await resumenSinLibreria(datos);
    if (irHistorial) { location.assign(datos.historial); return true; }
    return false;
  }
  async function mostrarSolicitudes(datos) {
    if (!datos?.mostrar) return;
    csrf = datos.csrf_token; filtro = 'todos'; error.hidden = true;
    // HTML del mismo servidor, con datos escapados por Jinja.
    contenido.innerHTML = datos.html; ultimoFoco = document.activeElement;
    dialogo.showModal(); filtrar();
    try { await post(dialogo.dataset.visto, {}); }
    catch (_) { error.textContent = 'No se pudo registrar la lectura. El aviso puede aparecer nuevamente.'; error.hidden = false; }
  }
  document.querySelectorAll('[data-abrir-solicitudes]').forEach(boton => boton.addEventListener('click', async () => {
    boton.disabled = true;
    try {
      const datos = await consultar(dialogo.dataset.consulta + '?abrir=1');
      if (datos.mostrar) await mostrarSolicitudes(datos);
      else boton.textContent = 'No hay solicitudes pendientes';
    } catch (_) { boton.textContent = 'Reintentar abrir solicitudes'; }
    finally { boton.disabled = false; }
  }));
  async function cargar() {
    try {
      if (await mostrarResumenOriginal(await consultar(dialogo.dataset.resumen))) return;
    } catch (_) { /* Una falla del resumen no bloquea las solicitudes. */ }
    // Releer después de cerrar el resumen evita mostrar solicitudes ya atendidas entretanto.
    try { await mostrarSolicitudes(await consultar(dialogo.dataset.consulta)); }
    catch (_) { /* La bandeja permite volver a abrir las solicitudes. */ }
  }
  cargar();
})();
