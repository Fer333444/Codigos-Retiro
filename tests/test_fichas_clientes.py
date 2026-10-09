"""Pruebas con datos temporales; importa y ejecuta el proyecto real sin copiarlo.

Ejecutar desde la raíz: python -m unittest discover -s tests -v
"""
import copy
import html
import importlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker
from fichas_clientes import (AlmacenFichas, CambioFicha, ConflictoFicha,
                            PERMISO_FICHAS, crear_catalogo, identidad_cliente, identidades_nuevas)

ROOT = Path(__file__).resolve().parents[1]
erp = None


def setUpModule():
    global erp, entorno_importacion, cwd_original
    cwd_original = os.getcwd()
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    entorno_importacion = tempfile.TemporaryDirectory()
    os.chdir(entorno_importacion.name)
    try:
        with patch.dict(os.environ, {'DATABASE_URL': '', 'OPENAI_API_KEY': '', 'TELEGRAM_BOT_TOKEN': '',
                                     'VAPID_PRIVATE_KEY': '', 'VAPID_PUBLIC_KEY': ''}):
            erp = importlib.import_module('app')
    except Exception:
        os.chdir(cwd_original)
        entorno_importacion.cleanup()
        raise


def tearDownModule():
    os.chdir(cwd_original)
    entorno_importacion.cleanup()


def registro(rid, usuario, **extra):
    return dict(id=rid, usuario=usuario, fecha='09/10/2026 10:30', monto='25.00',
                banco='pichincha', estado='retirado', asignado_a='cobrador',
                celular='Pago Manual', cedula='', detalles={'codigo_pichincha': f'COD-{rid}'},
                historial=['Creado por Cliente'], imagen=None, **extra)


class IdentidadesTest(unittest.TestCase):
    def test_repetidos_coinciden_y_subcadenas_no_mezclan(self):
        regs = [registro(1, 'ana'), registro(2, 'ana'), registro(3, 'anabel'), registro(4, 'Ana')]
        original = copy.deepcopy(regs)
        perfiles, por, _ = crear_catalogo(regs, {})
        self.assertEqual(len(perfiles), 3)
        self.assertEqual(por['1'], por['2'])
        self.assertNotEqual(por['1'], por['3'])
        self.assertNotEqual(por['1'], por['4'])
        self.assertEqual(regs, original)

    def test_erp_separa_origenes_del_identificador_local(self):
        regs = [registro(1, 'ana'), registro(2, 'WIDGET - ana', origen_socio='alex'),
                registro(3, 'FERCHO - ana', origen_socio='fercho')]
        perfiles, _, _ = crear_catalogo(regs, {})
        self.assertEqual(len(perfiles), 3)
        self.assertEqual({i['usuario'] for i in perfiles.values()}, {'ana'})

    def test_grupos_antiguos_solo_con_miembros_inequivocos(self):
        regs = [registro(1, 'ana + luis'), registro(2, 'ana + desconocido')]
        enlaces = {'a': {'usuario': 'ana'}, 'b': {'usuario': 'luis'}}
        _, por, pendientes = crear_catalogo(regs, enlaces)
        self.assertEqual([i['usuario'] for i in por['1']], ['ana', 'luis'])
        self.assertIn('2', pendientes)
        enlaces['c'] = {'usuario': 'ana + luis'}
        self.assertEqual(crear_catalogo(regs, enlaces)[1]['1'], [])

    def test_enlaces_duplicados_y_ids_duplicados_pendientes(self):
        enlaces = {'a': {'usuario': 'ana'}, 'b': {'usuario': 'ana'}}
        self.assertEqual(crear_catalogo([registro(1, 'ana')], enlaces)[0], {})
        self.assertEqual(crear_catalogo([registro(1, 'ana'), registro(1, 'luis')], {})[0], {})

    def test_prefijo_sin_origen_no_se_adivina(self):
        self.assertFalse(crear_catalogo([registro(1, 'WIDGET - ana')], {})[0])
        r = registro(1, 'WIDGET - ana')
        r['historial'] = ['[09/10/2026] Creado por Widget Externo']
        self.assertEqual(len(crear_catalogo([r], {})[0]), 1)
        self.assertFalse(crear_catalogo([r], {'x': {'usuario': 'WIDGET - ana'}})[0])

    def test_nuevos_miembros_explicitos_y_nombre_con_separador(self):
        miembros = identidades_nuevas(['ana + luis', 'maria'])
        r = registro(1, 'ana + luis + maria', clientes_ficha=miembros)
        self.assertEqual(crear_catalogo([r], {})[1]['1'], miembros)

    def test_desconocidos_pruebas_y_pago_manual_no_generan_contactos(self):
        regs = [registro(1, 'Desconocido'), registro(2, 'Pago Manual'),
                registro(3, 'ana', es_prueba=True), registro(4, '🔴 [PRUEBA] ERP - ana')]
        self.assertEqual(crear_catalogo(regs, {})[0], {})


class AplicacionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=entorno_importacion.name)
        self.addCleanup(self.tmp.cleanup)
        self.dbpath = Path(self.tmp.name) / 'fichas.sqlite3'
        self.motor = create_engine('sqlite:///' + self.dbpath.as_posix())
        self.addCleanup(self.motor.dispose)
        erp._almacen_fichas = AlmacenFichas(self.motor)
        erp.DATA_FILE = str(Path(self.tmp.name) / 'datos.json')
        erp.SessionLocal = None
        erp.engine = None
        erp.usuarios_db = {
            'supremo': {'rol': 'supremo', 'permisos': [], 'estado': 'Activo', 'nombre': 'Admin', 'email': '', 'password': 'prueba'},
            'reportes': {'rol': 'reportes', 'permisos': [], 'estado': 'Activo', 'nombre': 'Reportes', 'email': '', 'password': 'prueba'},
            'cobrador': {'rol': 'cobrador', 'permisos': [], 'estado': 'Activo', 'nombre': 'Cobrador', 'email': '', 'password': 'prueba'},
            'editor': {'rol': 'cobrador', 'permisos': [PERMISO_FICHAS], 'estado': 'Activo', 'nombre': 'Editor', 'email': '', 'password': 'prueba'},
        }
        erp.registros = [registro(1, 'ana'), registro(2, 'ana'), registro(3, 'anabel'),
                         registro(4, 'WIDGET - ana', origen_socio='alex'),
                         registro(5, 'FERCHO - ana', origen_socio='fercho')]
        erp.registros[1]['estado'] = 'fallido'
        erp.registros[1]['fecha'] = '08/10/2026 12:00'
        erp.enlaces_db = {'ana': {'usuario': 'ana'}, 'anabel': {'usuario': 'anabel'}}
        erp.sistema_config = {'auto_asignar': False}
        erp.historial_pagos = [{'id': 'pago-intacto', 'monto': 123}]
        erp.grupos_creados = ['grupo']
        erp.bloqueos_ip = {}
        erp.app.config.update(TESTING=True, UPLOAD_FOLDER=self.tmp.name)
        erp.guardar_datos()
        # Otras funciones de mantenimiento/notificación no forman parte de estas pruebas.
        hooks = [f for f in erp.app.before_request_funcs[None] if f.__name__ != 'mantenimiento_datos']
        self.patch_hooks = patch.dict(erp.app.before_request_funcs, {None: hooks})
        self.patch_hooks.start()
        self.addCleanup(self.patch_hooks.stop)
        self.patch_log = patch.object(erp, 'guardar_log_seguridad')
        self.patch_log.start()
        self.addCleanup(self.patch_log.stop)
        self.client = erp.app.test_client()
        self.identidad = identidad_cliente('enlaces', 'ana')
        self.url = '/clientes/' + self.identidad['id']
        self.login('supremo')

    def login(self, username, entorno='produccion'):
        with self.client.session_transaction() as ses:
            ses.clear()
            ses.update(usuario=username, entorno=entorno, rol=erp.usuarios_db[username]['rol'],
                       permisos=list(erp.usuarios_db[username]['permisos']))

    def guardar(self, nombre='Ana Pérez', telefono='+593 999 123 456', **extra):
        self.client.get(self.url)
        with self.client.session_transaction() as ses:
            csrf = ses.get('csrf_fichas', '')
        datos = dict(nombre=nombre, telefono=telefono, csrf_token=csrf,
                     version=erp.almacen_fichas().obtener(self.identidad['id'])['version'])
        datos.update(extra)
        return self.client.post(self.url, data=datos)

    def test_botones_repetidos_misma_ficha_y_filtrados(self):
        response = self.client.get('/reportes?vista=usuario&cliente=ana')
        self.assertEqual(response.status_code, 200)
        text = response.get_data(as_text=True)
        self.assertEqual(text.count('data-ficha-id="' + self.identidad['id'] + '"'), 2)
        self.assertIn('Usuario o teléfono de contacto', text)

    def test_historial_solo_cliente_exacto_y_contacto_vacio(self):
        text = self.client.get(self.url).get_data(as_text=True)
        self.assertIn('data-registro-id="1"', text)
        self.assertIn('data-registro-id="2"', text)
        for rid in (3, 4, 5):
            self.assertNotIn(f'data-registro-id="{rid}"', text)
        self.assertIn('<dt>Teléfono de contacto</dt><dd>Sin registrar</dd>', text)
        self.assertIn('Celular original del retiro</dt><dd>Pago Manual', text)

    def test_historial_fecha_y_estado(self):
        text = self.client.get(self.url + '?historial_desde=2026-10-09&historial_hasta=2026-10-09&historial_estado=retirado').get_data(as_text=True)
        self.assertIn('data-registro-id="1"', text)
        self.assertNotIn('data-registro-id="2"', text)
        self.assertEqual(self.client.get(self.url + '?historial_desde=incorrecta').status_code, 400)
        self.assertEqual(self.client.get(self.url + '?historial_desde=2026-10-10&historial_hasta=2026-10-08').status_code, 400)

    def test_guardado_auditoria_recarga_y_nuevo_proceso(self):
        antes = copy.deepcopy((erp.registros, erp.historial_pagos, erp.enlaces_db))
        datos_antes = Path(erp.DATA_FILE).read_bytes()
        response = self.guardar()
        self.assertEqual(response.status_code, 302)
        text = self.client.get(response.location).get_data(as_text=True)
        self.assertIn('Ficha guardada correctamente.', text)
        self.assertIn('Ana Pérez', text)
        self.assertEqual((erp.registros, erp.historial_pagos, erp.enlaces_db), antes)
        self.assertEqual(Path(erp.DATA_FILE).read_bytes(), datos_antes)
        with erp.almacen_fichas().sesiones() as db:
            cambio = db.scalars(select(CambioFicha)).one()
            self.assertEqual(cambio.actor, 'supremo')
            self.assertEqual(cambio.anterior, {'nombre': '', 'telefono': ''})
            self.assertEqual(cambio.nuevo['telefono'], '+593 999 123 456')
            self.assertTrue(cambio.fecha)
        script = 'import json,sys; from sqlalchemy import create_engine; from fichas_clientes import AlmacenFichas; print(json.dumps(AlmacenFichas(create_engine(sys.argv[1])).obtener(sys.argv[2])))'
        result = subprocess.run([sys.executable, '-c', script, 'sqlite:///' + self.dbpath.as_posix(), self.identidad['id']],
                                cwd=ROOT, check=True, capture_output=True, text=True)
        self.assertEqual(json.loads(result.stdout)['nombre'], 'Ana Pérez')

    def test_telefono_compartido_busqueda_sin_fusion(self):
        self.guardar(telefono='+593 999 123 456')
        otra = identidad_cliente('enlaces', 'anabel')
        erp.almacen_fichas().guardar(otra, 'Anabel', '+593999123456', 'supremo', 0)
        text = self.client.get('/reportes?vista=usuario&cliente=999123456').get_data(as_text=True)
        self.assertEqual(text.count('data-ficha-id="' + self.identidad['id'] + '"'), 2)
        self.assertIn('data-ficha-id="' + otra['id'] + '"', text)
        self.assertNotIn('data-ficha-id="' + identidad_cliente('alex', 'ana')['id'] + '"', text)
        history = self.client.get(self.url).get_data(as_text=True)
        self.assertNotIn('data-registro-id="3"', history)
        self.assertNotIn('data-ficha-id=', self.client.get('/reportes?vista=usuario&cliente=Pago+Manual').get_data(as_text=True))

    def test_matriz_de_permisos_pantalla_y_guardado(self):
        for usuario, consultar, editar in [('supremo', True, True), ('reportes', True, False),
                                           ('editor', True, True), ('cobrador', False, False)]:
            with self.subTest(usuario=usuario):
                self.login(usuario)
                response = self.client.get(self.url)
                self.assertEqual(response.status_code, 200 if consultar else 403)
                self.assertEqual('Editar ficha' in response.get_data(as_text=True), editar)
                response = self.guardar()
                self.assertEqual(response.status_code, 302 if editar else 403)
        erp.usuarios_db['reportes']['permisos'] = [PERMISO_FICHAS]
        erp.guardar_datos()
        self.login('reportes')
        self.assertEqual(self.guardar().status_code, 302)

    def test_revocacion_inmediata_sesion_abierta_e_inactivos(self):
        self.login('editor')
        self.assertEqual(self.client.get(self.url).status_code, 200)
        erp.usuarios_db['editor']['permisos'] = []
        erp.guardar_datos()
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.client.post(self.url, data={'nombre': 'Intruso'}).status_code, 403)
        erp.usuarios_db['supremo']['estado'] = 'Inactivo'
        erp.guardar_datos()
        self.login('supremo')
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_solo_permiso_fichas_accede_por_usuario(self):
        self.login('editor')
        text = self.client.get('/reportes').get_data(as_text=True)
        self.assertIn('data-ficha-id=', text)
        self.assertNotIn('Historial y Deudas</a>', text)
        self.assertEqual(self.client.get('/reportes?vista=metricas').status_code, 302)

    def test_ver_reportes_solo_no_concede_fichas(self):
        erp.usuarios_db['cobrador']['permisos'] = ['ver_reportes']
        erp.guardar_datos()
        self.login('cobrador')
        self.assertEqual(self.client.get('/reportes?vista=usuario').status_code, 200)
        self.assertNotIn('data-ficha-id=', self.client.get('/reportes?vista=usuario').get_data(as_text=True))
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_guardado_fallido_no_confirma_y_revierte_auditoria(self):
        self.guardar(nombre='Nombre guardado')
        def fallar(conn, cursor, statement, parameters, context, executemany):
            if statement.startswith('INSERT INTO fichas_clientes_auditoria'):
                raise RuntimeError('Fallo de disco simulado')
        event.listen(self.motor, 'before_cursor_execute', fallar)
        try:
            response = self.guardar(nombre='Nombre sin guardar')
        finally:
            event.remove(self.motor, 'before_cursor_execute', fallar)
        self.assertEqual(response.status_code, 503)
        text = response.get_data(as_text=True)
        self.assertIn('No se guardaron los cambios', text)
        self.assertIn('<dd>Nombre guardado</dd>', text)
        self.assertEqual(erp.almacen_fichas().obtener(self.identidad['id'])['nombre'], 'Nombre guardado')
        with erp.almacen_fichas().sesiones() as db:
            self.assertEqual(len(db.scalars(select(CambioFicha)).all()), 1)

    def test_conflicto_no_sobrescribe_cambio_ajeno(self):
        self.guardar(nombre='Primero')
        response = self.guardar(nombre='Segundo', version=0)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(erp.almacen_fichas().obtener(self.identidad['id'])['nombre'], 'Primero')

    def test_no_permite_post_sin_csrf_y_datos_invalidos(self):
        self.assertEqual(self.client.post(self.url, data={'nombre': 'x'}).status_code, 400)
        self.assertEqual(self.guardar(telefono='Pago Manual').status_code, 400)
        self.assertEqual(self.guardar(nombre='x' * 151).status_code, 400)
        self.assertEqual(self.guardar(telefono='').status_code, 302)

    def test_volver_conserva_busqueda_y_fechas_tras_guardar_y_filtrar(self):
        contexto = dict(cliente='+593 999', fecha_desde='2026-10-01', fecha_hasta='2026-10-09')
        response = self.guardar(**contexto, historial_estado='fallido')
        text = self.client.get(response.location).get_data(as_text=True)
        volver = html.unescape(re.search(r'class="back" href="([^"]+)"', text).group(1))
        query = parse_qs(urlparse(volver).query)
        for k, v in contexto.items():
            self.assertEqual(query[k], [v])
        self.assertEqual(query['vista'], ['usuario'])
        self.assertIn('data-registro-id="2"', text)
        self.assertNotIn('data-registro-id="1"', text)

    def test_comprobantes_autorizados_y_del_cliente_correcto(self):
        erp.registros[0]['imagen'] = 'uno.jpg'
        erp.registros[2]['imagen'] = 'otro.jpg'
        Path(self.tmp.name, 'uno.jpg').write_bytes(b'prueba')
        response = self.client.get(self.url + '/comprobantes/uno.jpg')
        self.assertEqual(response.status_code, 200)
        response.close()
        self.assertEqual(self.client.get(self.url + '/comprobantes/otro.jpg').status_code, 404)
        self.login('cobrador')
        self.assertEqual(self.client.get(self.url + '/comprobantes/uno.jpg').status_code, 403)

    def test_sin_sesion_y_sesion_pruebas_no_acceden(self):
        with self.client.session_transaction() as ses:
            ses.clear()
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.login('supremo', entorno='pruebas')
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_formularios_creacion_edicion_y_persistencia_permiso(self):
        for url in ('/usuarios/crear', '/usuarios'):
            text = self.client.get(url).get_data(as_text=True)
            self.assertIn('Consultar y editar fichas de clientes', text)
        response = self.client.post('/usuarios/crear', data=dict(username='nuevo', nombre='Nuevo', password='prueba', rol='reportes', permisos=PERMISO_FICHAS))
        self.assertEqual(response.status_code, 302)
        erp.cargar_datos()
        self.assertIn(PERMISO_FICHAS, erp.usuarios_db['nuevo']['permisos'])
        response = self.client.post('/editar_usuario', data=dict(username='nuevo', nombre='Nuevo', rol='reportes', estado='Activo'))
        self.assertEqual(response.status_code, 302)
        erp.cargar_datos()
        self.assertEqual(erp.usuarios_db['nuevo']['permisos'], [])

    def test_fallo_guardando_permiso_se_informa_y_no_cambia_usuario(self):
        with patch.object(erp, 'guardar_datos', return_value=False):
            response = self.client.post('/editar_usuario', data=dict(username='reportes', nombre='Reportes', rol='reportes', estado='Activo', permisos=PERMISO_FICHAS))
        self.assertEqual(erp.usuarios_db['reportes']['permisos'], [])
        text = self.client.get(response.location).get_data(as_text=True)
        self.assertIn('No se pudieron guardar', text)

    def test_consulta_ficha_omite_mantenimiento_financiero(self):
        with erp.app.test_request_context(self.url):
            with patch.object(erp, 'realizar_respaldo_diario') as respaldo:
                erp.mantenimiento_datos()
                respaldo.assert_not_called()

    def test_nuevo_retiro_guarda_miembros_y_orm_los_conserva(self):
        with erp.app.test_request_context('/retiro_grupo/grupo', method='POST', data={'monto_usuario_ana': '10', 'monto_usuario_anabel': '15'}):
            _, error = erp.insertar_registro_retiro('pichincha', '0990000000', '', '25.00', 'NUEVO', '', '', '', None, ['ana', 'anabel'], req=erp.request)
        self.assertIsNone(error)
        nuevo = erp.registros[0]
        self.assertEqual([i['usuario'] for i in nuevo['clientes_ficha']], ['ana', 'anabel'])
        restaurado = erp._registro_modelo_a_dict(erp._registro_dict_a_orm(nuevo))
        self.assertEqual(restaurado['clientes_ficha'], nuevo['clientes_ficha'])
        erp.cargar_datos()
        self.assertEqual(erp.registros[0]['clientes_ficha'], nuevo['clientes_ficha'])

    def test_grupo_rechaza_clientes_ajenos(self):
        erp.enlaces_db['ana']['grupo'] = 'grupo'
        response = self.client.post('/retiro_grupo/grupo', data={'usuarios_magis': 'intruso'})
        self.assertEqual(response.status_code, 400)

    def test_autorizacion_releida_desde_base_sql(self):
        erp.Base.metadata.create_all(self.motor)
        fabrica = sessionmaker(bind=self.motor)
        with fabrica.begin() as db:
            db.add(erp._usuario_dict_a_orm('editor', erp.usuarios_db['editor']))
        self.login('editor')
        with patch.object(erp, 'SessionLocal', fabrica):
            self.assertEqual(self.client.get(self.url).status_code, 200)
            with fabrica.begin() as db:
                db.get(erp.DBUsuario, 'editor').permisos = []
            self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_guardado_general_sql_conserva_fichas_y_miembros(self):
        erp.Base.metadata.create_all(self.motor)
        self.guardar()
        erp.registros[0]['clientes_ficha'] = identidades_nuevas(['ana'])
        fabrica = sessionmaker(bind=self.motor)
        with patch.object(erp, 'SessionLocal', fabrica):
            self.assertTrue(erp.guardar_datos())
            erp.cargar_datos()
        with fabrica() as db:
            self.assertEqual(db.get(erp.DBRegistro, 1).clientes_ficha[0]['usuario'], 'ana')
        self.assertEqual(erp.almacen_fichas().obtener(self.identidad['id'])['nombre'], 'Ana Pérez')


if __name__ == '__main__':
    unittest.main()
