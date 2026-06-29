"""
Script de migración: JSON (base_datos_erp.json / base_datos_local.json) → PostgreSQL.

Uso:
    export DATABASE_URL=postgresql://usuario:clave@host:5432/nombre_db
    python migracion_db.py

No modifica app.py ni elimina el JSON fuente. Crea tablas vacías y vuelca los datos.
"""

import json
import os
import sys

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Float,
    Integer,
    String,
    Text,
    create_engine,
)
from sqlalchemy.orm import declarative_base, sessionmaker

Base = declarative_base()


# ---------------------------------------------------------------------------
# Modelos SQLAlchemy (espejo de la estructura en app.py)
# ---------------------------------------------------------------------------

class Usuario(Base):
    __tablename__ = 'usuarios'

    username = Column(String(100), primary_key=True)
    password = Column(String(255), nullable=False, default='')
    rol = Column(String(50), nullable=False, default='cobrador')
    nombre = Column(String(150), nullable=True)
    apellido = Column(String(150), nullable=True)
    email = Column(String(255), nullable=True)
    estado = Column(String(50), nullable=True, default='Activo')
    disponible = Column(Boolean, nullable=False, default=True)
    permisos = Column(JSON, nullable=True, default=list)


class Registro(Base):
    __tablename__ = 'registros'

    id = Column(Integer, primary_key=True)
    transaccion_id = Column(String(100), nullable=True)
    fecha = Column(String(30), nullable=True)
    banco = Column(String(80), nullable=True)
    celular = Column(String(50), nullable=True)
    cedula = Column(String(30), nullable=True)
    monto = Column(String(30), nullable=True)
    usuario = Column(String(255), nullable=True)
    hora_limite = Column(String(30), nullable=True)
    expira_timestamp = Column(Float, nullable=True)
    timestamp_creacion = Column(Float, nullable=True)
    detalles = Column(JSON, nullable=True)
    imagen = Column(Text, nullable=True)
    imagen_fallo = Column(Text, nullable=True)
    motivo_fallo = Column(Text, nullable=True)
    banco_real_retiro = Column(String(80), nullable=True)
    asignado_a = Column(String(100), nullable=True)
    asignacion_estado = Column(String(50), nullable=True)
    estado = Column(String(50), nullable=True)
    historial = Column(JSON, nullable=True, default=list)
    liquidado = Column(Boolean, nullable=False, default=False)
    referencia_externa = Column(String(255), nullable=True)
    origen_socio = Column(String(50), nullable=True)
    es_prueba = Column(Boolean, nullable=True, default=False)
    codigo_prueba = Column(Boolean, nullable=True, default=False)
    alerta_deuda_firme = Column(Boolean, nullable=True, default=False)
    entorno_staging = Column(Boolean, nullable=True, default=False)
    notificado_deuda_1dia = Column(Boolean, nullable=True, default=False)


class Enlace(Base):
    __tablename__ = 'enlaces'

    token = Column(String(120), primary_key=True)
    usuario = Column(String(100), nullable=True)
    fecha = Column(String(30), nullable=True)
    grupo = Column(String(120), nullable=True, default='General')


class SuscripcionPush(Base):
    __tablename__ = 'suscripciones_push'

    id = Column(Integer, primary_key=True, autoincrement=True)
    usuario = Column(String(100), nullable=False, index=True)
    endpoint = Column(Text, nullable=True)
    datos_suscripcion = Column(JSON, nullable=True)


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------

def obtener_database_url():
    """Lee DATABASE_URL y normaliza el esquema para SQLAlchemy."""
    url = os.environ.get('DATABASE_URL')
    if not url:
        raise RuntimeError(
            'DATABASE_URL no está definida. Ejemplo: postgresql://user:pass@localhost:5432/erp'
        )
    if url.startswith('postgres://'):
        url = url.replace('postgres://', 'postgresql://', 1)
    return url


def resolver_ruta_json():
    """
    Misma convención que app.py:
    - Render/producción: /var/data/base_datos_erp.json
    - Local: base_datos_local.json (o base_datos_erp.json si existe en la raíz)
    """
    if os.path.exists('/var/data/base_datos_erp.json'):
        return '/var/data/base_datos_erp.json'
    if os.path.exists('base_datos_erp.json'):
        return 'base_datos_erp.json'
    if os.path.exists('base_datos_local.json'):
        return 'base_datos_local.json'
    raise FileNotFoundError(
        'No se encontró base_datos_erp.json ni base_datos_local.json.'
    )


def cargar_json(ruta):
    with open(ruta, 'r', encoding='utf-8') as f:
        return json.load(f)


def migrar_usuarios(session, usuarios_db):
    """Inserta usuarios_db: { username: { password, rol, nombre, ... } }."""
    count = 0
    for username, info in (usuarios_db or {}).items():
        if not isinstance(info, dict):
            continue
        session.add(Usuario(
            username=str(username).lower(),
            password=info.get('password', ''),
            rol=info.get('rol', 'cobrador'),
            nombre=info.get('nombre'),
            apellido=info.get('apellido'),
            email=info.get('email'),
            estado=info.get('estado', 'Activo'),
            disponible=bool(info.get('disponible', True)),
            permisos=info.get('permisos', []),
        ))
        count += 1
    session.commit()
    return count


def migrar_registros(session, registros):
    """Inserta la lista de retiros/códigos."""
    count = 0
    for r in (registros or []):
        if not isinstance(r, dict):
            continue
        session.add(Registro(
            id=r.get('id'),
            transaccion_id=r.get('transaccion_id'),
            fecha=r.get('fecha'),
            banco=r.get('banco'),
            celular=r.get('celular'),
            cedula=r.get('cedula'),
            monto=r.get('monto'),
            usuario=r.get('usuario'),
            hora_limite=r.get('hora_limite'),
            expira_timestamp=r.get('expira_timestamp'),
            timestamp_creacion=r.get('timestamp_creacion'),
            detalles=r.get('detalles'),
            imagen=r.get('imagen'),
            imagen_fallo=r.get('imagen_fallo'),
            motivo_fallo=r.get('motivo_fallo'),
            banco_real_retiro=r.get('banco_real_retiro'),
            asignado_a=r.get('asignado_a'),
            asignacion_estado=r.get('asignacion_estado'),
            estado=r.get('estado'),
            historial=r.get('historial', []),
            liquidado=bool(r.get('liquidado', False)),
            referencia_externa=r.get('referencia_externa'),
            origen_socio=r.get('origen_socio'),
            es_prueba=bool(r.get('es_prueba', False)),
            codigo_prueba=bool(r.get('codigo_prueba', False)),
            alerta_deuda_firme=bool(r.get('alerta_deuda_firme', False)),
            entorno_staging=bool(r.get('entorno_staging', False)),
            notificado_deuda_1dia=bool(r.get('notificado_deuda_1dia', False)),
        ))
        count += 1
    session.commit()
    return count


def migrar_enlaces(session, enlaces_db):
    """Inserta enlaces_db: { token: { usuario, fecha, grupo } }."""
    count = 0
    for token, info in (enlaces_db or {}).items():
        if not isinstance(info, dict):
            continue
        session.add(Enlace(
            token=str(token),
            usuario=info.get('usuario'),
            fecha=info.get('fecha'),
            grupo=info.get('grupo', 'General'),
        ))
        count += 1
    session.commit()
    return count


def migrar_suscripciones_push(session, suscripciones_push):
    """
    Inserta suscripciones_push.
    Formato actual: { usuario: [ { endpoint, keys, ... }, ... ] }
    Formato legado: { usuario: { endpoint, keys, ... } }
    """
    count = 0
    for usuario, subs in (suscripciones_push or {}).items():
        if isinstance(subs, dict):
            subs = [subs]
        if not isinstance(subs, list):
            continue
        for sub in subs:
            if not isinstance(sub, dict):
                continue
            session.add(SuscripcionPush(
                usuario=str(usuario).lower(),
                endpoint=sub.get('endpoint'),
                datos_suscripcion=sub,
            ))
            count += 1
    session.commit()
    return count


def ejecutar_migracion():
    """
    Crea tablas en PostgreSQL y vuelca el contenido del JSON de producción.
    """
    database_url = obtener_database_url()
    ruta_json = resolver_ruta_json()

    print(f'📂 Leyendo datos desde: {ruta_json}')
    print('🐘 Conectando a PostgreSQL...')

    engine = create_engine(database_url, echo=False)
    Session = sessionmaker(bind=engine)
    session = Session()

    try:
        Base.metadata.create_all(engine)
        print('✅ Tablas creadas (o ya existentes).')

        data = cargar_json(ruta_json)

        try:
            n_usuarios = migrar_usuarios(session, data.get('usuarios_db', {}))
            print(f'   → Usuarios migrados: {n_usuarios}')
        except Exception as e:
            session.rollback()
            print(f'❌ Error migrando usuarios_db: {e}')
            raise

        try:
            n_registros = migrar_registros(session, data.get('registros', []))
            print(f'   → Registros migrados: {n_registros}')
        except Exception as e:
            session.rollback()
            print(f'❌ Error migrando registros: {e}')
            raise

        try:
            n_enlaces = migrar_enlaces(session, data.get('enlaces_db', {}))
            print(f'   → Enlaces migrados: {n_enlaces}')
        except Exception as e:
            session.rollback()
            print(f'❌ Error migrando enlaces_db: {e}')
            raise

        try:
            n_push = migrar_suscripciones_push(session, data.get('suscripciones_push', {}))
            print(f'   → Suscripciones push migradas: {n_push}')
        except Exception as e:
            session.rollback()
            print(f'❌ Error migrando suscripciones_push: {e}')
            raise

        print('🎉 Migración completada con éxito.')

    except Exception as e:
        session.rollback()
        print(f'💥 Migración abortada: {e}')
        sys.exit(1)
    finally:
        session.close()


if __name__ == '__main__':
    ejecutar_migracion()
