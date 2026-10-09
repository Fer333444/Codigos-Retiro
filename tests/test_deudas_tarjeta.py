"""Deudas vigentes de cada cobrador en /admin, con datos temporales."""
import copy
from datetime import datetime
from pathlib import Path
import re
import unittest
from unittest.mock import patch

import test_fichas_clientes as base


def setUpModule():
    base.setUpModule()


def tearDownModule():
    base.tearDownModule()


def registro(rid, usuario, **cambios):
    datos = dict(base.registro(rid, usuario), asignado_a='guillermo', estado='fallido')
    datos.update(cambios)
    return datos


class DeudasTarjetaTest(unittest.TestCase):
    login = base.AplicacionTest.login

    def setUp(self):
        base.AplicacionTest.setUp(self)
        self.erp = base.erp
        self.erp.usuarios_db.update({
            'guillermo': dict(rol='recaudador', nombre='Willy', estado='Activo',
                              permisos=['procesar_retiros', 'ver_retiros']),
            'jenny': dict(rol='cobrador', nombre='Jenny', estado='Activo', permisos=[]),
        })
        reloj = patch.object(self.erp, 'hora_ecuador', return_value=datetime(2026, 10, 9, 15, 0))
        reloj.start()
        self.addCleanup(reloj.stop)

    def usar_registros(self, registros):
        self.erp.registros = registros
        self.erp.guardar_datos()

    def contexto(self, url='/admin'):
        with patch.object(self.erp, 'render_template', return_value='tarjetas') as render:
            respuesta = self.client.get(url)
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(render.call_args.args[0], 'admin.html')
        return render.call_args.kwargs

    def ids(self, contexto, cobrador='guillermo'):
        return [r['id'] for r in contexto['stats_cobradores'][cobrador]['fallidos']]

    def test_deuda_auditada_de_danny_sigue_en_tarjeta_willy(self):
        self.usar_registros([registro(1790896495163, 'DannyH12', monto='10.00',
                                     fecha='01/10/2026 18:14', liquidado=True,
                                     historial=['[06/10/2026 11:19] Auditado y cerrado por Guillermo.'])])
        contexto = self.contexto()
        self.assertEqual(self.ids(contexto), [1790896495163])
        tarjeta = next(c for c in contexto['cobradores'] if c['username'] == 'guillermo')
        self.assertEqual(tarjeta['nombre_mostrar'], 'Willy')
        self.assertEqual(contexto['stats_cobradores']['guillermo']['total_acumulado'], 0)

    def test_caidos_y_revision_auditados_o_no_siguen_visibles(self):
        self.usar_registros([
            registro(1, 'uno', estado='fallido', liquidado=False),
            registro(2, 'dos', estado='fallido', liquidado=True),
            registro(3, 'tres', estado='fallido_revision', liquidado=False),
            registro(4, 'cuatro', estado='fallido_revision', liquidado=True),
        ])
        self.assertEqual(self.ids(self.contexto()), [1, 2, 3, 4])

    def test_vencidos_antiguos_y_auditados_no_desaparecen_al_cambiar_dia(self):
        self.usar_registros([
            registro(1, 'hoy', estado='expirado', fecha='09/10/2026 08:00'),
            registro(2, 'ayer', estado='expirado', fecha='08/10/2026 08:00'),
            registro(3, 'antiguo', estado='expirado', fecha='01/09/2026 08:00', liquidado=True),
        ])
        self.assertEqual(self.ids(self.contexto()), [1, 2, 3])

    def test_no_incluye_cruces_saldados_fusionados_papelera_ni_exitosos(self):
        estados = ['pendiente_de_cruce', 'saldado', 'fusionado', 'papelera', 'retirado', 'activo']
        self.usar_registros([registro(1, 'deuda')] + [
            registro(i + 2, estado, estado=estado) for i, estado in enumerate(estados)
        ])
        self.assertEqual(self.ids(self.contexto()), [1])

    def test_cada_tarjeta_usa_login_exacto_no_nombre_mostrar(self):
        self.usar_registros([
            registro(1, 'cliente-guillermo'),
            registro(2, 'cliente-jenny', asignado_a='jenny', liquidado=True),
            registro(3, 'sin-asignar', asignado_a=None),
            registro(4, 'nombre-visible', asignado_a='Willy'),
            registro(5, 'otro-login', asignado_a='Guillermo'),
            registro(6, 'rol-sin-cobro', asignado_a='reportes'),
        ])
        contexto = self.contexto()
        self.assertEqual(self.ids(contexto), [1])
        self.assertEqual(self.ids(contexto, 'jenny'), [2])
        self.assertNotIn('reportes', contexto['stats_cobradores'])

    def test_efectivo_y_asignados_conservan_sus_reglas(self):
        self.usar_registros([
            registro(1, 'pago-hoy', estado='retirado', monto='50.00'),
            registro(2, 'pago-ayer', estado='retirado', monto='30.00', fecha='08/10/2026 09:00'),
            registro(3, 'pago-liquidado', estado='retirado', monto='200.00', liquidado=True),
            registro(4, 'caido-auditado', monto='10.00', liquidado=True),
            registro(5, 'vencido', estado='expirado', monto='80.00', fecha='01/10/2026 09:00'),
            registro(6, 'pendiente', estado='activo', monto='20.00'),
            registro(7, 'pendiente-visto', estado='activo', monto='15.00', visto_por_cobrador=True),
        ])
        stats = self.contexto()['stats_cobradores']['guillermo']
        self.assertEqual((stats['total_dia'], stats['total_acumulado']), (50, 80))
        self.assertEqual(stats['desglose_fechas'], {'09/10/2026': 50, '08/10/2026': 30})
        self.assertEqual((stats['asignados_count'], stats['asignados_valor']), (2, 35))
        self.assertEqual([r['id'] for r in stats['fallidos']], [4, 5])

    def test_produccion_excluye_registros_de_prueba(self):
        self.usar_registros([
            registro(1, 'real', liquidado=True),
            registro(2, 'prueba-caido', es_prueba=True, liquidado=True),
            registro(3, 'prueba-vencido', estado='expirado', es_prueba=True),
            registro(4, 'prueba-efectivo', estado='retirado', es_prueba=True),
            registro(5, 'prueba-asignado', estado='activo', es_prueba=True),
        ])
        contexto = self.contexto()
        self.assertEqual(self.ids(contexto), [1])
        stats = contexto['stats_cobradores']['guillermo']
        self.assertEqual((stats['total_acumulado'], stats['asignados_count']), (0, 0))

    def test_simulador_incluye_sus_pruebas_sin_traer_deudas_reales(self):
        self.usar_registros([registro(1, 'real')])
        pruebas = [registro(2, 'prueba-vencido', estado='expirado', fecha='01/10/2026 09:00',
                            es_prueba=True, liquidado=True)]
        self.login('supremo', entorno='pruebas')
        with patch.object(self.erp, 'registros_pruebas', pruebas), \
                patch.object(self.erp, 'usuarios_pruebas', copy.deepcopy(self.erp.usuarios_db)), \
                patch.object(self.erp, 'cobradores_pruebas', {}):
            contexto = self.contexto('/pruebas/admin')
        self.assertEqual(self.ids(contexto), [2])
        self.assertTrue(contexto['entorno_staging'])

    def test_ver_tarjetas_no_modifica_deudas_pagos_ni_archivo(self):
        self.usar_registros([registro(1, 'auditado', liquidado=True),
                             registro(2, 'vencido', estado='expirado', fecha='01/10/2026 09:00')])
        antes = copy.deepcopy((self.erp.registros, self.erp.historial_pagos, self.erp.enlaces_db))
        archivo_antes = Path(self.erp.DATA_FILE).read_bytes()
        with patch.object(self.erp, 'guardar_datos') as guardar:
            self.contexto()
        guardar.assert_not_called()
        self.assertEqual((self.erp.registros, self.erp.historial_pagos, self.erp.enlaces_db), antes)
        self.assertEqual(Path(self.erp.DATA_FILE).read_bytes(), archivo_antes)

    def test_html_muestra_cliente_en_tarjeta_del_cobrador(self):
        self.usar_registros([registro(1790896495163, 'DannyH12', monto='10.00',
                                     fecha='01/10/2026 18:14', liquidado=True),
                             registro(2, 'cliente-jenny', asignado_a='jenny')])
        for rol in ['supremo', 'guillermo']:
            with self.subTest(rol=rol):
                self.login(rol)
                respuesta = self.client.get('/admin')
                self.assertEqual(respuesta.status_code, 200)
                texto = respuesta.get_data(as_text=True)
                tarjetas = re.split(r'data-tarjeta-cobrador="([^"]+)"', texto)
                tarjeta_guillermo = tarjetas[tarjetas.index('guillermo') + 1]
                tarjeta_jenny = tarjetas[tarjetas.index('jenny') + 1]
                self.assertIn('data-deuda-id="1790896495163"', tarjeta_guillermo)
                self.assertIn('DannyH12', tarjeta_guillermo)
                self.assertIn('$10.00', tarjeta_guillermo)
                self.assertIn('Deudas / Caídos (1)', tarjeta_guillermo)
                self.assertNotIn('data-deuda-id="2"', tarjeta_guillermo)
                self.assertIn('data-deuda-id="2"', tarjeta_jenny)
                self.assertNotIn('data-deuda-id="1790896495163"', tarjeta_jenny)


if __name__ == '__main__':
    unittest.main()
