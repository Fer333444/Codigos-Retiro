"""Vencimientos y tarjetas de asignados, siempre con datos temporales."""
import copy
from datetime import datetime, timedelta, timezone
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
    datos = dict(base.registro(rid, usuario), asignado_a='guillermo', estado='activo')
    datos.update(cambios)
    return datos


def instante(fecha):
    return datetime.strptime(fecha, '%d/%m/%Y %H:%M').replace(
        tzinfo=timezone(timedelta(hours=-5))).timestamp()


class ContadoresAsignadosTest(unittest.TestCase):
    login = base.AplicacionTest.login

    def setUp(self):
        base.AplicacionTest.setUp(self)
        self.erp = base.erp
        self.erp.usuarios_db.update({
            'guillermo': dict(rol='recaudador', nombre='Willy', estado='Activo',
                              permisos=['procesar_retiros', 'ver_retiros']),
            'jenny': dict(rol='cobrador', nombre='Jenny', estado='Activo', permisos=[]),
        })
        reloj = patch.object(self.erp, 'hora_ecuador', return_value=datetime(2026, 10, 12, 12, 0))
        reloj.start()
        self.addCleanup(reloj.stop)

    def usar_registros(self, registros):
        self.erp.registros = registros
        self.erp.guardar_datos()

    def contexto(self, url='/admin'):
        with patch.object(self.erp, 'render_template', return_value='tarjetas') as render:
            respuesta = self.client.get(url)
        self.assertEqual(respuesta.status_code, 200)
        return render.call_args.kwargs

    def asignados(self, contexto, cobrador='guillermo'):
        return contexto['stats_cobradores'][cobrador]['asignados']

    def test_solo_activos_del_login_exacto_en_su_tarjeta(self):
        self.usar_registros([
            registro(1, 'cliente-willy', monto='50.00'),
            registro(2, 'cliente-jenny', asignado_a='jenny'),
            registro(3, 'sin-asignar', asignado_a=None),
            registro(4, 'nombre-visible', asignado_a='Willy'),
            registro(5, 'otro-login', asignado_a='Guillermo'),
        ] + [registro(i + 6, estado, estado=estado) for i, estado in enumerate(
            ['retirado', 'expirado', 'fallido', 'fallido_revision', 'pendiente_de_cruce', 'papelera'])])
        contexto = self.contexto()
        self.assertEqual([r['id'] for r in self.asignados(contexto)], [1])
        self.assertEqual([r['id'] for r in self.asignados(contexto, 'jenny')], [2])
        self.assertEqual(contexto['stats_cobradores']['guillermo']['asignados_count'], 1)
        self.assertEqual(contexto['stats_cobradores']['guillermo']['asignados_valor'], 50)

    def test_ordena_por_vencimiento_real_y_pone_sin_fecha_al_final(self):
        temprano = instante('12/10/2026 12:05')
        tarde = instante('12/10/2026 13:00')
        self.usar_registros([
            registro(1, 'sin-fecha'),
            registro(2, 'tarde', expira_timestamp=str(tarde)),
            registro(3, 'urgente', expira_timestamp=temprano, monto='50.00', banco='guayaquil'),
            registro(4, 'hora-antigua', fecha='12/10/2026 11:00', hora_limite='12:20'),
        ])
        asignados = self.asignados(self.contexto())
        self.assertEqual([r['id'] for r in asignados], [3, 4, 2, 1])
        self.assertEqual([r['vence_en'] for r in asignados], [
            temprano, instante('12/10/2026 12:20'), tarde, None])
        self.assertEqual((asignados[0]['usuario'], asignados[0]['monto'], asignados[0]['banco']),
                         ('urgente', '50.00', 'guayaquil'))

    def test_timestamp_tiene_prioridad_y_no_se_reinicia_al_reasignar_o_recargar(self):
        vence = instante('12/10/2026 13:00')
        self.usar_registros([registro(1, 'cliente', expira_timestamp=str(vence),
                                     fecha='12/10/2026 11:00', hora_limite='12:15')])
        self.assertEqual(self.asignados(self.contexto())[0]['vence_en'], vence)
        # Simula una asignación nueva sin ejecutar operaciones sobre dinero.
        self.erp.registros[0]['asignado_a'] = 'jenny'
        self.erp.registros[0]['fecha_asignacion'] = '12/10/2026 12:45'
        self.erp.registros[0]['historial'].append('Reasignado a Jenny')
        for _ in range(2):
            contexto = self.contexto()
            self.assertEqual(self.asignados(contexto), [])
            self.assertEqual(self.asignados(contexto, 'jenny')[0]['vence_en'], vence)
        self.assertEqual(self.erp.registros[0]['expira_timestamp'], str(vence))

    def test_timestamp_invalido_sin_legacy_no_inventa_vencimiento(self):
        for valor in [None, '', 'malo', 'NaN', 'Infinity', float('nan'), float('inf'), 0, -1]:
            with self.subTest(valor=valor):
                datos = registro(1, 'cliente', expira_timestamp=valor)
                self.assertIsNone(self.erp.vencimiento_codigo_asignado(datos))

    def test_timestamp_nulo_o_invalido_aprovecha_fecha_legacy_valida(self):
        for valor in [None, '', 'malo', 'NaN', 'Infinity', 0, -1]:
            with self.subTest(valor=valor):
                datos = registro(1, 'cliente', expira_timestamp=valor,
                                 fecha='12/10/2026 23:00', hora_limite='01:00')
                self.assertEqual(self.erp.vencimiento_codigo_asignado(datos),
                                 instante('13/10/2026 01:00'))

    def test_legacy_respeta_hora_ecuador_y_regla_de_cambio_de_dia(self):
        for creado, limite, esperado in [
            ('12/10/2026 11:00', '13:15', '12/10/2026 13:15'),
            ('12/10/2026 23:30', '01:00', '13/10/2026 01:00'),
            ('12/10/2026 12:30', '11:00', '12/10/2026 11:00'),
            ('12/10/2026 13:30', '01:00', '12/10/2026 01:00'),
        ]:
            with self.subTest(creado=creado, limite=limite):
                datos = registro(1, 'cliente', fecha=creado, hora_limite=limite)
                self.assertEqual(self.erp.vencimiento_codigo_asignado(datos), instante(esperado))

    def test_legacy_incompleto_o_invalido_se_muestra_sin_vencimiento(self):
        for campos in [{}, {'hora_limite': None}, {'hora_limite': ''},
                       {'hora_limite': '25:00'}, {'hora_limite': '12:75'},
                       {'hora_limite': '12:00', 'fecha': 'no es fecha'},
                       {'hora_limite': '12:00', 'fecha': None}]:
            with self.subTest(campos=campos):
                self.assertIsNone(self.erp.vencimiento_codigo_asignado(registro(1, 'cliente', **campos)))

    def test_produccion_excluye_pruebas_y_simulador_usa_sus_registros(self):
        self.usar_registros([registro(1, 'real'), registro(2, 'prueba', es_prueba=True)])
        self.assertEqual([r['id'] for r in self.asignados(self.contexto())], [1])
        self.login('supremo', entorno='pruebas')
        pruebas = [registro(3, 'simulado', es_prueba=True)]
        with patch.object(self.erp, 'registros_pruebas', pruebas), \
                patch.object(self.erp, 'usuarios_pruebas', copy.deepcopy(self.erp.usuarios_db)), \
                patch.object(self.erp, 'cobradores_pruebas', {}):
            contexto = self.contexto('/pruebas/admin')
        self.assertEqual([r['id'] for r in self.asignados(contexto)], [3])

    def test_consulta_no_muta_registros_finanzas_ni_archivo_y_usa_reloj_servidor(self):
        self.usar_registros([
            registro(1, 'con-fecha', expira_timestamp=instante('12/10/2026 12:30')),
            registro(2, 'legacy', hora_limite='14:00'),
            registro(3, 'sin-fecha'),
            registro(4, 'cobrado', estado='retirado', monto='120.00'),
        ])
        antes = copy.deepcopy((self.erp.registros, self.erp.historial_pagos, self.erp.enlaces_db))
        archivo = Path(self.erp.DATA_FILE).read_bytes()
        ahora = instante('12/10/2026 12:00')
        with patch.object(self.erp, 'guardar_datos') as guardar, \
                patch.object(self.erp.time, 'time', return_value=ahora):
            self.login('supremo')
            contexto = self.contexto()
        self.assertEqual(contexto['ahora_servidor'], ahora)
        self.assertEqual(contexto['stats_cobradores']['guillermo']['total_acumulado'], 120)
        guardar.assert_not_called()
        self.assertEqual((self.erp.registros, self.erp.historial_pagos, self.erp.enlaces_db), antes)
        self.assertEqual(Path(self.erp.DATA_FILE).read_bytes(), archivo)

    def test_mantenimiento_acepta_timestamp_nulo_y_conserva_expiracion_legacy(self):
        self.usar_registros([
            registro(1, 'legacy-futuro', expira_timestamp=None,
                     fecha='12/10/2026 11:00', hora_limite='12:05'),
            registro(2, 'sin-fecha', expira_timestamp=None, fecha='12/10/2026 11:00'),
            registro(3, 'legacy-vencido', expira_timestamp=None,
                     fecha='12/10/2026 11:00', hora_limite='11:30'),
            registro(4, 'timestamp-texto', expira_timestamp=str(instante('12/10/2026 13:00'))),
        ])
        hooks = self.erp.app.before_request_funcs[None] + [self.erp.mantenimiento_datos]
        with patch.dict(self.erp.app.before_request_funcs, {None: hooks}), \
                patch.object(self.erp, 'realizar_respaldo_diario'), \
                patch.object(self.erp, 'disparar_alerta_push') as push, \
                patch.object(self.erp, 'disparar_alerta_telegram') as telegram, \
                patch.object(self.erp, 'guardar_datos'), \
                patch.object(self.erp.time, 'time', return_value=instante('12/10/2026 12:00')):
            self.login('supremo')
            respuesta = self.client.get('/admin')
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual([r['estado'] for r in self.erp.registros],
                         ['activo', 'activo', 'expirado', 'activo'])
        self.assertIn('Expirado automáticamente', self.erp.registros[2]['historial'][-1])
        push.assert_not_called()
        telegram.assert_not_called()

    def test_html_lista_valor_y_vencimiento_arriba_de_deudas_y_muestra_vacio(self):
        self.usar_registros([
            registro(1, 'Cliente asignado', expira_timestamp=instante('12/10/2026 12:30'), monto='50.00'),
            registro(2, 'Cliente sin fecha'),
            registro(3, 'Cliente deuda', estado='fallido'),
        ])
        respuesta = self.client.get('/admin')
        self.assertEqual(respuesta.status_code, 200)
        tarjetas = re.split(r'data-tarjeta-cobrador="([^"]+)"', respuesta.get_data(as_text=True))
        willy = tarjetas[tarjetas.index('guillermo') + 1]
        jenny = tarjetas[tarjetas.index('jenny') + 1]
        self.assertIn('data-asignado-id="1"', willy)
        self.assertIn('Cliente asignado', willy)
        self.assertIn('$50.00', willy)
        self.assertIn('data-vence-en=', willy)
        self.assertIn('Sin vencimiento registrado', willy)
        self.assertLess(willy.index('data-asignado-id="1"'), willy.index('Deudas / Caídos'))
        self.assertNotIn('data-asignado-id="3"', willy)
        self.assertNotIn('data-asignado-id="1"', jenny)
        self.assertIn('Sin códigos asignados', jenny)


if __name__ == '__main__':
    unittest.main()
