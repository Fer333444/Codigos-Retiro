"""Persistencia del saldo de cruces, siempre sobre bases temporales."""
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import Column, MetaData, Table, create_engine
from sqlalchemy.orm import sessionmaker

import test_fichas_clientes as base


def setUpModule():
    base.setUpModule()


def tearDownModule():
    base.tearDownModule()


class SaldoDisponibleTest(unittest.TestCase):
    login = base.AplicacionTest.login

    def setUp(self):
        base.AplicacionTest.setUp(self)
        self.erp = base.erp

    def test_distingue_pago_antiguo_de_saldo_agotado(self):
        for saldo in (None, 0, 12.5):
            with self.subTest(saldo=saldo):
                original = base.registro(101, 'ana')
                if saldo is not None:
                    original['saldo_disponible'] = saldo
                restaurado = self.erp._registro_modelo_a_dict(self.erp._registro_dict_a_orm(original))
                if saldo is None:
                    self.assertNotIn('saldo_disponible', restaurado)
                else:
                    self.assertEqual(restaurado['saldo_disponible'], saldo)
                self.assertEqual(restaurado['monto'], original['monto'])

    def test_guardar_y_recargar_sql_conserva_saldo_parcial_y_cero(self):
        self.erp.Base.metadata.create_all(self.motor)
        fabrica = sessionmaker(bind=self.motor)
        self.erp.registros[0]['saldo_disponible'] = 0
        self.erp.registros[1]['saldo_disponible'] = 8.75
        with patch.object(self.erp, 'SessionLocal', fabrica):
            self.assertTrue(self.erp.guardar_datos())
            self.erp.cargar_datos()
            registros, _ = self.erp.datos_para_reportes_caidos()
        actuales = {r['id']: r for r in registros}
        self.assertEqual(actuales[1]['saldo_disponible'], 0)
        self.assertEqual(actuales[2]['saldo_disponible'], 8.75)
        self.assertNotIn('saldo_disponible', actuales[3])
        self.assertEqual(actuales[1]['monto'], '25.00')

    def test_arranque_agrega_columna_sin_alterar_datos_antiguos(self):
        ruta = Path(self.tmp.name) / 'erp_antiguo.sqlite3'
        url = 'sqlite:///' + ruta.as_posix()
        motor = create_engine(url)
        self.addCleanup(motor.dispose)
        metadata = MetaData()
        tabla = Table('registros', metadata, *[
            Column(c.name, c.type, primary_key=c.primary_key, nullable=c.nullable)
            for c in self.erp.DBRegistro.__table__.columns if c.name != 'saldo_disponible'
        ])
        metadata.create_all(motor)
        historial = ['[09/10/2026 09:00] Se destinaron $25 para saldar la deuda #1.']
        with motor.begin() as conexion:
            conexion.execute(tabla.insert().values(id=101, usuario='ana', monto='50.00',
                estado='retirado', liquidado=False, historial=historial))
        codigo = '''
import app
from sqlalchemy import inspect
app._sincronizar_columnas_registros()
columna = next(c for c in inspect(app.engine).get_columns('registros') if c['name'] == 'saldo_disponible')
assert columna['nullable']
with app.SessionLocal() as db:
    registro = db.get(app.DBRegistro, 101)
    assert registro.monto == '50.00'
    assert registro.saldo_disponible is None
    assert registro.historial == ['[09/10/2026 09:00] Se destinaron $25 para saldar la deuda #1.']
    assert 'saldo_disponible' not in app._registro_modelo_a_dict(registro)
app.engine.dispose()
'''
        entorno = dict(os.environ, DATABASE_URL=url, OPENAI_API_KEY='', TELEGRAM_BOT_TOKEN='',
            VAPID_PRIVATE_KEY='', VAPID_PUBLIC_KEY='', PYTHONIOENCODING='utf-8',
            PYTHONPATH=str(base.ROOT) + os.pathsep + os.environ.get('PYTHONPATH', ''))
        resultado = subprocess.run([sys.executable, '-c', codigo], cwd=self.tmp.name,
            env=entorno, capture_output=True, text=True, encoding='utf-8', timeout=45)
        self.assertEqual(resultado.returncode, 0, resultado.stderr)


if __name__ == '__main__':
    unittest.main()
