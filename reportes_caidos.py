"""Consulta y solicitudes de caídos, separadas de los movimientos financieros."""

import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from sqlalchemy import Column, Integer, JSON, String, Text, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import declarative_base, sessionmaker

from fichas_clientes import crear_catalogo

ZONA = timezone(timedelta(hours=-5))
BaseReportes = declarative_base()
ESTADOS_CAIDOS = {'fallido': 'No salió', 'fallido_revision': 'En revisión'}


def ahora_local():
    return datetime.now(ZONA)


def clave_estable(valor):
    return hashlib.sha256(json.dumps(valor, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def fecha_valida(valor):
    for formato in ('%d/%m/%Y %H:%M:%S', '%d/%m/%Y %H:%M', '%d/%m/%Y'):
        try:
            return datetime.strptime(str(valor or ''), formato).replace(tzinfo=ZONA)
        except ValueError:
            pass
    return None


def fecha_caida(registro):
    # Una hora de creación o de asignación nunca sustituye a la hora de caída.
    for evento in reversed(registro.get('historial') or []):
        evento = str(evento)
        if not re.search(r'NO SALI[ÓO]|marcado como FALLIDO|Expirado automáticamente', evento, re.I):
            continue
        prefijo = re.match(r'^\[(\d{2}/\d{2}/\d{4} \d{2}:\d{2}(?::\d{2})?)\]', evento)
        return fecha_valida(prefijo.group(1)) if prefijo else None
    return None


def es_prueba(registro):
    return any(registro.get(k) for k in ('es_prueba', 'codigo_prueba', 'entorno_staging')) or str(
        registro.get('usuario') or '').startswith('🔴 [PRUEBA]')


def saldo_para_cruce(pago):
    """Consulta el saldo sin cambiar pagos, incluyendo historiales anteriores a su persistencia."""
    try:
        explicito = pago.get('saldo_disponible')
        saldo = Decimal(str(explicito if explicito is not None else pago.get('monto', 0)))
        if not saldo.is_finite():
            return Decimal(0)
        if explicito is None:
            # Los pagos alternativos ya abonaron la deuda al crearse.
            if pago.get('celular') == 'Pago Manual':
                return Decimal(0)
            for evento in pago.get('historial') or []:
                texto = str(evento)
                if re.search(r'\] 🔄 Todo el dinero de este pago se usó para abonar a la deuda #\d+\.$', texto):
                    return Decimal(0)
                usado = re.search(r'\] 🔄 Se destinaron \$([0-9]+(?:\.[0-9]+)?) para '
                    r'(?:abonar a la deuda TOTAL del cliente|saldar la deuda #\d+)\.$', texto)
                if usado:
                    saldo -= Decimal(usado.group(1))
        return max(saldo, Decimal(0))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(0)


def pagos_disponibles_por_usuario(registros):
    """Un pago posterior con saldo permite cruzar las deudas previas del mismo usuario exacto."""
    ultimos = {}
    for pago in registros:
        if pago.get('estado') != 'retirado' or es_prueba(pago) or saldo_para_cruce(pago) <= 0:
            continue
        usuario = pago.get('usuario')
        try:
            identificador = int(pago['id'])
        except (KeyError, ValueError, TypeError, OverflowError):
            continue
        if usuario:
            ultimos[usuario] = max(identificador, ultimos.get(usuario, identificador))
    return ultimos


def catalogo_caidos(registros, enlaces, incluir_vencidos=False):
    """Las identidades ambiguas se muestran por registro, sin fusionarlas ni ocultarlas."""
    _, por_registro, pendientes = crear_catalogo(registros, enlaces)
    repetidos = Counter(str(r.get('id') or '') for r in registros)
    pagos_disponibles = pagos_disponibles_por_usuario(registros)
    resultado = {}
    for registro in registros:
        rid = str(registro.get('id') or '')
        estado = registro.get('estado')
        if estado not in ESTADOS_CAIDOS and not (incluir_vencidos and estado == 'expirado'):
            continue
        # «Liquidado» solo cierra la bandeja del cobrador; la deuda puede seguir activa.
        # El estado actual decide si necesita volver a reportarse (no pendiente_de_cruce).
        if es_prueba(registro):
            continue
        try:
            if pagos_disponibles.get(registro.get('usuario'), -1) > int(rid):
                continue
        except (ValueError, TypeError, OverflowError):
            pass
        caida = fecha_caida(registro)
        # El número de caídas también distingue recaídas ocurridas en el mismo minuto.
        episodios = sum(bool(re.search(r'NO SALI[ÓO]|marcado como FALLIDO|Expirado automáticamente',
                                      str(h), re.I)) for h in registro.get('historial') or [])
        clave = clave_estable([rid, registro.get('fecha'), caida.isoformat() if caida else '', episodios])
        identidades = por_registro.get(rid, [])
        if not identidades:
            identidades = [{'id': 'pendiente-' + clave, 'usuario': registro.get('usuario') or 'Sin usuario',
                            'origen_nombre': 'Identidad pendiente de revisión', 'origen': 'pendiente'}]
        archivos = list(dict.fromkeys(n.strip() for campo in ('imagen', 'imagen_fallo')
                                     for n in (registro.get(campo) or '').split(',') if n.strip()))
        codigo = {
            'clave': clave, 'registro_id': rid, 'codigo': 'RET-' + rid,
            'usuario_original': registro.get('usuario') or 'Sin usuario', 'clientes': identidades,
            'creado': registro.get('fecha') or 'Sin información',
            'caido': caida.strftime('%d/%m/%Y %H:%M') if caida else 'Sin información',
            'caido_iso': caida.isoformat() if caida else '',
            'estado': estado, 'estado_nombre': ESTADOS_CAIDOS.get(estado, 'Vencido'),
            'monto': str(registro.get('monto') or '0'), 'banco': str(registro.get('banco') or '').upper(),
            'cobrador': registro.get('asignado_a') or 'Sin asignar',
            'detalles': registro.get('detalles') or {}, 'archivos': archivos,
            'motivo': registro.get('motivo_fallo') or '', 'historial': registro.get('historial') or [],
            'advertencia': pendientes.get(rid, ''), 'solicitable': bool(rid) and repetidos[rid] == 1,
        }
        if not codigo['solicitable']:
            codigo['advertencia'] = 'Identificador duplicado o ausente. Requiere revisión antes de solicitar.'
            clave = clave_estable([clave, len(resultado)])
            codigo['clave'] = clave
        resultado[clave] = codigo
    return resultado


def filtrar_agrupar(catalogo, solicitudes, busqueda='', desde='', hasta=''):
    limites = {}
    for campo, valor in (('desde', desde), ('hasta', hasta)):
        try:
            limites[campo] = datetime.strptime(valor, '%Y-%m-%d').date() if valor else None
        except ValueError as exc:
            raise ValueError('Revisa las fechas: utiliza día, mes y año válidos.') from exc
    if limites['desde'] and limites['hasta'] and limites['desde'] > limites['hasta']:
        raise ValueError('La fecha inicial debe ser anterior o igual a la final.')
    grupos = {}
    texto = busqueda.strip().casefold()
    for clave, original in catalogo.items():
        caida = datetime.fromisoformat(original['caido_iso']).date() if original['caido_iso'] else None
        if (desde or hasta) and not caida:
            continue
        if limites['desde'] and caida < limites['desde'] or limites['hasta'] and caida > limites['hasta']:
            continue
        codigo = dict(original, solicitud=solicitudes.get(clave))
        for cliente in codigo['clientes']:
            if texto and not any(texto in str(v).casefold() for v in (
                    cliente['usuario'], codigo['codigo'], codigo['registro_id'])):
                continue
            grupo = grupos.setdefault(cliente['id'], dict(cliente, codigos=[], pendientes=[]))
            grupo['codigos'].append(codigo)
            if not codigo['solicitud'] and codigo['solicitable']:
                grupo['pendientes'].append(clave)
    for grupo in grupos.values():
        grupo['codigos'].sort(key=lambda c: (c['caido_iso'], c['registro_id']), reverse=True)
        grupo['pendientes'] = [c['clave'] for c in grupo['codigos'] if not c['solicitud'] and c['solicitable']]
        grupo['todas_atendidas'] = all(c['solicitud'] and c['solicitud']['atendido_en'] for c in grupo['codigos'])
    return sorted(grupos.values(), key=lambda g: g['codigos'][0]['caido_iso'], reverse=True)


class SolicitudReporte(BaseReportes):
    __tablename__ = 'solicitudes_reportes_caidos'
    clave = Column(String(64), primary_key=True)
    registro_id = Column(String(80), nullable=False, index=True)
    datos = Column(JSON, nullable=False)
    solicitado_por = Column(String(100), nullable=False)
    solicitado_en = Column(String(40), nullable=False)
    atendido_por = Column(String(100), nullable=True)
    atendido_en = Column(String(40), nullable=True)
    nota_atencion = Column(Text, nullable=True)


class AuditoriaReporte(BaseReportes):
    __tablename__ = 'auditoria_reportes_caidos'
    id = Column(Integer, primary_key=True, autoincrement=True)
    clave = Column(String(64), nullable=False, index=True)
    accion = Column(String(40), nullable=False)
    actor = Column(String(100), nullable=False)
    fecha = Column(String(40), nullable=False)
    nota = Column(Text, nullable=True)


class AvisoVisto(BaseReportes):
    __tablename__ = 'avisos_vistos_reportes_caidos'
    clave = Column(String(64), primary_key=True)
    usuario = Column(String(100), nullable=False, index=True)
    dia = Column(String(10), nullable=False)
    visto_en = Column(String(40), nullable=False)


class AlmacenReportes:
    def __init__(self, motor):
        self.engine = motor
        # Serializa creación concurrente en PostgreSQL, sin alterar tablas del ERP.
        with motor.begin() as conexion:
            if motor.dialect.name == 'postgresql':
                from sqlalchemy import text
                conexion.execute(text('SELECT pg_advisory_xact_lock(704903171)'))
            BaseReportes.metadata.create_all(conexion)
        self.sesiones = sessionmaker(bind=motor)

    @staticmethod
    def datos(fila):
        return {c.name: getattr(fila, c.name) for c in fila.__table__.columns}

    def insertar_unico(self, db, modelo, valores):
        insertar = pg_insert if self.engine.dialect.name == 'postgresql' else sqlite_insert
        return db.execute(insertar(modelo).values(**valores).on_conflict_do_nothing()).rowcount == 1

    def solicitudes(self):
        with self.sesiones() as db:
            return {s.clave: self.datos(s) for s in db.scalars(select(SolicitudReporte))}

    def solicitar(self, codigos, actor, ahora=None):
        fecha = (ahora or ahora_local()).isoformat(timespec='seconds')
        nuevos = 0
        with self.sesiones.begin() as db:
            for codigo in codigos:
                nuevo = self.insertar_unico(db, SolicitudReporte, {
                    'clave': codigo['clave'], 'registro_id': codigo['registro_id'], 'datos': codigo,
                    'solicitado_por': actor, 'solicitado_en': fecha})
                if nuevo:
                    nuevos += 1
                    db.add(AuditoriaReporte(clave=codigo['clave'], accion='solicitado', actor=actor, fecha=fecha))
        return nuevos

    def atender(self, clave, actor, nota):
        if not 3 <= len(nota.strip()) <= 500 or any(ord(c) < 32 and c not in '\n\r' for c in nota):
            raise ValueError('Indica una referencia o nota de la gestión, entre 3 y 500 caracteres.')
        fecha = ahora_local().isoformat(timespec='seconds')
        with self.sesiones.begin() as db:
            resultado = db.execute(update(SolicitudReporte).where(
                SolicitudReporte.clave == clave, SolicitudReporte.atendido_en.is_(None)
            ).values(atendido_por=actor, atendido_en=fecha, nota_atencion=nota.strip()))
            if resultado.rowcount:
                db.add(AuditoriaReporte(clave=clave, accion='atendido', actor=actor, fecha=fecha, nota=nota.strip()))
            return resultado.rowcount == 1

    def aviso_visto(self, usuario, dia):
        with self.sesiones() as db:
            return db.get(AvisoVisto, clave_estable(['codigos-solicitados', usuario, dia])) is not None

    def marcar_aviso_visto(self, usuario, ahora=None):
        ahora = ahora or ahora_local()
        with self.sesiones.begin() as db:
            self.insertar_unico(db, AvisoVisto, dict(clave=clave_estable(['codigos-solicitados', usuario, ahora.date().isoformat()]),
                usuario=usuario, dia=ahora.date().isoformat(), visto_en=ahora.isoformat(timespec='seconds')))


def texto_fecha(valor):
    if not valor:
        return ''
    try:
        return datetime.fromisoformat(valor).astimezone(ZONA).strftime('%d/%m/%Y %H:%M')
    except ValueError:
        return str(valor)
