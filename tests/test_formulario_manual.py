"""Validación de formularios sin iniciar el ERP ni acceder a datos o servicios reales."""
import ast
from datetime import datetime
from pathlib import Path
import re
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from flask import Flask, redirect, session
from werkzeug.datastructures import MultiDict

ROOT = Path(__file__).resolve().parents[1]


class FormularioManualTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        arbol = ast.parse((ROOT / 'app.py').read_text(encoding='utf-8-sig'))
        nombres = {'validar_codigos_manuales_retiro', 'procesar_formulario_retiro'}
        cls.codigo = compile(ast.Module(body=[n for n in arbol.body
            if isinstance(n, ast.FunctionDef) and n.name in nombres], type_ignores=[]), 'app.py', 'exec')

    def setUp(self):
        self.app = Flask(__name__)
        self.app.secret_key = 'prueba-local'
        self.insertar = Mock(return_value=('TRX-PRUEBA', None))
        self.rechazar = Mock(side_effect=lambda req, mensaje, **kwargs: (mensaje, 400))
        self.env = dict(re=re, insertar_registro_retiro=self.insertar,
                        _rechazar_procesar_formulario_retiro=self.rechazar,
                        session=session, redirect=redirect, flash=Mock(),
                        hora_ecuador=lambda: datetime(2026, 11, 16, 12, 0),
                        render_widget_postmessage_exito=Mock(return_value='widget-ok'))
        exec(self.codigo, self.env)

    def solicitud(self, banco='pichincha', foto=False, **datos):
        form = dict(banco=banco, monto='25.00', celular='0991234567',
                    codigo_recibido='0012', clave_envio='0034', clave_retiro='0056')
        form.update(datos)
        imagen = Mock(filename='comprobante.png')
        return SimpleNamespace(form=MultiDict(form), args=MultiDict(), url='/retiro/prueba',
                               files=MultiDict([('comprobante', imagen)] if foto else []))

    def test_pichincha_rechaza_longitudes_y_caracteres_invalidos(self):
        for codigo in ('', '1', '123', '12345', '123456', '12a4', ' 1234', '１２３４'):
            with self.subTest(codigo=codigo):
                error = self.env['validar_codigos_manuales_retiro'](self.solicitud(codigo_recibido=codigo), 'pichincha')
                self.assertIn('Pichincha', error)
                self.assertIn('4 dígitos', error)

    def test_guayaquil_exige_cuatro_en_cada_campo_por_separado(self):
        for campo in ('clave_envio', 'clave_retiro'):
            for codigo in ('', '123', '123456', '12a4'):
                with self.subTest(campo=campo, codigo=codigo):
                    self.assertIsNotNone(self.env['validar_codigos_manuales_retiro'](
                        self.solicitud('guayaquil', **{campo: codigo}), 'guayaquil'))
        self.assertIsNone(self.env['validar_codigos_manuales_retiro'](self.solicitud('guayaquil'), 'guayaquil'))

    def test_sin_foto_no_se_omite_validacion_mediante_marcador(self):
        req = self.solicitud(codigo_recibido='123456', campos_manuales='')
        self.assertIsNotNone(self.env['validar_codigos_manuales_retiro'](req, 'pichincha'))

    def test_con_foto_valida_campos_completados_manualmente(self):
        req = self.solicitud('guayaquil', foto=True, clave_envio='123456',
                            campos_manuales='clave_envio')
        self.assertIn('envío', self.env['validar_codigos_manuales_retiro'](req, 'guayaquil'))
        req.form['clave_envio'] = '0012'
        req.form['clave_retiro'] = 'OCR123456'
        self.assertIsNone(self.env['validar_codigos_manuales_retiro'](req, 'guayaquil'))

    def test_produbanco_y_codigos_ocr_conservan_sus_reglas(self):
        req = self.solicitud('produbanco', codigo_recibido='123456')
        self.assertIsNone(self.env['validar_codigos_manuales_retiro'](req, 'produbanco'))
        req = self.solicitud(foto=True, codigo_recibido='123456', campos_manuales='')
        self.assertIsNone(self.env['validar_codigos_manuales_retiro'](req, 'pichincha'))

    def test_invalido_no_guarda_imagen_ni_crea_retiro_normal_o_widget(self):
        for widget in (False, True):
            req = self.solicitud(foto=True, codigo_recibido='123456', campos_manuales='codigo_recibido')
            with self.app.test_request_context('/retiro/prueba', method='POST'):
                resultado = self.env['procesar_formulario_retiro'](req, ['ana'], modo_widget=widget)
            self.assertEqual(resultado[1], 400)
            req.files['comprobante'].save.assert_not_called()
            self.insertar.assert_not_called()

    def test_guardado_valido_conserva_ceros_y_codigos_independientes(self):
        for banco in ('pichincha', 'guayaquil', 'produbanco'):
            for widget in (False, True):
                with self.subTest(banco=banco, widget=widget):
                    req = self.solicitud(banco, codigo_recibido='001234' if banco == 'produbanco' else '0012')
                    with self.app.test_request_context('/retiro/prueba', method='POST'):
                        self.env['procesar_formulario_retiro'](req, ['ana'], modo_widget=widget)
                    args = self.insertar.call_args.args
                    self.assertEqual(args[4:7], (req.form['codigo_recibido'], '0056', '0034'))


if __name__ == '__main__':
    unittest.main()
