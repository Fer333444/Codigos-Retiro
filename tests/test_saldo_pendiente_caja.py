"""El efectivo pendiente permanece hasta confirmar su recepción, con datos temporales."""
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
    datos = dict(base.registro(rid, usuario), asignado_a='guillermo')
    datos.update(cambios)
    return datos


class SaldoPendienteCajaTest(unittest.TestCase):
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
        self.reloj = reloj.start()
        self.addCleanup(reloj.stop)
        self.erp.registros = [
            registro(1, 'cliente-ayer', monto='130.00', fecha='08/10/2026 10:00'),
            registro(2, 'cliente-hoy', monto='220.00'),
        ]
        self.erp.guardar_datos()

    def contexto(self):
        with patch.object(self.erp, 'render_template', return_value='tarjetas') as render:
            respuesta = self.client.get('/admin')
        self.assertEqual(respuesta.status_code, 200)
        return render.call_args.kwargs['stats_cobradores']

    def comprobar_tarjeta(self, importe, cobrador='guillermo'):
        respuesta = self.client.get('/admin')
        self.assertEqual(respuesta.status_code, 200)
        tarjetas = re.split(r'data-tarjeta-cobrador="([^"]+)"', respuesta.get_data(as_text=True))
        tarjeta = tarjetas[tarjetas.index(cobrador) + 1]
        saldo = re.search(r'data-saldo-pendiente(?:="[^"]*")?[^>]*>\s*\$([\d.]+)', tarjeta)
        self.assertIsNotNone(saldo, 'La tarjeta debe identificar y mostrar el saldo pendiente acumulado.')
        self.assertEqual(float(saldo.group(1)), importe)
        self.assertIn('Saldo pendiente por recibir', tarjeta)
        monto_boton = re.search(r'data-monto="([^"]+)"', tarjeta)
        self.assertIsNotNone(monto_boton)
        self.assertEqual(float(monto_boton.group(1)), importe)

    def recibir(self, monto):
        respuesta = self.client.post('/marcar_recibido', data={
            'cobrador': 'guillermo', 'monto_recibido': str(monto), 'metodo_pago': 'Efectivo',
        }, headers={'Referer': 'http://localhost/admin'})
        self.assertEqual(respuesta.status_code, 302)
        self.assertTrue(respuesta.location.endswith('/admin'))

    def test_ayer_y_hoy_permanecen_acumulados_al_cambiar_dia_sin_mutar_datos(self):
        antes = copy.deepcopy((self.erp.registros, self.erp.historial_pagos))
        archivo_antes = Path(self.erp.DATA_FILE).read_bytes()
        with patch.object(self.erp, 'guardar_datos') as guardar:
            stats = self.contexto()['guillermo']
            self.assertEqual((stats['total_dia'], stats['total_acumulado']), (220, 350))
            self.comprobar_tarjeta(350)
            self.reloj.return_value = datetime(2026, 10, 10, 9, 0)
            stats = self.contexto()['guillermo']
            self.assertEqual((stats['total_dia'], stats['total_acumulado']), (0, 350))
            self.comprobar_tarjeta(350)
        guardar.assert_not_called()
        self.assertEqual((self.erp.registros, self.erp.historial_pagos), antes)
        self.assertEqual(Path(self.erp.DATA_FILE).read_bytes(), archivo_antes)

    def test_recepcion_parcial_reduce_solo_lo_confirmado_y_persiste_saldo(self):
        self.recibir(180)
        self.assertEqual(self.contexto()['guillermo']['total_acumulado'], 170)
        self.comprobar_tarjeta(170)
        self.assertTrue(self.erp.registros[0]['liquidado'])
        self.assertFalse(self.erp.registros[1].get('liquidado', False))
        self.assertEqual(float(self.erp.registros[1]['monto']), 170)
        self.assertEqual(self.erp.historial_pagos[0]['monto'], '180.00')
        self.erp.registros = []
        self.erp.historial_pagos = []
        self.erp.cargar_datos()
        self.reloj.return_value = datetime(2026, 10, 12, 9, 0)
        self.assertEqual(self.contexto()['guillermo']['total_dia'], 0)
        self.comprobar_tarjeta(170)
        self.assertEqual(self.erp.historial_pagos[0]['monto'], '180.00')

    def test_recepcion_total_deja_cero_sin_incluir_liquidados_ni_otro_cobrador(self):
        liquidado = registro(3, 'entregado', monto='500.00', liquidado=True)
        otro = registro(4, 'cliente-jenny', monto='90.00', asignado_a='jenny')
        self.erp.registros.extend([liquidado, otro])
        self.erp.guardar_datos()
        intactos = copy.deepcopy([liquidado, otro])
        self.comprobar_tarjeta(350)
        self.recibir(350)
        self.erp.cargar_datos()
        self.reloj.return_value = datetime(2026, 10, 10, 9, 0)
        self.comprobar_tarjeta(0)
        self.comprobar_tarjeta(90, cobrador='jenny')
        self.assertEqual(self.erp.registros[2:], intactos)
        self.assertTrue(all(r['liquidado'] for r in self.erp.registros[:2]))
        self.assertEqual(self.erp.historial_pagos[0]['monto'], '350.00')


if __name__ == '__main__':
    unittest.main()
