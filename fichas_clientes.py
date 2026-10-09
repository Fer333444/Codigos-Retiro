"""Identidades y contactos independientes de los datos financieros de cada retiro."""

import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timezone

from sqlalchemy import Column, Integer, JSON, String, Text, select, update
from sqlalchemy.orm import declarative_base, sessionmaker

BaseFichas = declarative_base()
PERMISO_FICHAS = 'gestionar_fichas_clientes'
ORIGENES = {'enlaces': 'Enlaces y grupos', 'alex': 'ERP · Widget', 'fercho': 'ERP · Fercho'}
GENERICO = {'', 'desconocido', 'widget-externo', 'pago manual', 'none', 'null'}


def identidad_cliente(origen, usuario):
    """Conserva el identificador literalmente; teléfono y OCR nunca son claves."""
    if origen not in ORIGENES or not isinstance(usuario, str) or usuario.strip().casefold() in GENERICO:
        return None
    clave = json.dumps([origen, usuario], ensure_ascii=False, separators=(',', ':'))
    return {'id': hashlib.sha256(clave.encode('utf-8')).hexdigest(),
            'origen': origen, 'usuario': usuario, 'origen_nombre': ORIGENES[origen]}


def identidades_nuevas(usuarios, origen=None):
    """Captura miembros antes de perder sus límites al unir nombres con ' + '."""
    resultado = []
    for usuario in usuarios:
        if origen in ('alex', 'fercho'):
            prefijo = 'WIDGET - ' if origen == 'alex' else 'FERCHO - '
            if not usuario.startswith(prefijo):
                continue
            usuario = usuario[len(prefijo):]
        identidad = identidad_cliente(origen or 'enlaces', usuario)
        if identidad and identidad not in resultado:
            resultado.append(identidad)
    return resultado


def crear_catalogo(registros, enlaces):
    """Resuelve históricos sin escribirlos; excluye duplicados y etiquetas ambiguas.

    No iguala mayúsculas ni busca subcadenas. Un registro múltiple antiguo
    requiere todos sus miembros exactos en enlaces y ninguna colisión con
    identificadores que contengan el separador. Cada ERP conserva su ámbito.
    """
    cantidades = Counter(e.get('usuario') for e in enlaces.values() if e.get('usuario'))
    ids = Counter(str(r.get('id', '')) for r in registros)
    perfiles, por_registro, pendientes = {}, {}, {}
    for registro in registros:
        rid = str(registro.get('id', ''))
        identidades = []
        if ids[rid] > 1 or not rid:
            pendientes[rid] = 'Identificador de registro duplicado o ausente; requiere revisión.'
        elif registro.get('es_prueba') or registro.get('codigo_prueba') or registro.get('entorno_staging'):
            pendientes[rid] = 'Código de pruebas: sin ficha en producción.'
        elif registro.get('clientes_ficha') is not None:
            for item in registro.get('clientes_ficha') or []:
                if isinstance(item, dict):
                    identidad = identidad_cliente(item.get('origen'), item.get('usuario'))
                    if identidad and identidad['id'] == item.get('id') and identidad not in identidades:
                        identidades.append(identidad)
        else:
            usuario = registro.get('usuario') or ''
            origen = registro.get('origen_socio')
            if usuario.startswith('🔴 [PRUEBA]'):
                pendientes[rid] = 'Código de pruebas: sin ficha en producción.'
            elif origen in ('alex', 'fercho'):
                prefijo = 'WIDGET - ' if origen == 'alex' else 'FERCHO - '
                if usuario.startswith(prefijo):
                    identidades = identidades_nuevas([usuario], origen)
            elif origen:
                pendientes[rid] = 'Origen del cliente pendiente de revisión.'
            elif usuario.startswith(('WIDGET - ', 'FERCHO - ')):
                marca = 'Creado por Widget Externo' if usuario.startswith('WIDGET - ') else 'Recibido vía API Fercho'
                if not cantidades[usuario] and any(marca in str(h) for h in registro.get('historial', [])):
                    identidades = identidades_nuevas([usuario], 'alex' if usuario.startswith('WIDGET - ') else 'fercho')
            elif ' + ' in usuario:
                miembros = usuario.split(' + ')
                colision = any(cantidades[' + '.join(miembros[i:j])]
                               for i in range(len(miembros)) for j in range(i + 2, len(miembros) + 1))
                if not colision and all(cantidades[m] == 1 for m in miembros):
                    identidades = identidades_nuevas(miembros)
            elif cantidades[usuario] <= 1:
                identidades = identidades_nuevas([usuario])
        if not identidades:
            pendientes.setdefault(rid, 'Identidad pendiente de revisión; no se ha asociado a una ficha.')
        por_registro[rid] = identidades
        for identidad in identidades:
            perfiles[identidad['id']] = identidad
    return perfiles, por_registro, pendientes


def digitos_telefono(valor):
    return re.sub(r'\D', '', valor or '')


def coincide_busqueda(registro, texto, identidades, contactos):
    texto = texto.strip()
    if texto.casefold() in (registro.get('usuario') or '').casefold():
        return True
    telefono = digitos_telefono(texto)
    if not telefono or not re.fullmatch(r'[+\d\s().-]+', texto):
        return False
    return any(telefono in digitos_telefono(contactos.get(i['id'], {}).get('telefono', ''))
               for i in identidades)


class FichaCliente(BaseFichas):
    __tablename__ = 'fichas_clientes'
    id = Column(String(64), primary_key=True)
    origen = Column(String(30), nullable=False)
    usuario = Column(Text, nullable=False)
    nombre = Column(String(150), nullable=False, default='')
    telefono = Column(String(40), nullable=False, default='')
    version = Column(Integer, nullable=False, default=1)
    actualizado_por = Column(String(100), nullable=False)
    actualizado_en = Column(String(40), nullable=False)


class CambioFicha(BaseFichas):
    __tablename__ = 'fichas_clientes_auditoria'
    id = Column(Integer, primary_key=True, autoincrement=True)
    ficha_id = Column(String(64), nullable=False, index=True)
    actor = Column(String(100), nullable=False)
    fecha = Column(String(40), nullable=False)
    anterior = Column(JSON, nullable=False)
    nuevo = Column(JSON, nullable=False)


class ConflictoFicha(Exception):
    pass


class AlmacenFichas:
    def __init__(self, engine):
        self.engine = engine
        BaseFichas.metadata.create_all(engine)
        self.sesiones = sessionmaker(bind=engine)

    @staticmethod
    def datos(ficha):
        if not ficha:
            return {'nombre': '', 'telefono': '', 'version': 0}
        return {c.name: getattr(ficha, c.name) for c in FichaCliente.__table__.columns}

    def obtener(self, ficha_id):
        with self.sesiones() as db:
            return self.datos(db.get(FichaCliente, ficha_id))

    def contactos(self):
        with self.sesiones() as db:
            return {f.id: self.datos(f) for f in db.scalars(select(FichaCliente))}

    def guardar(self, identidad, nombre, telefono, actor, version):
        """Contacto y auditoría se confirman juntos o no se guarda nada."""
        fecha = datetime.now(timezone.utc).isoformat(timespec='seconds')
        with self.sesiones.begin() as db:
            anterior = self.datos(db.get(FichaCliente, identidad['id']))
            if anterior['version'] != version:
                raise ConflictoFicha('La ficha cambió en otra sesión. Recarga antes de editar.')
            valores = dict(nombre=nombre, telefono=telefono, actualizado_por=actor,
                           actualizado_en=fecha, version=version + 1)
            if version:
                cambio = db.execute(update(FichaCliente).where(
                    FichaCliente.id == identidad['id'], FichaCliente.version == version
                ).values(**valores))
                if cambio.rowcount != 1:
                    raise ConflictoFicha('La ficha cambió en otra sesión. Recarga antes de editar.')
            else:
                db.add(FichaCliente(id=identidad['id'], origen=identidad['origen'],
                                    usuario=identidad['usuario'], **valores))
            db.add(CambioFicha(ficha_id=identidad['id'], actor=actor, fecha=fecha,
                              anterior={k: anterior[k] for k in ('nombre', 'telefono')},
                              nuevo={'nombre': nombre, 'telefono': telefono}))
