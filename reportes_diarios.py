"""Informes privados de Telegram: consultas de solo lectura y entregas persistentes.

No importa app.py ni modifica retiros, saldos, deudas o la base de producción.
"""
import argparse
from collections import Counter
from contextlib import contextmanager
import csv
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import html
import io
import json
import logging
import os
from pathlib import Path
import re
import shutil
import sqlite3
import threading
from urllib.parse import quote
import zipfile

import requests

ECUADOR = timezone(timedelta(hours=-5))
MAX_ARCHIVO = 45_000_000
ESTADOS = {'retirado': 'Completado', 'activo': 'Activo / pendiente',
           'fallido': 'No salió', 'fallido_revision': 'No salió / en revisión',
           'expirado': 'Vencido', 'saldado': 'Saldado', 'fusionado': 'Fusionado',
           'pendiente_de_cruce': 'Pendiente de cruce', 'papelera': 'Papelera'}
LOG = logging.getLogger('informes_telegram')


def ahora_ecuador():
    return datetime.now(ECUADOR)


def fecha_registro(registro):
    value = registro.get('fecha')
    if value:
        try:
            return datetime.strptime(str(value)[:16], '%d/%m/%Y %H:%M').date()
        except ValueError:
            pass
    try:
        return datetime.fromtimestamp(float(registro['timestamp_creacion']), ECUADOR).date()
    except (KeyError, ValueError, TypeError, OverflowError, OSError):
        return None


def importe(value):
    try:
        amount = Decimal(str(value).replace(',', '.'))
        return amount.quantize(Decimal('0.01')) if amount.is_finite() else None
    except (InvalidOperation, ValueError):
        return None


def es_prueba(registro):
    return any(registro.get(k) for k in ('es_prueba', 'codigo_prueba', 'entorno_staging')) or '[PRUEBA]' in str(registro.get('usuario', '')).upper()


def leer_datos():
    """La conexión y la transacción son independientes del estado financiero en memoria."""
    url = os.environ.get('DATABASE_URL', '')
    if not url:
        raise RuntimeError('Falta DATABASE_URL para leer el informe de producción')
    from sqlalchemy import create_engine, text
    engine = create_engine(url.replace('postgres://', 'postgresql://', 1), pool_pre_ping=True)
    try:
        with engine.connect() as conn, conn.begin():
            conn.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'))
            rows = [dict(r) for r in conn.execute(text('''SELECT id, transaccion_id, fecha,
                timestamp_creacion, usuario, monto, banco, estado, asignado_a, celular,
                banco_real_retiro, motivo_fallo, historial, imagen, imagen_fallo, liquidado,
                es_prueba, codigo_prueba, entorno_staging FROM registros''')).mappings()]
            pagos = conn.execute(text("SELECT valor FROM miscelaneo WHERE clave='historial_pagos'")).scalar() or []
            return rows, pagos
    finally:
        engine.dispose()


def seleccionar_datos(registros, pagos, dia, corte):
    marca = dia.strftime('%d/%m/%Y')
    elegidos, ignorados, vistos = [], 0, set()
    for r in registros:
        if es_prueba(r):
            continue
        fecha = fecha_registro(r)
        historial = r.get('historial') or []
        if isinstance(historial, str):
            historial = [historial]
        movimiento = any(re.match(r'^\[' + re.escape(marca) + r'(?: |\])', str(e)) for e in historial)
        if fecha != dia and not movimiento:
            if fecha is None:
                ignorados += 1
            continue
        key = str(r.get('id'))
        if key in vistos:
            raise ValueError('ID de retiro duplicado en la fuente; informe detenido')
        vistos.add(key)
        row = {k: r.get(k) for k in ('id', 'transaccion_id', 'fecha', 'usuario', 'monto', 'banco',
               'estado', 'asignado_a', 'celular', 'banco_real_retiro', 'motivo_fallo',
               'imagen', 'imagen_fallo', 'liquidado')}
        row['historial'] = [str(e) for e in historial]
        row['seccion'] = 'Ingresados del día' if fecha == dia else 'Movimientos de retiros anteriores'
        if row.get('estado') == 'papelera':
            row['seccion'] = 'Papelera (fuera de totales)'
        row['monto_valido'] = importe(r.get('monto')) is not None
        elegidos.append(row)
    elegidos.sort(key=lambda r: (r['seccion'], str(r.get('usuario', '')).casefold(), str(r['id'])))
    return {'fecha': dia.isoformat(), 'corte': corte.isoformat(), 'registros': elegidos,
            'pagos': [{k: p.get(k) for k in ('id_pago', 'fecha', 'cobrador', 'monto', 'metodo', 'tipo', 'receptor')}
                      for p in pagos if str(p.get('fecha', '')).startswith(marca)],
            'sin_fecha_en_fuente': ignorados}


def totales(datos):
    rows = [r for r in datos['registros'] if r['seccion'] == 'Ingresados del día']
    result = {}
    for r in rows:
        estado = str(r.get('estado') or 'sin_estado')
        item = result.setdefault(estado, {'cantidad': 0, 'monto': Decimal(0)})
        item['cantidad'] += 1
        item['monto'] += importe(r.get('monto')) or Decimal(0)
    return result


def resumen(datos, prueba=False):
    titulo = 'PRUEBA · CORTE PARCIAL' if prueba else 'INFORME DIARIO DE RETIROS'
    lines = [titulo, datetime.fromisoformat(datos['fecha']).strftime('%d/%m/%Y'),
             'Hora de Ecuador · corte ' + datetime.fromisoformat(datos['corte']).strftime('%d/%m %H:%M'), '']
    stats = totales(datos)
    lines.append('Registros ingresados: ' + str(sum(v['cantidad'] for v in stats.values())))
    for estado, item in sorted(stats.items()):
        lines.append(f"{ESTADOS.get(estado, estado)}: {item['cantidad']} · ${item['monto']:.2f}")
    if not stats:
        lines.append('Sin retiros ingresados este día en la base consultada.')
    previos = sum(r['seccion'] == 'Movimientos de retiros anteriores' for r in datos['registros'])
    lines += ['', f'Retiros anteriores con movimientos: {previos}',
              f'Liquidaciones registradas: {len(datos["pagos"])}',
              'Montos y estados registrados al corte. Saldados/fusionados no se cuentan como cobros nuevos.',
              'Adjuntos: informe para leer, tabla CSV y comprobantes originales organizados por registro.']
    if datos.get('archivos_no_incluidos'):
        lines.append(f'Atención: {len(datos["archivos_no_incluidos"])} comprobantes no disponibles; detalle en el informe.')
    if any(not r['monto_valido'] for r in datos['registros']):
        lines.append('Atención: hay montos inválidos, excluidos de las sumas e identificados en el detalle.')
    return '\n'.join(lines)


def celda_csv(value):
    text = str(value if value is not None else '')
    return "'" + text if text.lstrip().startswith(('=', '+', '-', '@', '\t', '\r')) else text


def informe_csv(datos):
    stream = io.StringIO(newline='')
    writer = csv.writer(stream, delimiter=';')
    writer.writerow(['Sección', 'ID', 'Transacción', 'Fecha', 'Usuario', 'Monto registrado', 'Banco',
                     'Estado', 'Cobrador', 'Celular del retiro', 'Banco de cobro', 'Liquidado', 'Motivo', 'Comprobantes', 'Historial'])
    for r in datos['registros']:
        writer.writerow([celda_csv(v) for v in [r['seccion'], r['id'], r.get('transaccion_id'), r.get('fecha'),
            r.get('usuario'), r.get('monto'), r.get('banco'), ESTADOS.get(r.get('estado'), r.get('estado')),
            r.get('asignado_a'), r.get('celular'), r.get('banco_real_retiro'), 'Sí' if r.get('liquidado') else 'No',
            r.get('motivo_fallo'), ' | '.join(r.get('comprobantes', [])), ' | '.join(r['historial'])]])
    return stream.getvalue().encode('utf-8-sig')


def informe_html(datos, prueba=False):
    esc = lambda v: html.escape(str(v if v is not None else '—'))
    body = [f'<h1>Informe de retiros · {esc(datos["fecha"])}</h1>',
            f'<pre>{esc(resumen(datos, prueba))}</pre>',
            '<p>Los estados corresponden a la consulta indicada. Los pendientes no se consideran fallidos. '
            'Las liquidaciones son movimientos de caja separados; no se suman otra vez a los retiros. '
            'El monto es el que conserva el sistema y puede reflejar abonos o ajustes anteriores.</p>',
            '<p>Los registros compartidos conservan sus usuarios y se cuentan una sola vez por ID. '
            'La papelera aparece aparte y los datos de prueba se excluyen.</p>']
    for seccion in ('Ingresados del día', 'Movimientos de retiros anteriores', 'Papelera (fuera de totales)'):
        rows = [r for r in datos['registros'] if r['seccion'] == seccion]
        body += [f'<h2>{esc(seccion)} · {len(rows)}</h2>',
                 '<div class="tabla"><table><tr><th>Usuario / ID</th><th>Fecha</th><th>Monto</th><th>Banco</th><th>Estado</th><th>Cobrador</th></tr>']
        for r in rows:
            estado = ESTADOS.get(r.get('estado'), r.get('estado') or 'Sin estado')
            body.append(f'<tr><td><strong>{esc(r.get("usuario"))}</strong><br>{esc(r["id"])}</td>'
                        f'<td>{esc(r.get("fecha"))}</td><td>${esc(r.get("monto"))}</td><td>{esc(r.get("banco"))}</td>'
                        f'<td>{esc(estado)}</td><td>{esc(r.get("asignado_a"))}</td></tr>')
        body.append('</table></div>')
        for r in rows:
            body.append(f'<details><summary>{esc(r.get("usuario"))} · ${esc(r.get("monto"))} · {esc(r["id"])}</summary>')
            body.append(f'<p>Transacción: {esc(r.get("transaccion_id"))}<br>Celular guardado en el retiro: {esc(r.get("celular"))}'
                        f'<br>Banco de cobro: {esc(r.get("banco_real_retiro"))}<br>Liquidado: {"Sí" if r.get("liquidado") else "No"}'
                        f'<br>Motivo: {esc(r.get("motivo_fallo"))}</p>')
            for archivo in r.get('comprobantes', []):
                body.append(f'<p><a href="{quote(archivo, safe="/")}">{esc(Path(archivo).name)}</a></p>')
            if not r.get('comprobantes'):
                body.append('<p>Sin archivo de comprobante disponible para este registro.</p>')
            body.append('<ul>' + ''.join('<li>' + esc(e) + '</li>' for e in r['historial']) + '</ul></details>')
    body.append('<h2>Liquidaciones del día</h2>')
    for p in datos['pagos']:
        body.append(f'<p>{esc(p.get("fecha"))} · {esc(p.get("cobrador"))} · ${esc(p.get("monto"))} · '
                    f'{esc(p.get("metodo"))} · Recibió: {esc(p.get("receptor"))}</p>')
    if datos.get('archivos_no_incluidos'):
        body.append('<h2>Comprobantes no incluidos</h2><ul>' + ''.join('<li>' + esc(v) + '</li>' for v in datos['archivos_no_incluidos']) + '</ul>')
    if datos.get('sin_fecha_en_fuente'):
        body.append(f'<p>Hay {datos["sin_fecha_en_fuente"]} registros sin fecha interpretable en la fuente; no se asignaron a este día.</p>')
    style = 'body{font:15px system-ui,sans-serif;color:#172033;background:#f5f7fb;margin:24px auto;max-width:1100px;padding:20px}h1,h2{color:#5b21b6}pre{white-space:pre-wrap;background:white;padding:22px;border-radius:12px;line-height:1.6}table{border-collapse:collapse;width:100%;background:white}td,th{padding:12px;border:1px solid #ddd;text-align:left}th{background:#ede9fe}.tabla{overflow:auto}details{background:white;border:1px solid #ddd;padding:12px;margin:10px 0;border-radius:8px}summary{cursor:pointer;font-weight:600}a{color:#5b21b6}'
    return ('<!doctype html><html lang="es"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>Informe diario de retiros</title><style>' + style + '</style><body>' + ''.join(body) + '</body></html>').encode('utf-8')


def construir_paquete(datos, uploads, destino, prueba=False, limite=MAX_ARCHIVO):
    """Archiva una instantánea verificable. Nunca sigue rutas fuera de uploads."""
    destino = Path(destino)
    destino.mkdir(parents=True, exist_ok=True)
    uploads = Path(uploads).resolve()
    archivos, errores = {}, []
    for r in datos['registros']:
        r['comprobantes'] = []
        for campo in ('imagen', 'imagen_fallo'):
            for nombre in str(r.get(campo) or '').split(','):
                nombre = nombre.strip()
                if not nombre:
                    continue
                path = (uploads / nombre).resolve()
                if Path(nombre).name != nombre or not path.is_relative_to(uploads) or not path.is_file():
                    errores.append(f'ID {r["id"]}: {nombre} (archivo no disponible o ruta inválida)')
                    continue
                if path.stat().st_size > limite - 1_000_000:
                    errores.append(f'ID {r["id"]}: {nombre} (supera el tamaño permitido del adjunto)')
                    continue
                rid = re.sub(r'[^a-zA-Z0-9_-]', '_', str(r['id']))
                relativo = f'comprobantes/{rid}/{nombre}'
                archivos[relativo] = path
                if relativo not in r['comprobantes']:
                    r['comprobantes'].append(relativo)
    datos['archivos_no_incluidos'] = errores
    html_bytes, csv_bytes = informe_html(datos, prueba), informe_csv(datos)
    estimado = sum(p.stat().st_size for p in archivos.values()) + 3 * (len(html_bytes) + len(csv_bytes)) + 2_000_000
    if shutil.disk_usage(destino).free < estimado + 50_000_000:
        raise OSError('Espacio insuficiente para el informe; se reserva espacio para el sistema')
    (destino / 'LEER_INFORME.html').write_bytes(html_bytes)
    (destino / 'DETALLE.csv').write_bytes(csv_bytes)
    (destino / 'resumen.txt').write_text(resumen(datos, prueba), encoding='utf-8')
    (destino / 'datos.json').write_text(json.dumps(datos, ensure_ascii=False, indent=2), encoding='utf-8')
    metadata = len(html_bytes) + len(csv_bytes) + 50_000
    grupos, actual, peso = [], [], metadata
    for relativo, path in archivos.items():
        size = path.stat().st_size + 1000
        if actual and peso + size >= limite:
            grupos.append(actual)
            actual, peso = [], metadata
        actual.append((relativo, path))
        peso += size
    grupos.append(actual)
    salidas = ['LEER_INFORME.html', 'DETALLE.csv']
    for numero, grupo in enumerate(grupos, 1):
        name = f'RETIROS_{datos["fecha"]}_{numero:02d}_de_{len(grupos):02d}.zip'
        with zipfile.ZipFile(destino / name, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('LEER_INFORME.html', html_bytes)
            archive.writestr('DETALLE.csv', csv_bytes)
            archive.writestr('LEEME.txt', 'Extrae todas las partes ZIP en la misma carpeta y abre LEER_INFORME.html. Los comprobantes están separados por ID de registro.\n')
            for relativo, path in grupo:
                archive.write(path, relativo)
        if (destino / name).stat().st_size > limite:
            raise ValueError('Adjunto demasiado grande; no se enviará un informe incompleto')
        salidas.append(name)
    manifest = {'resumen': resumen(datos, prueba), 'archivos': [
        {'nombre': n, 'sha256': hashlib.sha256((destino / n).read_bytes()).hexdigest()} for n in salidas]}
    temp = destino / 'manifest.json.tmp'
    temp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(destino / 'manifest.json')
    return manifest


@contextmanager
def bloqueo_proceso(path):
    """Un único emisor por disco, incluso con varios workers de Gunicorn."""
    with open(path, 'a+b') as handle:
        if handle.tell() == 0:
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError):
            yield False
            return
        try:
            yield True
        finally:
            if os.name == 'nt':
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class ErrorTelegram(Exception):
    def __init__(self, mensaje, incierto=False):
        super().__init__(mensaje)
        self.incierto = incierto


class Telegram:
    def __init__(self, token):
        if not re.fullmatch(r'\d+:[A-Za-z0-9_-]+', token or ''):
            raise ValueError('Falta el token del bot exclusivo de informes')
        self._base = f'https://api.telegram.org/bot{token}/'

    def llamar(self, metodo, data=None, files=None):
        try:
            response = requests.post(self._base + metodo, data=data, files=files, timeout=(15, 120))
            result = response.json()
        except requests.ConnectTimeout:
            raise ErrorTelegram('No se pudo conectar con Telegram') from None
        except (requests.RequestException, ValueError):
            raise ErrorTelegram('Respuesta de Telegram no confirmada; requiere revisión antes de reenviar', True) from None
        if not result.get('ok'):
            code = result.get('error_code', response.status_code)
            # Nunca incluir URLs de petición ni excepciones que contengan el token.
            raise ErrorTelegram(f'Telegram rechazó la operación (código {code})', code >= 500)
        return result['result']

    def enviar(self, chat_id, texto=None, archivo=None):
        if archivo is None:
            result = self.llamar('sendMessage', {'chat_id': chat_id, 'text': texto})
        else:
            with open(archivo, 'rb') as f:
                result = self.llamar('sendDocument', {'chat_id': chat_id}, {'document': (Path(archivo).name, f)})
        return result['message_id']


class Emisor:
    def __init__(self, raiz, token, destinatarios, inicio, uploads, fuente=leer_datos, transporte=None):
        self.raiz, self.uploads = Path(raiz), Path(uploads)
        self.destinatarios = list(dict.fromkeys(str(x).strip() for x in destinatarios if str(x).strip()))
        if not self.destinatarios or any(not re.fullmatch(r'-?\d+', x) for x in self.destinatarios):
            raise ValueError('Destinatarios de Telegram inválidos')
        self.inicio, self.fuente = date.fromisoformat(str(inicio)), fuente
        self.telegram = transporte if transporte is not None else Telegram(token)
        self.raiz.mkdir(parents=True, exist_ok=True)
        self.db_path = self.raiz / 'entregas.sqlite3'
        with self.db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS entregas (
                lote TEXT, chat TEXT, parte TEXT, estado TEXT, actualizado TEXT,
                message_id INTEGER, error TEXT, PRIMARY KEY (lote, chat, parte))''')

    @contextmanager
    def db(self):
        conn = sqlite3.connect(self.db_path, timeout=10)
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def enviar_parte(self, lote, chat, parte, accion):
        with self.db() as db:
            row = db.execute('SELECT estado, actualizado FROM entregas WHERE lote=? AND chat=? AND parte=?',
                             (lote, chat, parte)).fetchone()
            if row and row[0] in ('enviado', 'enviando', 'incierto'):
                return
            if row and (ahora_ecuador() - datetime.fromisoformat(row[1])).total_seconds() < 300:
                return
            db.execute('INSERT OR REPLACE INTO entregas VALUES (?,?,?,?,?,?,?)',
                       (lote, chat, parte, 'enviando', ahora_ecuador().isoformat(), None, None))
        try:
            message_id = accion()
            estado, error = 'enviado', None
        except ErrorTelegram as ex:
            message_id, estado, error = None, 'incierto' if ex.incierto else 'error', str(ex)
        except Exception:
            message_id, estado, error = None, 'incierto', 'Entrega interrumpida; requiere revisión'
        with self.db() as db:
            db.execute('UPDATE entregas SET estado=?, actualizado=?, message_id=?, error=? WHERE lote=? AND chat=? AND parte=?',
                       (estado, ahora_ecuador().isoformat(), message_id, error, lote, chat, parte))
        if error:
            LOG.warning('Informe %s, destino %s, parte %s: %s', lote, chat, parte, error)

    def procesar_dia(self, dia, corte, prueba=False):
        lote = ('prueba-' + corte.strftime('%Y%m%dT%H%M%S')) if prueba else dia.isoformat()
        folder = self.raiz / lote
        manifest_path = folder / 'manifest.json'
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
            esperadas = {(chat, parte) for chat in self.destinatarios
                         for parte in ['resumen'] + [a['nombre'] for a in manifest['archivos']]}
            with self.db() as db:
                enviadas = set(db.execute('SELECT chat, parte FROM entregas WHERE lote=? AND estado=?',
                                         (lote, 'enviado')).fetchall())
            if esperadas <= enviadas:
                return lote
        else:
            registros, pagos = self.fuente()
            datos = seleccionar_datos(registros, pagos, dia, corte)
            manifest = construir_paquete(datos, self.uploads, folder, prueba)
        for item in manifest['archivos']:
            if hashlib.sha256((folder / item['nombre']).read_bytes()).hexdigest() != item['sha256']:
                raise ValueError('Un adjunto cambió después del corte; informe detenido')
        for chat in self.destinatarios:
            self.enviar_parte(lote, chat, 'resumen', lambda: self.telegram.enviar(chat, texto=manifest['resumen']))
            for item in manifest['archivos']:
                name = item['nombre']
                self.enviar_parte(lote, chat, name, lambda: self.telegram.enviar(chat, archivo=folder / name))
        return lote

    def tick(self, now=None):
        now = (now or ahora_ecuador()).astimezone(ECUADOR)
        ultimo = now.date() if now.time() >= time(23, 59) else now.date() - timedelta(days=1)
        with bloqueo_proceso(self.raiz / '.emisor.lock') as acquired:
            if not acquired:
                return
            self.limpiar_adjuntos_entregados(now)
            dia = self.inicio
            while dia <= ultimo:
                self.procesar_dia(dia, now)
                dia += timedelta(days=1)

    def limpiar_adjuntos_entregados(self, now):
        """Solo copias generadas y entregadas, después de siete días; conserva fuente y ledger."""
        for manifest_path in self.raiz.glob('*/manifest.json'):
            if now.timestamp() - manifest_path.stat().st_mtime < 7 * 86400:
                continue
            manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
            esperadas = {(chat, parte) for chat in self.destinatarios
                         for parte in ['resumen'] + [a['nombre'] for a in manifest['archivos']]}
            with self.db() as db:
                enviadas = set(db.execute('SELECT chat, parte FROM entregas WHERE lote=? AND estado=?',
                                         (manifest_path.parent.name, 'enviado')).fetchall())
            if not esperadas <= enviadas:
                continue
            for item in manifest['archivos']:
                name = item['nombre']
                if Path(name).name != name or not (name in ('LEER_INFORME.html', 'DETALLE.csv') or re.fullmatch(r'RETIROS_\d{4}-\d{2}-\d{2}_\d+_de_\d+\.zip', name)):
                    continue
                path = manifest_path.parent / name
                if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == item['sha256']:
                    path.unlink()


def desde_entorno(uploads=None):
    return Emisor(os.environ.get('INFORMES_TELEGRAM_DIR', '/var/data/informes_telegram'),
                  os.environ.get('INFORMES_TELEGRAM_BOT_TOKEN', ''),
                  os.environ.get('INFORMES_TELEGRAM_CHAT_IDS', '').split(','),
                  os.environ['INFORMES_TELEGRAM_DESDE'],
                  uploads or os.environ.get('INFORMES_TELEGRAM_UPLOADS', '/var/data/uploads'))


def iniciar_programador(uploads):
    if os.environ.get('INFORMES_TELEGRAM_ACTIVO', '').lower() != 'true':
        return None
    def ejecutar():
        try:
            emisor = desde_entorno(uploads)
        except Exception:
            LOG.error('Informes diarios: configuración inválida; emisor desactivado')
            return
        LOG.warning('Informes diarios activos: 23:59 Ecuador; %s destinatarios', len(emisor.destinatarios))
        while True:
            try:
                emisor.tick()
            except Exception as ex:
                LOG.error('No se completó el ciclo del informe (%s); se reintentará', type(ex).__name__)
            threading.Event().wait(30)
    hilo = threading.Thread(target=ejecutar, name='informes-telegram-diarios', daemon=True)
    hilo.start()
    return hilo


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--comprobar', action='store_true')
    parser.add_argument('--prueba', action='store_true')
    args = parser.parse_args()
    emisor = desde_entorno()
    if args.comprobar:
        me = emisor.telegram.llamar('getMe')
        print(json.dumps({'bot': me.get('username'), 'hora': '23:59 America/Guayaquil', 'desde': str(emisor.inicio)}))
        for chat in emisor.destinatarios:
            try:
                result = emisor.telegram.llamar('getChat', {'chat_id': chat})
                print(json.dumps({'chat': str(result['id']), 'nombre': result.get('first_name') or result.get('title'), 'accesible': True}))
            except ErrorTelegram as ex:
                print(json.dumps({'chat': chat, 'accesible': False, 'error': str(ex)}))
    elif args.prueba:
        now = ahora_ecuador()
        with bloqueo_proceso(emisor.raiz / '.emisor.lock') as acquired:
            if not acquired:
                raise RuntimeError('Hay otro informe en curso')
            lote = emisor.procesar_dia(now.date(), now, prueba=True)
        with emisor.db() as db:
            print(json.dumps(db.execute('SELECT chat, parte, estado, message_id FROM entregas WHERE lote=?', (lote,)).fetchall()))
    else:
        emisor.tick()


if __name__ == '__main__':
    main()
