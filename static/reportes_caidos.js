(() => {
  const abrir = document.getElementById('abrir-menu');
  const lateral = document.getElementById('menu-lateral');
  const fondo = document.getElementById('fondo-menu');
  function menu(activo) {
    lateral?.classList.toggle('abierto', activo);
    if (fondo) fondo.hidden = !activo;
    abrir?.setAttribute('aria-expanded', String(activo));
    if (activo) document.getElementById('cerrar-menu')?.focus();
    else abrir?.focus();
  }
  abrir?.addEventListener('click', () => menu(true));
  document.getElementById('cerrar-menu')?.addEventListener('click', () => menu(false));
  fondo?.addEventListener('click', () => menu(false));
  document.addEventListener('keydown', e => { if (e.key === 'Escape' && lateral?.classList.contains('abierto')) menu(false); });
  const grupos = [...document.querySelectorAll('.grupo-cliente')];
  const movil = matchMedia('(max-width: 760px)');
  function adaptar() { grupos.forEach((grupo, i) => { grupo.open = !movil.matches || i === 0; }); }
  adaptar();
  movil.addEventListener('change', adaptar);
  document.querySelectorAll('summary a, summary button').forEach(enlace => enlace.addEventListener('click', e => e.stopPropagation()));
  document.querySelectorAll('form[data-guardar]').forEach(form => form.addEventListener('submit', () => {
    const boton = form.querySelector('button[type=submit]');
    if (boton) { boton.disabled = true; boton.textContent = 'Guardando…'; }
  }));
})();
