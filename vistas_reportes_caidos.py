"""Interfaz de caídos y aviso diario en pantalla. No envía mensajes externos."""

import os
import secrets
from decimal import Decimal, InvalidOperation

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, session, url_for
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import create_engine

from reportes_caidos import AlmacenReportes, ahora_local, catalogo_caidos, clave_estable, es_prueba, filtrar_agrupar, texto_fecha


def registrar_reportes_caidos(app, servicios):
    bp = Blueprint('caidos', __name__, url_prefix='/reportes-caidos')
    app.extensions['almacen_reportes_caidos'] = None

    def almacen():
        if app.extensions['almacen_reportes_caidos'] is None:
            motor = servicios['motor']()
            if motor is None:
                ruta = os.path.join(os.path.dirname(os.path.abspath(servicios['archivo']())), 'reportes_caidos.sqlite3')
                motor = create_engine('sqlite:///' + ruta.replace('\\', '/'))
            app.extensions['almacen_reportes_caidos'] = AlmacenReportes(motor)
        return app.extensions['almacen_reportes_caidos']

    def permisos():
        usuario = servicios['usuario']()
        rol = usuario.get('rol') if usuario.get('estado', 'Activo') == 'Activo' else None
        return rol in ('supremo', 'recaudador'), rol == 'reportes', rol in ('supremo', 'reportes')

    def comprobar(accion='leer'):
        solicitar, atender, aviso = permisos()
        permitido = {'leer': solicitar or atender, 'solicitar': solicitar, 'atender': atender, 'aviso': aviso}[accion]
        if not permitido:
            abort(403)

    def csrf():
        return session.setdefault('csrf_caidos', secrets.token_urlsafe(32))

    def comprobar_csrf():
        if not request.form.get('csrf_token') or not secrets.compare_digest(
                request.form['csrf_token'], session.get('csrf_caidos', '')):
            abort(400, description='El formulario venció. Recarga la página y vuelve a intentarlo.')

    def catalogo():
        registros, enlaces = servicios['datos']()
        return catalogo_caidos(registros, enlaces, incluir_vencidos=True)

    def serializador():
        return URLSafeTimedSerializer(app.secret_key, salt='seleccion-reportes-caidos-v1')

    def filtros():
        return {k: request.values.get(k, '')[:160] for k in ('busqueda', 'desde', 'hasta')}

    def contexto(**valores):
        usuario = servicios['usuario']()
        return dict(mi_usuario=session.get('usuario'), rol=usuario.get('rol'),
                    mis_permisos=usuario.get('permisos') or [], csrf_token=csrf(), **valores)

    def leer_seleccion(token):
        try:
            seleccion = serializador().loads(token, max_age=1800)
        except (BadSignature, SignatureExpired):
            abort(400, description='La selección venció o cambió. Vuelve al listado y selecciona los códigos.')
        if (not isinstance(seleccion, dict) or seleccion.get('actor') != session.get('usuario') or
                not isinstance(seleccion.get('claves'), list) or not 1 <= len(seleccion['claves']) <= 200):
            abort(400)
        actuales = catalogo()
        codigos = []
        for clave in dict.fromkeys(seleccion['claves']):
            codigo = actuales.get(clave)
            if not codigo or seleccion.get('huellas', {}).get(clave) != clave_estable(codigo) or not codigo['solicitable'] or not any(
                    c['id'] == seleccion.get('grupo') for c in codigo['clientes']):
                abort(409, description='Un código cambió desde que abriste el listado. Recarga antes de reportar.')
            codigos.append(codigo)
        return seleccion, codigos

    @app.context_processor
    def acceso_caidos():
        solicitar, atender, aviso = permisos()
        return dict(puede_ver_caidos=solicitar, puede_atender_caidos=atender, recibe_aviso_caidos=aviso)

    @app.template_filter('fecha_solicitud')
    def formato_fecha(valor):
        return texto_fecha(valor)

    @bp.after_request
    def sin_cache(respuesta):
        respuesta.headers['Cache-Control'] = 'no-store'
        return respuesta

    @bp.errorhandler(400)
    @bp.errorhandler(409)
    def error_esperado(error):
        return render_template('caidos_mensaje.html', **contexto(titulo='Revisa la solicitud',
            mensaje=error.description, volver=url_for('caidos.listado'))), error.code

    @bp.errorhandler(Exception)
    def error_almacen(error):
        from werkzeug.exceptions import HTTPException
        if isinstance(error, HTTPException):
            return error
        app.logger.exception('No se pudo completar la operación de reportes caídos')
        return render_template('caidos_mensaje.html', **contexto(titulo='No se pudo completar la operación',
            mensaje='No se confirmó ningún cambio. Vuelve a intentarlo; los retiros no se han modificado.',
            volver=url_for('caidos.listado'))), 503

    @bp.route('')
    def listado():
        comprobar('solicitar')
        opciones = filtros()
        try:
            actuales = catalogo()
            grupos = filtrar_agrupar(actuales, almacen().solicitudes(), **opciones)
        except ValueError as error:
            abort(400, description=str(error))
        for grupo in grupos:
            claves = grupo['pendientes'][:200]
            grupo['token'] = serializador().dumps(dict(actor=session['usuario'], grupo=grupo['id'],
                claves=claves, filtros=opciones, huellas={k: clave_estable(actuales[k]) for k in claves})) if claves else ''
            grupo['cantidad_seleccion'] = len(claves)
        return render_template('reportes_caidos.html', **contexto(grupos=grupos, filtros=opciones,
            titulo='Reportes', subtitulo='Códigos caídos y vencidos de todos los cobradores', vista='listado'))

    @bp.route('/confirmar', methods=['GET', 'POST'])
    def confirmar():
        comprobar('solicitar')
        if request.method == 'POST':
            comprobar_csrf()
        token = request.values.get('seleccion', '')
        seleccion, codigos = leer_seleccion(token)
        existentes = almacen().solicitudes()
        nuevos = [c for c in codigos if c['clave'] not in existentes]
        volver = url_for('caidos.listado', **seleccion.get('filtros', {}))
        if request.method == 'POST' and request.form.get('accion') != 'previsualizar':
            cantidad = almacen().solicitar(nuevos, session['usuario'])
            flash(f'Solicitud guardada: {cantidad} código(s). Aparecerán en la ventana Códigos solicitados.'
                  if cantidad else 'Estos códigos ya tienen una solicitud registrada. No se duplicaron.', 'success')
            return redirect(volver)
        return render_template('caidos_confirmar.html', **contexto(codigos=nuevos, seleccion=token,
            ya_solicitados=len(codigos) - len(nuevos), volver=volver))

    @bp.route('/solicitudes')
    def solicitudes():
        comprobar()
        estado = request.args.get('estado', 'pendientes')
        if estado not in ('pendientes', 'atendidas', 'todas'):
            abort(400, description='Selecciona un estado válido.')
        actuales = catalogo()
        valores = [dict(s, vigente=s['clave'] in actuales) for s in almacen().solicitudes().values()]
        valores = [s for s in valores if estado == 'todas' or
                   (estado == 'atendidas' and s['atendido_en']) or
                   (estado == 'pendientes' and not s['atendido_en'] and s['vigente'])]
        valores.sort(key=lambda s: s['solicitado_en'], reverse=True)
        return render_template('caidos_solicitudes.html', **contexto(solicitudes=valores,
            estado=estado, vista='solicitudes'))

    def obtener_codigo(clave):
        solicitud = almacen().solicitudes().get(clave)
        codigo = catalogo().get(clave)
        vigente = codigo is not None
        if not codigo and solicitud:
            codigo = solicitud['datos']
        if not codigo:
            abort(404)
        return codigo, solicitud, vigente

    @bp.route('/codigo/<clave>')
    def detalle(clave):
        comprobar()
        codigo, solicitud, vigente = obtener_codigo(clave)
        return render_template('caidos_detalle.html', **contexto(codigo=codigo, solicitud=solicitud,
            volver=url_for('caidos.listado', **filtros()) if permisos()[0] else url_for('caidos.solicitudes'),
            filtros=filtros(), vigente=vigente))

    @bp.route('/codigo/<clave>/comprobante/<filename>')
    def comprobante(clave, filename):
        from flask import send_from_directory
        comprobar()
        codigo, _, _ = obtener_codigo(clave)
        if filename not in codigo['archivos']:
            abort(404)
        return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

    @bp.route('/atender/<clave>', methods=['POST'])
    def atender(clave):
        comprobar('atender')
        comprobar_csrf()
        solicitud = almacen().solicitudes().get(clave)
        if not solicitud:
            abort(404)
        if not solicitud['atendido_en'] and clave not in catalogo():
            abort(409, description='Este código ya no necesita volver a reportarse: cambió de estado o tiene un pago disponible para cruzar. Su solicitud se conserva en el historial.')
        nota = request.form.get('nota', '').strip() or 'Confirmado como atendido por el rol Reportes.'
        try:
            cambiado = almacen().atender(clave, session['usuario'], nota)
        except ValueError as error:
            abort(400, description=str(error))
        if request.form.get('desde_aviso') == '1':
            return jsonify(ok=True, cambiado=cambiado)
        flash('Solicitud marcada como atendida. Los pagos y deudas se conservan.' if cambiado else
              'La solicitud ya estaba atendida.', 'success')
        return redirect(url_for('caidos.detalle', clave=clave))

    @bp.route('/resumen-pendientes')
    def resumen_pendientes():
        """El resumen original conserva sus contadores generales, sin solicitudes ni filtros de cruce."""
        comprobar('aviso')
        registros, _ = servicios['datos']()
        caidos = sum(r.get('estado') in ('fallido', 'fallido_revision') for r in registros if not es_prueba(r))
        vencidos = sum(r.get('estado') == 'expirado' for r in registros if not es_prueba(r))
        return jsonify(mostrar=bool(caidos or vencidos), cantidad_caidos=caidos, cantidad_expirados=vencidos,
                       historial=url_for('vista_reportes', vista='historial'))

    @bp.route('/aviso-diario')
    def aviso_diario():
        """Ventana independiente: solo códigos que un recaudador solicitó y aún requieren reporte."""
        comprobar('aviso')
        codigos = catalogo()
        solicitudes = [s for s in almacen().solicitudes().values() if not s['atendido_en'] and s['clave'] in codigos]
        solicitudes.sort(key=lambda s: s['solicitado_en'])
        importe = Decimal(0)
        for solicitud in solicitudes:
            try:
                monto = Decimal(str(solicitud['datos'].get('monto', 0)))
                if monto.is_finite() and monto > 0:
                    importe += monto
            except (InvalidOperation, ValueError, TypeError):
                pass
        totales = dict(total_solicitudes=len(solicitudes),
                       total_usuarios=len({s['datos']['usuario_original'] for s in solicitudes}),
                       total_monto=format(importe, '.2f'))
        hoy = ahora_local().date().isoformat()
        if not solicitudes or (request.args.get('abrir') != '1' and almacen().aviso_visto(session['usuario'], hoy)):
            return jsonify(mostrar=False, html='', **totales)
        return jsonify(mostrar=True, csrf_token=csrf(), **totales,
            html=render_template('_contenido_aviso_caidos.html', solicitudes=solicitudes[:30],
                                 puede_atender=permisos()[1], **totales))

    @bp.route('/aviso-visto', methods=['POST'])
    def aviso_visto():
        comprobar('aviso')
        comprobar_csrf()
        almacen().marcar_aviso_visto(session['usuario'])
        return jsonify(ok=True)

    app.register_blueprint(bp)
    return almacen
