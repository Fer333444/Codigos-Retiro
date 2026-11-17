"""Pruebas locales con fuentes y Telegram simulados. No acceden a producción."""
import copy
import os
from datetime import datetime
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import zipfile

import reportes_diarios as reportes


def fila(rid=1, **kwargs):
    data = dict(id=rid, fecha='17/11/2026 09:00', usuario='Cliente A', monto='70.00', banco='pichincha',
                estado='retirado', asignado_a='guillermo', historial=['[17/11/2026 09:00] Creado'],
                imagen='foto.jpg', imagen_fallo=None, liquidado=False)
    data.update(kwargs)
    return data


class InformesDiariosTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.uploads = self.root / 'uploads'
        self.uploads.mkdir()
        (self.uploads / 'foto.jpg').write_bytes(b'imagen-de-prueba')
        self.rows = [fila(), fila(2, estado='fallido', monto='30.00'),
                     fila(3, estado='activo', monto='10.00'), fila(4, estado='papelera', monto='500.00'),
                     fila(5, fecha='16/11/2026 10:00', monto='50.00', historial=['[17/11/2026 12:00] Retirado']),
                     fila(6, es_prueba=True, monto='999.00'), fila(7, estado='saldado', monto='20.00')]
        self.now = datetime(2026, 11, 17, 23, 59, tzinfo=reportes.ECUADOR)
        self.source = Mock(side_effect=lambda: (copy.deepcopy(self.rows), []))
        self.transport = Mock()
        self.transport.enviar.side_effect = range(100, 300)

    def emitter(self, transport=None):
        return reportes.Emisor(self.root / 'informes', '', ['1', '2'], '2026-11-17', self.uploads,
                                self.source, self.transport if transport is None else transport)

    def datos(self):
        return reportes.seleccionar_datos(copy.deepcopy(self.rows), [], self.now.date(), self.now)

    def test_estados_importes_y_movimientos_anteriores_no_se_mezclan(self):
        datos = self.datos()
        stats = reportes.totales(datos)
        self.assertEqual(sum(r['cantidad'] for r in stats.values()), 4)
        self.assertEqual(stats['retirado']['cantidad'], 1)
        self.assertEqual(str(stats['retirado']['monto']), '70.00')
        self.assertEqual(stats['fallido']['cantidad'], 1)
        self.assertEqual(stats['activo']['cantidad'], 1)
        self.assertNotIn('papelera', stats)
        self.assertNotIn(6, [r['id'] for r in datos['registros']])
        self.assertEqual(next(r for r in datos['registros'] if r['id'] == 5)['seccion'], 'Movimientos de retiros anteriores')

    def test_paquete_incluye_todas_las_imagenes_y_csv_sin_formulas(self):
        (self.uploads / 'fallo.jpg').write_bytes(b'evidencia-fallo')
        self.rows[0].update(usuario='=HYPERLINK("x")<script>no</script>', imagen='foto.jpg,fallo.jpg')
        datos = self.datos()
        manifest = reportes.construir_paquete(datos, self.uploads, self.root / 'salida')
        self.assertEqual(datos['archivos_no_incluidos'], [])
        self.assertIn("'=HYPERLINK", (self.root / 'salida/DETALLE.csv').read_text(encoding='utf-8-sig'))
        self.assertNotIn('<script>no</script>', (self.root / 'salida/LEER_INFORME.html').read_text(encoding='utf-8'))
        archivo = self.root / 'salida' / manifest['archivos'][-1]['nombre']
        with zipfile.ZipFile(archivo) as z:
            self.assertIn('comprobantes/1/foto.jpg', z.namelist())
            self.assertIn('comprobantes/1/fallo.jpg', z.namelist())
            self.assertEqual(z.read('comprobantes/1/fallo.jpg'), b'evidencia-fallo')

    def test_rutas_fuera_de_uploads_y_archivos_ausentes_se_advierten(self):
        (self.root / 'secreto.txt').write_text('no-incluir')
        self.rows[0]['imagen'] = '../secreto.txt,no-existe.jpg'
        datos = self.datos()
        reportes.construir_paquete(datos, self.uploads, self.root / 'salida')
        self.assertEqual(len(datos['archivos_no_incluidos']), 2)
        with zipfile.ZipFile(next((self.root / 'salida').glob('*.zip'))) as z:
            self.assertFalse(any('secreto' in n for n in z.namelist()))

    def test_montos_invalidos_no_se_convierten_en_cobros(self):
        for monto in ('NaN', 'Infinity', 'texto'):
            self.rows[0]['monto'] = monto
            datos = self.datos()
            self.assertEqual(reportes.totales(datos)['retirado']['monto'], 0)
            self.assertIn('montos inválidos', reportes.resumen(datos))

    def test_2359_hora_ecuador_y_no_repite_tras_reinicio(self):
        emitter = self.emitter()
        emitter.tick(self.now.replace(hour=23, minute=58))
        self.transport.enviar.assert_not_called()
        emitter.tick(self.now.astimezone(reportes.timezone.utc))
        self.assertEqual(self.transport.enviar.call_count, 8)
        self.emitter().tick(self.now.replace(day=18, hour=10))
        self.assertEqual(self.transport.enviar.call_count, 8)
        self.source.assert_called_once()

    def test_domingo_sin_movimientos_envia_informe_vacio(self):
        emitter = self.emitter()
        emitter.inicio = reportes.date(2026, 11, 22)
        emitter.tick(self.now.replace(day=22))
        self.assertIn('Sin retiros ingresados', self.transport.enviar.call_args_list[0].kwargs['texto'])

    def test_un_destinatario_bloqueado_no_impide_el_otro(self):
        def enviar(chat, **kwargs):
            if chat == '1':
                raise reportes.ErrorTelegram('Telegram rechazó la operación (código 403)')
            return 123
        self.transport.enviar.side_effect = enviar
        emitter = self.emitter()
        emitter.tick(self.now)
        with emitter.db() as db:
            rows = db.execute('SELECT chat, estado, COUNT(*) FROM entregas GROUP BY chat, estado').fetchall()
        self.assertIn(('1', 'error', 4), rows)
        self.assertIn(('2', 'enviado', 4), rows)

    def test_resultado_incierto_no_se_reenvia_a_ciegas(self):
        self.transport.enviar.side_effect = reportes.ErrorTelegram('respuesta incierta', True)
        emitter = self.emitter()
        emitter.tick(self.now)
        self.assertEqual(self.transport.enviar.call_count, 8)
        self.emitter().tick(self.now)
        self.assertEqual(self.transport.enviar.call_count, 8)

    def test_archivo_modificado_no_se_envia(self):
        emitter = self.emitter()
        self.transport.enviar.side_effect = reportes.ErrorTelegram('bloqueado')
        emitter.tick(self.now)
        (emitter.raiz / '2026-11-17/DETALLE.csv').write_text('alterado')
        with self.assertRaisesRegex(ValueError, 'cambió'):
            emitter.tick(self.now)

    def test_bloqueo_evitar_dos_emisores_simultaneos(self):
        emitter = self.emitter()
        with reportes.bloqueo_proceso(emitter.raiz / '.emisor.lock') as acquired:
            self.assertTrue(acquired)
            emitter.tick(self.now)
        self.transport.enviar.assert_not_called()

    def test_errores_de_red_no_exponen_token(self):
        bot = reportes.Telegram('12345:secreto_de_prueba')
        with patch.object(reportes.requests, 'post', side_effect=reportes.requests.ReadTimeout('https://api.telegram.org/bot12345:secreto_de_prueba/sendMessage')):
            with self.assertRaises(reportes.ErrorTelegram) as ex:
                bot.enviar('1', texto='prueba')
            self.assertTrue(ex.exception.incierto)
            self.assertNotIn('secreto', str(ex.exception))

    def test_limpieza_no_borra_originales_ni_reenvia_informes(self):
        emitter = self.emitter()
        emitter.tick(self.now)
        manifest = emitter.raiz / '2026-11-17/manifest.json'
        os.utime(manifest, (self.now.timestamp(), self.now.timestamp()))
        emitter.limpiar_adjuntos_entregados(self.now.replace(day=25))
        self.assertFalse(list(manifest.parent.glob('*.zip')))
        self.assertTrue((self.uploads / 'foto.jpg').exists())
        self.assertTrue((manifest.parent / 'datos.json').exists())
        emitter.procesar_dia(self.now.date(), self.now)
        self.assertEqual(self.transport.enviar.call_count, 8)

    def test_no_llena_el_disco_si_no_hay_espacio(self):
        with patch.object(reportes.shutil, 'disk_usage', return_value=Mock(free=10_000_000)):
            with self.assertRaises(OSError):
                reportes.construir_paquete(self.datos(), self.uploads, self.root / 'salida')


if __name__ == '__main__':
    unittest.main()
