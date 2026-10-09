"""Paginación, búsqueda global y exportación de Links Clientes con datos aislados."""
import copy
from pathlib import Path
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import test_fichas_clientes as base


def setUpModule():
    base.setUpModule()


def tearDownModule():
    base.tearDownModule()


class PaginacionLinksTest(unittest.TestCase):
    login = base.AplicacionTest.login

    def setUp(self):
        base.AplicacionTest.setUp(self)
        self.erp = base.erp
        self.enlaces = {
            f'token-{i:02}': {
                'usuario': f'Cliente {i:02}',
                'grupo': 'General' if i != 22 else 'Grupo especial',
                'fecha': '09/10/2026 10:30' if i != 22 else '08/10/2026 09:15',
            }
            for i in range(23)
        }
        self.usar_enlaces(self.enlaces)

    def usar_enlaces(self, enlaces):
        self.erp.enlaces_db = copy.deepcopy(enlaces)
        self.erp.guardar_datos()

    def pagina(self, **parametros):
        with patch.object(self.erp, 'render_template', return_value='links') as render:
            respuesta = self.client.get('/', query_string=parametros)
        self.assertEqual(respuesta.status_code, 200)
        return render.call_args.kwargs

    def test_veintitres_clientes_en_tres_paginas_sin_perder_orden_ni_datos(self):
        originales = copy.deepcopy(self.erp.enlaces_db)
        archivo_antes = Path(self.erp.DATA_FILE).read_bytes()
        vistos = []
        for numero, cantidad in [(1, 10), (2, 10), (3, 3)]:
            with self.subTest(pagina=numero):
                contexto = self.pagina(pagina=numero)
                enlaces = contexto['enlaces']
                esperados = list(originales)[(numero - 1) * 10:numero * 10]
                self.assertEqual(list(enlaces), esperados)
                self.assertEqual(len(enlaces), cantidad)
                self.assertEqual(enlaces, {token: originales[token] for token in esperados})
                vistos.extend(enlaces)
                resumen = contexto['paginacion_usuarios']
                self.assertEqual((resumen['pagina'], resumen['paginas']), (numero, 3))
                self.assertEqual(resumen['total_usuarios'], 23)
                self.assertEqual((resumen['desde'], resumen['hasta']),
                                 ((numero - 1) * 10 + 1, min(numero * 10, 23)))
        self.assertEqual(vistos, list(originales))
        self.assertEqual(self.erp.enlaces_db, originales)
        self.assertEqual(Path(self.erp.DATA_FILE).read_bytes(), archivo_antes)

    def test_busqueda_global_encuentra_usuario_token_grupo_y_fecha_antes_de_paginar(self):
        for consulta in ['cLiEnTe 22', 'TOKEN-22', '/retiro/token-22', 'GRUPO ESPECIAL', '08/10/2026']:
            with self.subTest(consulta=consulta):
                contexto = self.pagina(cliente=consulta, pagina=3)
                self.assertEqual(list(contexto['enlaces']), ['token-22'])
                self.assertEqual(contexto['filtro_cliente'], consulta)
                self.assertEqual(contexto['paginacion_usuarios']['total_usuarios'], 1)
                self.assertEqual(contexto['paginacion_usuarios']['pagina'], 1)

    def test_busqueda_unicode_casefold_y_paginacion_conservan_filtro_codificado(self):
        consulta = 'STRASSE + &'
        self.usar_enlaces({f'token-{i}': dict(data, usuario=f'Straße + & {i}')
                          for i, data in enumerate(self.enlaces.values())})
        contexto = self.pagina(cliente=consulta, pagina=2)
        resumen = contexto['paginacion_usuarios']
        self.assertEqual(resumen['total_usuarios'], 23)
        self.assertEqual(len(contexto['enlaces']), 10)
        for enlace in [resumen['anterior_url'], resumen['siguiente_url'],
                       *[pagina['url'] for pagina in resumen['paginas_visibles']]]:
            partes = urlparse(enlace)
            self.assertEqual(partes.path, '/')
            parametros = parse_qs(partes.query)
            self.assertEqual(parametros['cliente'], [consulta])
            self.assertIn('pagina', parametros)
            self.assertNotIn('exportar', parametros)

    def test_paginas_invalidas_fuera_de_rango_y_busqueda_sin_resultados(self):
        for valor, esperado in [('', 1), ('abc', 1), ('-4', 1), ('0', 1), ('1.5', 1), ('99', 3)]:
            with self.subTest(pagina=valor):
                self.assertEqual(self.pagina(pagina=valor)['paginacion_usuarios']['pagina'], esperado)
        self.assertIsNone(self.pagina()['paginacion_usuarios']['anterior_url'])
        self.assertIsNone(self.pagina(pagina=3)['paginacion_usuarios']['siguiente_url'])
        for enlaces, parametros in [(self.enlaces, {'cliente': 'No existe', 'pagina': 99}),
                                     ({}, {'pagina': 2})]:
            self.usar_enlaces(enlaces)
            contexto = self.pagina(**parametros)
            self.assertEqual(contexto['enlaces'], {})
            resumen = contexto['paginacion_usuarios']
            self.assertEqual((resumen['pagina'], resumen['paginas']), (1, 1))
            self.assertEqual((resumen['desde'], resumen['hasta'], resumen['total_usuarios']), (0, 0, 0))

    def test_exportacion_incluye_todos_los_clientes_aunque_haya_pagina_y_busqueda(self):
        respuesta = self.client.get('/', query_string={'exportar': '1', 'pagina': 2, 'cliente': 'Cliente 22'})
        self.assertEqual(respuesta.status_code, 200)
        self.assertTrue(respuesta.is_json)
        filas = respuesta.get_json()['filas']
        self.assertEqual(filas, [[data['usuario'], f'http://localhost/retiro/{token}', data['grupo'], data['fecha']]
                                for token, data in self.enlaces.items()])
        self.assertEqual(len(filas), 23)

    def test_listado_y_exportacion_respetan_sesion_y_permiso_crear_links(self):
        for usuario in [None, 'reportes', 'cobrador']:
            if usuario:
                self.login(usuario)
            else:
                with self.client.session_transaction() as ses:
                    ses.clear()
            for parametros in [{}, {'exportar': '1'}]:
                with self.subTest(usuario=usuario, parametros=parametros):
                    respuesta = self.client.get('/', query_string=parametros)
                    self.assertEqual(respuesta.status_code, 302)
                    self.assertFalse(respuesta.is_json)
                    self.assertNotIn('Cliente 22', respuesta.get_data(as_text=True))
        self.erp.usuarios_db['cobrador']['permisos'] = ['crear_links']
        self.login('cobrador')
        self.assertEqual(len(self.pagina()['enlaces']), 10)
        self.assertEqual(len(self.client.get('/?exportar=1').get_json()['filas']), 23)


if __name__ == '__main__':
    unittest.main()
