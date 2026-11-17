"""Horarios y entradas de clientes, sin iniciar el ERP ni tocar datos o servicios."""
import ast
import copy
from datetime import datetime, timedelta
from pathlib import Path
import unittest
from unittest.mock import Mock

from flask import Flask, flash, jsonify, redirect, render_template, request, session, url_for

ROOT = Path(__file__).resolve().parents[1]
MENSAJE_CERRADO = ('En este momento nuestro sistema de recepción de códigos se encuentra cerrado. '
                   'Por favor, inténtalo más tarde durante nuestro horario de atención.')


class HorarioAtencionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tree = ast.parse((ROOT / 'app.py').read_text(encoding='utf-8-sig'))
        names = {'hora_ecuador', 'dentro_horario_atencion', 'recepcion_codigos_abierta',
                 'retiro', 'retiro_grupo', 'widget_retiro', 'widget_retiro_pruebas',
                 'vista_widget_retiro', 'recibir_ticket_socio', 'toggle_horario'}
        cls.code = compile(ast.Module(body=[node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name in names], type_ignores=[]),
            str(ROOT / 'app.py'), 'exec')

    def setUp(self):
        self.app = Flask(__name__, template_folder=str(ROOT / 'templates'))
        self.app.config.update(TESTING=True, SECRET_KEY='horario-solo-pruebas')
        self.app.add_url_rule('/', endpoint='index', view_func=lambda: 'personal')
        self.app.add_url_rule('/login', endpoint='login', view_func=lambda: 'login')
        self.clock = Mock(return_value=datetime(2026, 11, 16, 12))
        self.process = Mock(return_value='procesado')
        self.save = Mock(return_value=True)
        self.insert = Mock(return_value=('TRX-PRUEBA', None))
        self.download = Mock(return_value='prueba.png')
        self.config = {'horario_activo': True, 'bancos_activos': {'pichincha': True}}
        self.env = dict(app=self.app, datetime=datetime, timedelta=timedelta,
                        request=request, session=session, url_for=url_for,
                        render_template=render_template, redirect=redirect, flash=flash,
                        jsonify=jsonify, sistema_config=self.config,
                        enlaces_db={'ana': {'usuario': 'Ana', 'grupo': 'Clientes'}},
                        grupos_creados=['Clientes'], es_entorno_staging=lambda: False,
                        procesar_formulario_retiro=self.process, guardar_datos=self.save,
                        db_registros=Mock(return_value=[]), insertar_registro_retiro=self.insert,
                        descargar_imagen_desde_url=self.download)
        exec(self.code, self.env)
        self.original_clock = self.env['hora_ecuador']
        self.env['hora_ecuador'] = self.clock
        self.client = self.app.test_client()

    def test_limites_exactos_todos_los_dias(self):
        monday = datetime(2026, 11, 16)
        for day in range(7):
            date = monday + timedelta(days=day)
            closing = 18 if day == 5 else 20
            cases = [(0, 0, 0, False), (8, 59, 59, False), (9, 0, 0, day != 6),
                     (closing - 1, 59, 59, day != 6), (closing, 0, 0, False),
                     (23, 59, 59, False)]
            for hour, minute, second, expected in cases:
                now = date.replace(hour=hour, minute=minute, second=second)
                with self.subTest(now=now):
                    self.assertEqual(self.env['recepcion_codigos_abierta'](now), expected)
        self.save.assert_not_called()

    def test_hora_ecuador_independiente_del_reloj_local_del_servidor(self):
        self.env['datetime'] = Mock()
        self.env['datetime'].utcnow.return_value = datetime(2026, 11, 23, 14)
        self.assertEqual(self.original_clock(), datetime(2026, 11, 23, 9))
        self.env['datetime'].utcnow.return_value = datetime(2026, 11, 23, 4, 59)
        self.assertEqual(self.original_clock(), datetime(2026, 11, 22, 23, 59))

    def test_bandera_antigua_no_impide_apertura_automatica_tras_reinicio(self):
        self.config['horario_activo'] = False
        for config in (copy.deepcopy(self.config), {'horario_activo': False,
                        'horario_cierre_manual_fecha': '2026-11-21'}):
            self.env['sistema_config'] = config
            self.assertFalse(self.env['recepcion_codigos_abierta'](datetime(2026, 11, 22, 12)))
            self.assertFalse(self.env['recepcion_codigos_abierta'](datetime(2026, 11, 23, 8, 59)))
            self.assertTrue(self.env['recepcion_codigos_abierta'](datetime(2026, 11, 23, 9)))
        self.save.assert_not_called()

    def test_formularios_cerrados_conservan_mensaje_y_bloquean_envio_tardio(self):
        for path in ('/retiro/ana', '/retiro_grupo/Clientes', '/widget_retiro?token=ana'):
            with self.subTest(path=path):
                self.clock.return_value = datetime(2026, 11, 16, 19, 59, 59)
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.clock.return_value = datetime(2026, 11, 16, 20)
                for response in (self.client.get(path), self.client.post(path, data={
                        'banco': 'pichincha', 'monto': '70.00', 'usuarios_magis': 'Ana'})):
                    self.assertEqual(response.status_code, 403 if response.request.method == 'POST' else 200)
                    expected = ('Sistema fuera de horario. Inténtalo más tarde.'
                                if 'widget' in path else MENSAJE_CERRADO)
                    self.assertIn(expected, response.get_data(as_text=True))
        self.process.assert_not_called()
        self.insert.assert_not_called()
        self.download.assert_not_called()
        self.save.assert_not_called()

    def test_lunes_nueve_reabre_todas_las_entradas_y_respeta_bancos(self):
        self.clock.return_value = datetime(2026, 11, 23, 9)
        for path in ('/retiro/ana', '/retiro_grupo/Clientes', '/widget_retiro?token=ana'):
            with self.subTest(path=path):
                self.process.reset_mock()
                response = self.client.post(path, data={'banco': 'pichincha', 'usuarios_magis': 'Ana'})
                self.assertEqual(response.status_code, 200)
                self.process.assert_called_once()
        self.config['bancos_activos']['pichincha'] = False
        self.process.reset_mock()
        for path in ('/retiro/ana', '/retiro_grupo/Clientes', '/widget_retiro?token=ana'):
            self.assertEqual(self.client.post(path, data={'banco': 'pichincha'}).status_code, 403)
        self.process.assert_not_called()

    def test_api_socio_no_descarga_ni_inserta_fuera_de_horario(self):
        data = {'cfo_ticket_id': 'prueba-1', 'banco': 'pichincha', 'monto': '70',
                'usuario': 'Ana', 'url_imagen': 'https://example.invalid/prueba.png'}
        self.clock.return_value = datetime(2026, 11, 22, 12)
        response = self.client.post('/api/v1/recibir_ticket_socio', json=data)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json, {'error': 'Sistema fuera de horario'})
        self.download.assert_not_called()
        self.insert.assert_not_called()
        self.clock.return_value = datetime(2026, 11, 23, 9)
        self.assertEqual(self.client.post('/api/v1/recibir_ticket_socio', json=data).status_code, 200)
        self.insert.assert_called_once()

    def login(self, role='supremo'):
        with self.client.session_transaction() as state:
            state.update(usuario='admin-prueba', rol=role, permisos=[])

    def test_cierre_manual_solo_dura_ese_dia_y_puede_reabrirse(self):
        self.login()
        self.client.post('/toggle_horario')
        self.assertFalse(self.env['recepcion_codigos_abierta']())
        self.assertEqual(self.config['horario_cierre_manual_fecha'], '2026-11-16')
        self.env['sistema_config'] = copy.deepcopy(self.config)
        self.assertFalse(self.env['recepcion_codigos_abierta']())
        self.assertTrue(self.env['recepcion_codigos_abierta'](datetime(2026, 11, 17, 9)))
        self.client.post('/toggle_horario')
        self.assertTrue(self.env['recepcion_codigos_abierta']())

    def test_interruptor_no_abre_fuera_de_horario_ni_sin_permiso(self):
        self.login()
        before = copy.deepcopy(self.config)
        self.clock.return_value = datetime(2026, 11, 21, 18)
        self.client.post('/toggle_horario')
        self.assertFalse(self.env['recepcion_codigos_abierta']())
        self.login('cobrador')
        self.clock.return_value = datetime(2026, 11, 23, 12)
        response = self.client.post('/toggle_horario')
        self.assertTrue(response.location.endswith('/login'))
        self.assertEqual(self.config, before)
        self.save.assert_not_called()

    def test_guardado_fallido_no_simula_cierre_exitoso(self):
        self.login()
        before = copy.deepcopy(self.config)
        self.save.return_value = False
        self.client.post('/toggle_horario')
        self.assertEqual(self.config, before)
        self.assertTrue(self.env['recepcion_codigos_abierta']())
        with self.client.session_transaction() as state:
            self.assertIn('No se pudo guardar', state['_flashes'][-1][1])


if __name__ == '__main__':
    unittest.main()
