async function tablaParaExportar() {
    let tabla = document.getElementById('tablaExportar');
    if (tabla?.dataset.exportarCompleto === 'true') {
        const url = new URL(location.href);
        url.searchParams.set('exportar', '1');
        url.searchParams.delete('pagina');
        const respuesta = await fetch(url, {credentials: 'same-origin', cache: 'no-store'});
        if (!respuesta.ok) throw new Error('No se pudo cargar el reporte completo. Intenta nuevamente.');
        const documento = new DOMParser().parseFromString(await respuesta.text(), 'text/html');
        tabla = documento.getElementById('tablaExportar');
        if (!tabla) throw new Error('No se pudo cargar el reporte. Revisa que tu sesión siga abierta.');
        if (!tabla.querySelector('[data-reporte-usuario]')) throw new Error('No hay resultados para exportar con estos filtros.');
    }
    if (!tabla || tabla.rows.length <= 1) throw new Error('No hay datos en la tabla para exportar.');
    return tabla;
}

async function prepararExportacion(generar) {
    const botones = [...document.querySelectorAll('[data-exportar-reporte]')];
    if (botones.some(b => b.disabled)) return;
    botones.forEach(b => { b.disabled = true; b.setAttribute('aria-busy', 'true'); });
    try { generar(await tablaParaExportar()); }
    catch (error) { alert(error.message || 'No se pudo generar el archivo. Intenta nuevamente.'); }
    finally { botones.forEach(b => { b.disabled = false; b.removeAttribute('aria-busy'); }); }
}

function exportarExcel(nombreArchivo) {
    return prepararExportacion(tabla => {
        const wb = XLSX.utils.table_to_book(tabla, {sheet: 'Reporte'});
        const fecha = new Date().toISOString().split('T')[0];
        XLSX.writeFile(wb, `${nombreArchivo}_${fecha}.xlsx`);
    });
}

function exportarPDF(nombreReporte) {
    return prepararExportacion(tabla => {
        const {jsPDF} = window.jspdf;
        const doc = new jsPDF('landscape');
        doc.setFontSize(16);
        doc.text(`Reporte: ${nombreReporte.replace(/_/g, ' ')}`, 14, 15);
        doc.setFontSize(10);
        doc.text(`Generado el: ${new Date().toLocaleString()}`, 14, 22);
        const opciones = {startY: 28, theme: 'grid', styles: {fontSize: 8, cellPadding: 2},
            headStyles: {fillColor: [22, 163, 74]}};
        if (tabla.dataset.exportarCompleto === 'true') {
            const texto = celda => celda.textContent.replace(/\s+/g, ' ').trim();
            opciones.head = [...tabla.tHead.rows].map(fila => [...fila.cells].map(texto));
            opciones.body = [...tabla.tBodies[0].rows].map(fila => [...fila.cells].map(texto));
        } else opciones.html = '#tablaExportar';
        doc.autoTable(opciones);
        doc.save(`${nombreReporte}_${new Date().toISOString().split('T')[0]}.pdf`);
    });
}
