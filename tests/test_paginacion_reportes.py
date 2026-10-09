"""Paginación del reporte por usuario usando datos y fichas temporales."""
import html
import re
import unittest
from decimal import Decimal
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import test_fichas_clientes as base


def setUpModule():
    base.setUpModule()


def tearDownModule():
    base.tearDownModule()


def registro(rid, usuario, **cambios):
    return dict(base.registro(rid, usuario), **cambios)


class PaginacionReportesTest(unittest.TestCase):
    login = base.AplicacionTest.login
    guardar = base.AplicacionTest.guardar

    def setUp(self):
        base.AplicacionTest.setUp(self)
        self.erp = base.erp

    def usar_registros(self, registros):
        self.erp.registros = registros
        self.erp.guardar_datos()

    def reporte(self, **parametros):
        with patch.object(self.erp, 'render_template', return_value='reporte') as render:
            respuesta = self.client.get('/reportes', query_string=dict(vista='usuario', **parametros))
        self.assertEqual(respuesta.status_code, 200)
        return render.call_args.kwargs

    def test_paginas_de_diez_usuarios_sin_dividir_ni_perder_registros(self):
        registros = [registro(i, f'cliente-{i:02}') for i in range(22)]
        registros += [registro(i + 100, f'cliente-{i:02}') for i in reversed(range(22))]
        self.usar_registros(registros)
        ids_vistos, usuarios_vistos = set(), set()
        for pagina, cantidad in [(1, 10), (2, 10), (3, 2)]:
            contexto = self.reporte(pagina=pagina)
            filas = contexto['registros_tabla_dinamica']
            usuarios = {r['usuario'] for r in filas}
            ids = {r['id'] for r in filas}
            esperados = {f'cliente-{i:02}' for i in range((pagina - 1) * 10, min(pagina * 10, 22))}
            self.assertEqual(usuarios, esperados)
            self.assertEqual(len(usuarios), cantidad)
            self.assertEqual(len(filas), cantidad * 2)
            self.assertFalse(ids & ids_vistos)
            self.assertFalse(usuarios & usuarios_vistos)
            self.assertEqual(filas, [r for r in registros if r['usuario'] in esperados])
            ids_vistos.update(ids)
            usuarios_vistos.update(usuarios)
            resumen = contexto['paginacion_usuarios']
            self.assertEqual((resumen['total_usuarios'], resumen['total_registros']), (22, 44))
            self.assertEqual(resumen['total_monto'], Decimal('1100.00'))
            self.assertEqual(resumen['paginas'], 3)
        self.assertEqual(ids_vistos, {r['id'] for r in registros})

    def test_usuario_exacto_no_mezcla_mayusculas_ni_origenes(self):
        nombres = ['ana', 'Ana', 'WIDGET - ana', 'FERCHO - ana', 'ana + luis']
        nombres += [f'otro-{i}' for i in range(6)]
        self.usar_registros([registro(i, nombre) for i, nombre in enumerate(nombres)])
        primero, segundo = self.reporte(), self.reporte(pagina=2)
        self.assertEqual(primero['paginacion_usuarios']['total_usuarios'], 11)
        self.assertEqual([r['usuario'] for r in primero['registros_tabla_dinamica']], nombres[:10])
        self.assertEqual([r['usuario'] for r in segundo['registros_tabla_dinamica']], nombres[10:])

    def test_busqueda_fechas_y_estados_se_aplican_antes_de_paginar(self):
        registros = [registro(i, f'cliente-{i}', fecha='08/10/2026 10:00') for i in range(30)]
        registros += [registro(100 + i, f'cliente-{i}', monto='10.50') for i in range(12)]
        registros += [registro(200, 'otro'), registro(201, 'cliente-activo', estado='activo'),
                      registro(202, 'cliente-papelera', estado='papelera'),
                      registro(203, 'cliente-futuro', fecha='10/10/2026 10:00')]
        self.usar_registros(registros)
        contexto = self.reporte(cliente='cliente', fecha_desde='2026-10-09', fecha_hasta='2026-10-09', pagina=2)
        self.assertEqual([r['id'] for r in contexto['registros_tabla_dinamica']], [110, 111])
        resumen = contexto['paginacion_usuarios']
        self.assertEqual((resumen['total_usuarios'], resumen['total_registros']), (12, 12))
        self.assertEqual(resumen['total_monto'], Decimal('126.00'))
        self.assertEqual((resumen['desde'], resumen['hasta']), (11, 12))

    def test_busqueda_por_contacto_pagina_solo_usuarios_coincidentes(self):
        self.erp.almacen_fichas().guardar(self.identidad, 'Ana', '+593999123456', 'supremo', 0)
        contexto = self.reporte(cliente='999123456', pagina=99)
        self.assertEqual({r['usuario'] for r in contexto['registros_tabla_dinamica']}, {'ana'})
        self.assertEqual(contexto['paginacion_usuarios']['total_usuarios'], 1)
        self.assertEqual(contexto['paginacion_usuarios']['pagina'], 1)

    def test_paginas_invalidas_y_fuera_de_rango(self):
        self.usar_registros([registro(i, f'usuario-{i}') for i in range(21)])
        for valor, esperado in [('', 1), ('abc', 1), ('-4', 1), ('0', 1), ('1.5', 1), ('99', 3)]:
            with self.subTest(pagina=valor):
                resumen = self.reporte(pagina=valor)['paginacion_usuarios']
                self.assertEqual(resumen['pagina'], esperado)
        self.assertIsNone(self.reporte()['paginacion_usuarios']['anterior_url'])
        self.assertIsNone(self.reporte(pagina=3)['paginacion_usuarios']['siguiente_url'])

    def test_sin_resultados_mantiene_rango_cero_y_una_pagina(self):
        contexto = self.reporte(cliente='no-existe', pagina=99)
        self.assertEqual(contexto['registros_tabla_dinamica'], [])
        resumen = contexto['paginacion_usuarios']
        self.assertEqual((resumen['pagina'], resumen['paginas']), (1, 1))
        self.assertEqual((resumen['desde'], resumen['hasta'], resumen['total_usuarios']), (0, 0, 0))
        self.assertEqual(resumen['total_monto'], 0)

    def test_enlaces_conservan_filtros_y_no_arrastran_exportacion(self):
        self.usar_registros([registro(i, f'cliente + {i}') for i in range(81)])
        resumen = self.reporte(cliente='cliente +', fecha_desde='2026-10-01',
                              fecha_hasta='2026-10-09', pagina=5, exportar=1)['paginacion_usuarios']
        numeros = [p['numero'] for p in resumen['paginas_visibles']]
        self.assertEqual(numeros, [1, 3, 4, 5, 6, 7, 9])
        self.assertLessEqual(len(numeros), 7)
        for enlace in [resumen['anterior_url'], resumen['siguiente_url'], *[p['url'] for p in resumen['paginas_visibles']]]:
            partes = urlparse(enlace)
            self.assertEqual(partes.path, '/reportes')
            consulta = parse_qs(partes.query)
            self.assertEqual(consulta['vista'], ['usuario'])
            self.assertEqual(consulta['cliente'], ['cliente +'])
            self.assertEqual(consulta['fecha_desde'], ['2026-10-01'])
            self.assertEqual(consulta['fecha_hasta'], ['2026-10-09'])
            self.assertNotIn('exportar', consulta)

    def test_exportar_incluye_todos_los_resultados_filtrados(self):
        self.usar_registros([registro(i, f'cliente-{i}') for i in range(21)] + [registro(30, 'otro')])
        contexto = self.reporte(cliente='cliente', pagina=2, exportar=1)
        self.assertEqual([r['id'] for r in contexto['registros_tabla_dinamica']], list(range(21)))
        self.assertEqual(contexto['paginacion_usuarios']['total_usuarios'], 21)
        self.assertEqual(len(self.reporte(cliente='cliente', pagina=2)['registros_tabla_dinamica']), 10)

    def test_otras_vistas_no_se_paginan(self):
        self.usar_registros([registro(i, f'cliente-{i}') for i in range(21)])
        for vista in ['completados', 'historial', 'cobradores', 'valor', 'estado', 'sucursal', 'metricas']:
            with self.subTest(vista=vista):
                with patch.object(self.erp, 'render_template', return_value='reporte') as render:
                    respuesta = self.client.get('/reportes', query_string=dict(vista=vista, pagina=2))
                self.assertEqual(respuesta.status_code, 200)
                contexto = render.call_args.kwargs
                self.assertIsNone(contexto['paginacion_usuarios'])
                self.assertEqual(len(contexto['registros_tabla_dinamica']), 21)

    def test_simulador_conserva_prefijo_en_navegacion(self):
        self.login('supremo', entorno='pruebas')
        with patch.object(self.erp, 'registros_pruebas', [registro(i, f'cliente-{i}') for i in range(21)]):
            with patch.object(self.erp, 'render_template', return_value='reporte') as render:
                respuesta = self.client.get('/pruebas/reportes?vista=usuario&pagina=2')
        self.assertEqual(respuesta.status_code, 200)
        resumen = render.call_args.kwargs['paginacion_usuarios']
        for enlace in [resumen['anterior_url'], resumen['siguiente_url'], *[p['url'] for p in resumen['paginas_visibles']]]:
            self.assertEqual(urlparse(enlace).path, '/pruebas/reportes')

    def test_ficha_conserva_pagina_al_volver_despues_de_guardar(self):
        contexto = dict(cliente='ana', fecha_desde='2026-10-01', fecha_hasta='2026-10-09', pagina='3')
        respuesta = self.guardar(**contexto)
        self.assertEqual(respuesta.status_code, 302)
        texto = self.client.get(respuesta.location).get_data(as_text=True)
        volver = html.unescape(re.search(r'class="back" href="([^"]+)"', texto).group(1))
        consulta = parse_qs(urlparse(volver).query)
        for clave, valor in contexto.items():
            self.assertEqual(consulta[clave], [valor])
        self.assertEqual(consulta['vista'], ['usuario'])
        self.assertIn('name="pagina" value="3"', texto)


if __name__ == '__main__':
    unittest.main()
