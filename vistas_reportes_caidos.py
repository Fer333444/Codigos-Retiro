"""Interfaz de caídos y aviso diario en pantalla. No envía mensajes externos."""

import os
import secrets

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, session, url_for
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import create_engine

from reportes_caidos import AlmacenReportes, ahora_local, catalogo_caidos, clave_estable, filtrar_agrupar, texto_fecha


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
            flash(f'Solicitud guardada: {cantidad} código(s). Aparecerán en el aviso diario de Reportes.'
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
        valores = list(almacen().solicitudes().values())
        valores = [s for s in valores if estado == 'todas' or bool(s['atendido_en']) == (estado == 'atendidas')]
        valores.sort(key=lambda s: s['solicitado_en'], reverse=True)
        return render_template('caidos_solicitudes.html', **contexto(solicitudes=valores,
            estado=estado, vista='solicitudes'))

    def obtener_codigo(clave):
        solicitud = almacen().solicitudes().get(clave)
        codigo = catalogo().get(clave)
        if not codigo and solicitud:
            codigo = solicitud['datos']
        if not codigo:
            abort(404)
        return codigo, solicitud

    @bp.route('/codigo/<clave>')
    def detalle(clave):
        comprobar()
        codigo, solicitud = obtener_codigo(clave)
        return render_template('caidos_detalle.html', **contexto(codigo=codigo, solicitud=solicitud,
            volver=url_for('caidos.listado', **filtros()) if permisos()[0] else url_for('caidos.solicitudes'),
            filtros=filtros()))

    @bp.route('/codigo/<clave>/comprobante/<filename>')
    def comprobante(clave, filename):
        from flask import send_from_directory
        comprobar()
        codigo, _ = obtener_codigo(clave)
        if filename not in codigo['archivos']:
            abort(404)
        return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

    @bp.route('/atender/<clave>', methods=['POST'])
    def atender(clave):
        comprobar('atender')
        comprobar_csrf()
        if clave not in almacen().solicitudes():
            abort(404)
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

    @bp.route('/aviso-diario')
    def aviso_diario():
        comprobar('aviso')
        hoy = ahora_local().date().isoformat()
        if almacen().aviso_visto(session['usuario'], hoy):
            return jsonify(mostrar=False)
        codigos = catalogo()
        solicitudes = [s for s in almacen().solicitudes().values() if not s['atendido_en']]
        solicitudes.sort(key=lambda s: s['solicitado_en'])
        caidos = sum(c['estado'] in ('fallido', 'fallido_revision') for c in codigos.values())
        vencidos = sum(c['estado'] == 'expirado' for c in codigos.values())
        if not solicitudes and not caidos and not vencidos:
            return jsonify(mostrar=False)
        return jsonify(mostrar=True, csrf_token=csrf(), html=render_template('_contenido_aviso_caidos.html',
            solicitudes=solicitudes[:30], total_solicitudes=len(solicitudes), cantidad_caidos=caidos,
            cantidad_expirados=vencidos, puede_atender=permisos()[1]))

    @bp.route('/aviso-visto', methods=['POST'])
    def aviso_visto():
        comprobar('aviso')
        comprobar_csrf()
        almacen().marcar_aviso_visto(session['usuario'])
        return jsonify(ok=True)

    app.register_blueprint(bp)
    return almacen
