"""Pruebas aisladas: no usa la base real ni envía avisos externos."""
import copy
import html
import json
import re
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from sqlalchemy import event, select

import test_fichas_clientes as base
from reportes_caidos import (AlmacenReportes, AuditoriaReporte, AvisoVisto, SolicitudReporte,
                             ZONA, catalogo_caidos, fecha_caida, filtrar_agrupar, saldo_para_cruce)


def registro(rid, usuario, **extra):
    resultado = base.registro(rid, usuario)
    resultado.update(extra)
    return resultado


def setUpModule():
    base.setUpModule()


def tearDownModule():
    base.tearDownModule()


class ReportesCaidosTest(unittest.TestCase):
    login = base.AplicacionTest.login

    def setUp(self):
        base.AplicacionTest.setUp(self)
        self.erp = base.erp
        self.almacen = AlmacenReportes(self.motor)
        self.erp.app.extensions['almacen_reportes_caidos'] = self.almacen
        self.erp.usuarios_db['guillermo'] = dict(rol='recaudador', permisos=[], estado='Activo', password='prueba')
        self.erp.usuarios_db['otro'] = dict(rol='recaudador', permisos=[], estado='Activo', password='prueba')
        self.erp.registros = [
            registro(1, 'ana', estado='fallido', asignado_a='guillermo',
                historial=['[09/10/2026 10:42] ❌ Marcado como NO SALIÓ (Deuda) por Guillermo. Motivo: prueba']),
            registro(2, 'ana', estado='fallido_revision', asignado_a='jenny',
                historial=['[08/10/2026 17:25] ⚠️ Marcado como NO SALIÓ por Jenny. Motivo: prueba']),
            registro(3, 'anabel', estado='expirado', asignado_a='cristian',
                historial=['[09/10/2026 12:00] ❌ Expirado automáticamente (Tiempo agotado)']),
            registro(4, 'ana', estado='activo'),
            registro(5, 'ana', estado='retirado'),
            registro(6, 'ana', estado='fallido', es_prueba=True),
            registro(7, 'ana', estado='papelera'),
            registro(8, 'ana', estado='fallido', liquidado=True),
            registro(9, 'WIDGET - ana', estado='fallido', origen_socio='alex'),
        ]
        self.erp.guardar_datos()
        self.login('guillermo')

    def catalogo(self):
        return catalogo_caidos(self.erp.registros, self.erp.enlaces_db, True)

    def seleccion(self, query='busqueda=ana'):
        respuesta = self.client.get('/reportes-caidos?' + query)
        self.assertEqual(respuesta.status_code, 200)
        tokens = re.findall(r'name="seleccion" value="([^"]+)"', respuesta.get_data(as_text=True))
        self.assertTrue(tokens)
        return '/reportes-caidos/confirmar?seleccion=' + html.unescape(tokens[0])

    def token_csrf(self):
        with self.client.session_transaction() as ses:
            return ses['csrf_caidos']

    def solicitar(self, query='busqueda=ana'):
        enlace = self.seleccion(query)
        self.assertEqual(self.client.get(enlace).status_code, 200)
        token = parse_qs(urlparse(enlace).query)['seleccion'][0]
        return self.client.post('/reportes-caidos/confirmar', data=dict(
            seleccion=token, csrf_token=self.token_csrf()))

    def test_rol_recaudador_ve_todos_sin_permiso_cobrar(self):
        texto = self.client.get('/reportes-caidos').get_data(as_text=True)
        for rid in (1, 2, 3, 8, 9):
            self.assertIn(f'data-codigo="{rid}"', texto)
        for rid in (4, 5, 6, 7):
            self.assertNotIn(f'data-codigo="{rid}"', texto)
        self.assertIn('Vencido', texto)
        self.assertIn('ERP · Widget', texto)

    def test_matriz_de_permisos_y_revocacion_inmediata(self):
        for usuario, acceso in [('supremo', 200), ('guillermo', 200), ('reportes', 403), ('cobrador', 403), ('editor', 403)]:
            self.login(usuario)
            self.assertEqual(self.client.get('/reportes-caidos').status_code, acceso)
        self.login('guillermo')
        self.erp.usuarios_db['guillermo']['rol'] = 'cobrador'
        self.erp.guardar_datos()
        self.assertEqual(self.client.get('/reportes-caidos').status_code, 403)
        self.login('otro', entorno='pruebas')
        self.assertEqual(self.client.get('/reportes-caidos').status_code, 403)
        with self.client.session_transaction() as ses:
            ses.clear()
        self.assertEqual(self.client.get('/reportes-caidos').status_code, 403)

    def test_deudas_auditadas_siguen_visibles_y_reportables(self):
        self.erp.registros = [
            registro(21, 'DANNYH12', estado='fallido', liquidado=True, historial=[
                '[02/10/2026 10:27] Marcado como NO SALIÓ (Deuda) por Guillermo.',
                '[06/10/2026 11:19] Auditado y cerrado por Guillermo.']),
            registro(22, 'WIDGET - 26019GL', estado='expirado', liquidado=True, origen_socio='alex',
                historial=['[03/10/2026 04:49] Expirado automáticamente (Tiempo agotado)']),
            registro(23, 'cruce', estado='pendiente_de_cruce'),
            registro(24, 'saldada', estado='saldado'),
            registro(25, 'fusionada', estado='fusionado'),
        ]
        self.erp.guardar_datos()
        anterior = Path(self.erp.DATA_FILE).read_bytes()
        texto = self.client.get('/reportes-caidos').get_data(as_text=True)
        for rid in (21, 22):
            self.assertIn(f'data-codigo="{rid}"', texto)
        for rid in (23, 24, 25):
            self.assertNotIn(f'data-codigo="{rid}"', texto)
        self.assertIn('02/10/2026', texto)
        for rid in (21, 22):
            self.assertEqual(self.solicitar(f'busqueda=RET-{rid}').status_code, 302)
        self.assertEqual(len(self.almacen.solicitudes()), 2)
        self.login('reportes')
        aviso = self.client.get('/reportes-caidos/aviso-diario').json
        self.assertIn('DANNYH12', aviso['html'])
        self.assertIn('26019GL', aviso['html'])
        self.assertEqual(Path(self.erp.DATA_FILE).read_bytes(), anterior)

    def test_solicitud_que_pasa_a_cruce_sale_de_pendientes_y_aviso(self):
        self.solicitar('busqueda=RET-1')
        clave = next(iter(self.almacen.solicitudes()))
        self.erp.registros[0]['estado'] = 'pendiente_de_cruce'
        self.erp.guardar_datos()
        anterior = Path(self.erp.DATA_FILE).read_bytes()
        self.assertNotIn('data-codigo="1"', self.client.get('/reportes-caidos').get_data(as_text=True))
        self.login('reportes')
        self.assertNotIn('RET-1', self.client.get('/reportes-caidos/aviso-diario').json['html'])
        self.assertNotIn('RET-1', self.client.get('/reportes-caidos/solicitudes').get_data(as_text=True))
        historial = self.client.get('/reportes-caidos/solicitudes?estado=todas').get_data(as_text=True)
        self.assertIn('RET-1', historial)
        self.assertIn('Fuera del reporte actual', historial)
        detalle = self.client.get('/reportes-caidos/codigo/' + clave).get_data(as_text=True)
        self.assertNotIn('Ya atendido</button>', detalle)
        self.assertEqual(self.client.post('/reportes-caidos/atender/' + clave,
            data=dict(csrf_token=self.token_csrf(), desde_aviso='1')).status_code, 409)
        self.assertIsNone(self.almacen.solicitudes()[clave]['atendido_en'])
        self.assertEqual(Path(self.erp.DATA_FILE).read_bytes(), anterior)

    def test_codigo_que_pasa_a_cruce_antes_de_confirmar_no_se_solicita(self):
        enlace = self.seleccion('busqueda=RET-1')
        token = parse_qs(urlparse(enlace).query)['seleccion'][0]
        self.erp.registros[0]['estado'] = 'pendiente_de_cruce'
        self.erp.guardar_datos()
        respuesta = self.client.post('/reportes-caidos/confirmar', data=dict(
            seleccion=token, csrf_token=self.token_csrf()))
        self.assertEqual(respuesta.status_code, 409)
        self.assertFalse(self.almacen.solicitudes())

    def test_pago_disponible_excluye_solo_deudas_anteriores_del_mismo_usuario(self):
        deuda = registro(100, 'ana', estado='expirado', liquidado=True)
        pago = registro(200, 'ana', estado='retirado', celular='0990000000',
                        monto='50', asignado_a='otro_cobrador')
        casos = [({}, False), ({'saldo_disponible': '0.01'}, False),
                 ({'saldo_disponible': 0}, True), ({'saldo_disponible': -1}, True),
                 ({'saldo_disponible': 'invalido'}, True), ({'saldo_disponible': 'NaN'}, True),
                 ({'id': 99}, True), ({'usuario': 'anabel'}, True),
                 ({'usuario': 'Ana'}, True), ({'estado': 'activo'}, True),
                 ({'estado': 'saldado'}, True), ({'es_prueba': True}, True),
                 ({'celular': 'Pago Manual'}, True)]
        for cambios, visible in casos:
            with self.subTest(cambios=cambios):
                datos = [deuda, dict(pago, **cambios)]
                anterior = copy.deepcopy(datos)
                self.assertEqual(bool(catalogo_caidos(datos, {}, True)), visible)
                self.assertEqual(datos, anterior)
        # Un pago anterior no oculta una deuda nueva del mismo cliente.
        datos = [deuda, pago, registro(300, 'ana', estado='fallido')]
        self.assertEqual([c['registro_id'] for c in catalogo_caidos(datos, {}, True).values()], ['300'])

    def test_pago_utilizado_no_se_cuenta_dos_veces_si_no_tenia_saldo_persistido(self):
        pago = registro(200, 'ana', estado='retirado', monto='50', celular='0990000000')
        eventos = [
            '[09/10/2026 12:00] 🔄 Se destinaron $20 para saldar la deuda #1.',
            '[09/10/2026 12:01] 🔄 Se destinaron $30 para abonar a la deuda TOTAL del cliente.']
        self.assertEqual(saldo_para_cruce(dict(pago, historial=eventos[:1])), 30)
        self.assertEqual(saldo_para_cruce(dict(pago, historial=eventos)), 0)
        self.assertEqual(saldo_para_cruce(dict(pago, saldo_disponible=30, historial=eventos[:1])), 30)
        self.assertEqual(saldo_para_cruce(dict(pago, historial=[
            '[09/10/2026 12:00] 🔄 Todo el dinero de este pago se usó para abonar a la deuda #1.'])), 0)

    def test_pago_nuevo_retira_solicitud_del_aviso_y_reaparece_si_se_agota(self):
        self.solicitar('busqueda=RET-1')
        clave = next(iter(self.almacen.solicitudes()))
        pago = registro(200, 'ana', estado='retirado', celular='0990000000', saldo_disponible=50)
        self.erp.registros.append(pago)
        self.erp.guardar_datos()
        self.assertNotIn('data-codigo="1"', self.client.get('/reportes-caidos').get_data(as_text=True))
        self.login('reportes')
        self.assertNotIn('RET-1', self.client.get('/reportes-caidos/aviso-diario').json['html'])
        self.assertNotIn('RET-1', self.client.get('/reportes-caidos/solicitudes').get_data(as_text=True))
        self.assertIsNone(self.almacen.solicitudes()[clave]['atendido_en'])
        pago['saldo_disponible'] = 0
        self.erp.guardar_datos()
        self.assertIn('RET-1', self.client.get('/reportes-caidos/aviso-diario').json['html'])
        self.assertIn('RET-1', self.client.get('/reportes-caidos/solicitudes').get_data(as_text=True))

    def test_filtro_usuario_codigo_fecha_y_sin_fecha(self):
        respuesta = self.client.get('/reportes-caidos?busqueda=RET-1&desde=2026-10-09&hasta=2026-10-09')
        self.assertIn('data-codigo="1"', respuesta.get_data(as_text=True))
        self.assertNotIn('data-codigo="2"', respuesta.get_data(as_text=True))
        self.assertEqual(self.client.get('/reportes-caidos?desde=mal').status_code, 400)
        self.assertEqual(self.client.get('/reportes-caidos?desde=2026-10-10&hasta=2026-10-09').status_code, 400)
        self.assertIsNone(fecha_caida(self.erp.registros[-1]))
        self.assertNotIn('data-codigo="9"', self.client.get('/reportes-caidos?desde=2026-10-01').get_data(as_text=True))

    def test_confirmar_respeta_filtros_y_persistencia_sin_finanzas(self):
        anterior = copy.deepcopy(self.erp.registros)
        archivo = Path(self.erp.DATA_FILE).read_bytes()
        hooks = self.erp.app.before_request_funcs[None] + [self.erp.mantenimiento_datos]
        with patch.dict(self.erp.app.before_request_funcs, {None: hooks}), patch.object(self.erp, 'guardar_datos') as guardar:
            respuesta = self.solicitar('busqueda=RET-1&desde=2026-10-09&hasta=2026-10-09')
            self.assertEqual(respuesta.status_code, 302)
            guardar.assert_not_called()
        self.assertEqual(self.erp.registros, anterior)
        self.assertEqual(Path(self.erp.DATA_FILE).read_bytes(), archivo)
        guardadas = AlmacenReportes(self.motor).solicitudes()
        self.assertEqual(len(guardadas), 1)
        self.assertEqual(next(iter(guardadas.values()))['registro_id'], '1')
        self.assertIn('desde=2026-10-09', respuesta.location)

    def test_csrf_seleccion_alterada_y_de_otro_usuario(self):
        enlace = self.seleccion('busqueda=RET-1')
        token = parse_qs(urlparse(enlace).query)['seleccion'][0]
        self.assertEqual(self.client.post('/reportes-caidos/confirmar', data={'seleccion':token}).status_code, 400)
        self.assertEqual(self.client.get('/reportes-caidos/confirmar?seleccion=manipulado').status_code, 400)
        self.login('otro')
        self.assertEqual(self.client.get(enlace).status_code, 400)
        self.assertEqual(self.almacen.solicitudes(), {})

    def test_cambio_estado_o_datos_desde_confirmacion_rechazado(self):
        enlace = self.seleccion('busqueda=RET-1')
        token = parse_qs(urlparse(enlace).query)['seleccion'][0]
        self.erp.registros[0]['monto'] = '900'
        self.erp.guardar_datos()
        respuesta = self.client.post('/reportes-caidos/confirmar', data=dict(seleccion=token, csrf_token=self.token_csrf()))
        self.assertEqual(respuesta.status_code, 409)
        self.assertFalse(self.almacen.solicitudes())

    def test_duplicados_y_clics_concurrentes_no_duplican(self):
        codigos = [next(iter(self.catalogo().values()))]
        with ThreadPoolExecutor(max_workers=2) as pool:
            resultados = list(pool.map(lambda _: self.almacen.solicitar(codigos, 'guillermo'), range(2)))
        self.assertEqual(sum(resultados), 1)
        with self.almacen.sesiones() as db:
            self.assertEqual(len(list(db.scalars(select(AuditoriaReporte)))), 1)

    def test_guardado_falla_y_revierte_solicitud_y_auditoria(self):
        def fallar(conn, cursor, statement, parameters, context, executemany):
            if statement.startswith('INSERT INTO auditoria_reportes_caidos'):
                raise RuntimeError('Error de almacenamiento simulado')
        event.listen(self.motor, 'before_cursor_execute', fallar)
        try:
            with self.assertLogs(self.erp.app.logger, level='ERROR'):
                self.assertEqual(self.solicitar('busqueda=RET-1').status_code, 503)
        finally:
            event.remove(self.motor, 'before_cursor_execute', fallar)
        self.assertEqual(self.almacen.solicitudes(), {})

    def test_solo_reportes_puede_atender_con_csrf(self):
        self.solicitar('busqueda=RET-1')
        clave = next(iter(self.almacen.solicitudes()))
        self.assertEqual(self.client.post('/reportes-caidos/atender/' + clave,
            data=dict(csrf_token=self.token_csrf())).status_code, 403)
        self.login('supremo')
        self.assertEqual(self.client.post('/reportes-caidos/atender/' + clave).status_code, 403)
        self.login('reportes')
        self.client.get('/reportes-caidos/solicitudes')
        self.assertEqual(self.client.post('/reportes-caidos/atender/' + clave).status_code, 400)
        anterior = Path(self.erp.DATA_FILE).read_bytes()
        respuesta = self.client.post('/reportes-caidos/atender/' + clave,
            data=dict(csrf_token=self.token_csrf(), desde_aviso='1'))
        self.assertEqual(respuesta.json, {'ok':True, 'cambiado':True})
        self.assertEqual(self.almacen.solicitudes()[clave]['atendido_por'], 'reportes')
        self.assertEqual(Path(self.erp.DATA_FILE).read_bytes(), anterior)
        self.assertNotIn('RET-1', self.client.get('/reportes-caidos/solicitudes').get_data(as_text=True))
        self.assertIn('RET-1', self.client.get('/reportes-caidos/solicitudes?estado=atendidas').get_data(as_text=True))

    def test_aviso_diario_persistente_por_usuario_no_cierra_gestiones(self):
        self.solicitar('busqueda=RET-1')
        self.login('reportes')
        with patch('vistas_reportes_caidos.ahora_local', return_value=datetime(2026,10,9,14,tzinfo=ZONA)), patch('reportes_caidos.ahora_local', return_value=datetime(2026,10,9,14,tzinfo=ZONA)):
            datos = self.client.get('/reportes-caidos/aviso-diario').json
            self.assertTrue(datos['mostrar'])
            self.assertIn('Ya atendido', datos['html'])
            self.assertIn('RET-1', datos['html'])
            self.assertEqual(self.client.post('/reportes-caidos/aviso-visto', data={'csrf_token':datos['csrf_token']}).status_code, 200)
            self.assertFalse(self.client.get('/reportes-caidos/aviso-diario').json['mostrar'])
            self.assertFalse(next(iter(self.almacen.solicitudes().values()))['atendido_en'])
            self.login('supremo')
            aviso = self.client.get('/reportes-caidos/aviso-diario').json
            self.assertTrue(aviso['mostrar'])
            self.assertNotIn('Ya atendido', aviso['html'])
        self.login('reportes')
        with patch('vistas_reportes_caidos.ahora_local', return_value=datetime(2026,10,10,0,1,tzinfo=ZONA)):
            self.assertTrue(self.client.get('/reportes-caidos/aviso-diario').json['mostrar'])

    def test_atendido_deja_de_aparecer_en_aviso(self):
        self.solicitar('busqueda=RET-1')
        clave = next(iter(self.almacen.solicitudes()))
        self.almacen.atender(clave, 'reportes', 'Nuevo reporte confirmado')
        self.login('reportes')
        aviso = self.client.get('/reportes-caidos/aviso-diario').json
        self.assertNotIn('RET-1', aviso['html'])
        self.assertIn('0</strong> solicitud', aviso['html'])

    def test_comprobantes_no_permiten_archivos_ajenos(self):
        self.erp.registros[0]['imagen'] = 'comprobante.png'
        self.erp.guardar_datos()
        Path(self.tmp.name, 'comprobante.png').write_bytes(b'foto de prueba')
        clave = next(iter(self.catalogo()))
        ruta = '/reportes-caidos/codigo/' + clave + '/comprobante/'
        with self.client.get(ruta + 'comprobante.png') as respuesta:
            self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(self.client.get(ruta + 'otro.png').status_code, 404)
        self.login('cobrador')
        self.assertEqual(self.client.get(ruta + 'comprobante.png').status_code, 403)

    def test_detalle_resuelto_sigue_consultable_para_atender(self):
        self.solicitar('busqueda=RET-1')
        clave = next(iter(self.almacen.solicitudes()))
        self.erp.registros[0]['estado'] = 'fusionado'
        self.erp.guardar_datos()
        self.login('reportes')
        self.assertEqual(self.client.get('/reportes-caidos/codigo/' + clave).status_code, 200)

    def test_previsualizar_grupo_grande_no_guarda_antes_de_confirmar(self):
        self.erp.registros = [registro(i, 'ana', estado='fallido') for i in range(1, 202)]
        self.erp.guardar_datos()
        enlace = self.seleccion('busqueda=ana')
        token = parse_qs(urlparse(enlace).query)['seleccion'][0]
        respuesta = self.client.post('/reportes-caidos/confirmar', data=dict(
            seleccion=token, csrf_token=self.token_csrf(), accion='previsualizar'))
        self.assertEqual(respuesta.status_code, 200)
        self.assertIn('Revisa los 200 códigos', respuesta.get_data(as_text=True))
        self.assertFalse(self.almacen.solicitudes())

    def test_identidades_separadas_compartidas_y_ambiguas(self):
        registros = [registro(20, 'ana + anabel', estado='fallido'),
                     registro(21, 'nadie + otra', estado='fallido')]
        catalogo = catalogo_caidos(registros, self.erp.enlaces_db, True)
        grupos = filtrar_agrupar(catalogo, {})
        self.assertEqual(len(grupos), 3)
        claves = [c['clave'] for g in grupos for c in g['codigos'] if c['registro_id'] == '20']
        self.assertEqual(len(set(claves)), 1)
        self.assertIn('Identidad pendiente de revisión', str(grupos))

    def test_enlaces_menus_y_escape(self):
        self.erp.registros[0]['usuario'] = '<img src=x onerror=alert(1)>'
        self.erp.guardar_datos()
        texto = self.client.get('/reportes-caidos').get_data(as_text=True)
        self.assertIn('&lt;img src=x onerror=alert(1)&gt;', texto)
        self.assertNotIn('<img src=x', texto)
        self.erp.usuarios_db['guillermo']['permisos'] = ['procesar_retiros']
        self.erp.guardar_datos()
        self.login('guillermo')
        texto = self.client.get('/admin').get_data(as_text=True)
        self.assertLess(texto.index('Mi Bandeja (Cobrar)'), texto.index('data-menu-caidos'))


if __name__ == '__main__':
    unittest.main()
