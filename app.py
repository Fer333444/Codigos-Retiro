import os
import random
import string
import csv
import io
import json
import base64
import time
from pywebpush import webpush, WebPushException
from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify, send_from_directory
from werkzeug.utils import secure_filename
from datetime import datetime, timedelta

# --- CONFIGURACIÓN DE INTELIGENCIA ARTIFICIAL ---
from openai import OpenAI
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY") 
openai_client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

# --- CONFIGURACIÓN DE NOTIFICACIONES PUSH (VAPID) ---
VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY")
VAPID_PUBLIC_KEY = os.environ.get("VAPID_PUBLIC_KEY")
VAPID_CLAIMS = {"sub": "mailto:contenido2025yt@gmail.com"}

app = Flask(__name__)
app.secret_key = "flujo_secreto_123"
app.permanent_session_lifetime = timedelta(days=365)

# ==========================================
# 💾 SISTEMA DE DISCO DURO PERSISTENTE 💾
# ==========================================
# Si el código detecta que está en Render, guarda JSON y FOTOS en el disco blindado (/var/data)
if os.path.exists('/var/data'):
    DATA_FILE = '/var/data/base_datos_erp.json'
    UPLOAD_FOLDER = '/var/data/uploads'
else:
    DATA_FILE = 'base_datos_local.json'
    UPLOAD_FOLDER = 'static/uploads'

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
os.makedirs(UPLOAD_FOLDER, exist_ok=True) # Construye la bóveda de fotos si no existe

# ==========================================
# 📸 RUTA MÁGICA PARA LEER LAS FOTOS DEL DISCO
# ==========================================
@app.route('/ver_imagen/<filename>')
def ver_imagen(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

# --- Variables Globales ---
registros = []
sistema_config = {'auto_asignar': False}
enlaces_db = {}
grupos_creados = [] 
liquidaciones_db = {}
ubicaciones_cobradores = {}
historial_pagos = []
suscripciones_push = {}

def guardar_datos():
    data_a_guardar = {
        'registros': registros,
        'sistema_config': sistema_config,
        'enlaces_db': enlaces_db,
        'grupos_creados': grupos_creados,
        'usuarios_db': usuarios_db,
        'historial_pagos': historial_pagos,
        'suscripciones_push': suscripciones_push # <-- AQUÍ GUARDAMOS LOS PERMISOS PUSH
    }
    try:
        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(data_a_guardar, f, ensure_ascii=False, indent=4)
    except Exception as e:
        print("Error crítico al guardar en disco:", e)

def cargar_datos():
    global registros, sistema_config, enlaces_db, grupos_creados, usuarios_db, historial_pagos, suscripciones_push
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                registros = data.get('registros', [])
                sistema_config = data.get('sistema_config', {'auto_asignar': False})
                enlaces_db = data.get('enlaces_db', {})
                grupos_creados = data.get('grupos_creados', [])
                
                usuarios_cargados = data.get('usuarios_db', {})
                if usuarios_cargados:
                    usuarios_db = usuarios_cargados
                    
                historial_pagos = data.get('historial_pagos', [])
                suscripciones_push = data.get('suscripciones_push', {}) # <-- AQUÍ CARGAMOS LOS PERMISOS PUSH
            print("✅ Base de datos cargada exitosamente desde el Disco.")
        except Exception as e:
            print("Error crítico al cargar desde el disco:", e)

cargar_datos()
# ==========================================

def extraer_datos_imagen_ocr(image_bytes_list):
    if not openai_client: return None
    try:
        content_list = []
        prompt_experto = """
        Actúa como un Especialista en Extracción de Datos de Códigos de Retiro (Ecuador).
        Analiza TODAS las imágenes enviadas juntas y cruza la información para armar el JSON.
        
        REGLAS DE BANCOS:
        1. BANCO GUAYAQUIL: Si dice "Efectivo móvil" o tiene DOS códigos. 
           - "CLAVE_ENVIO" = el primer código. "CLAVE_RETIRO" = el segundo código.
        2. BANCO PICHINCHA: Si dice "Pichincha" y da un solo código de retiro.
           - "CLAVE_RETIRO" = el código.
        3. BANCO PRODUBANCO: Si menciona "Produbanco" u "Orden de retiro sin tarjeta".
           - Busca el código de 6 dígitos que a veces llega por SMS.
           - "CLAVE_RETIRO" = ese código de 6 dígitos.

        FORMATO JSON ESTRICTO:
        {
         "BANCO": "pichincha, guayaquil, produbanco u otro",
         "MONTO": "Solo el número (ej. 50.00)",
         "CELULAR": "Número de celular asociado o ''",
         "CEDULA": "Número de cédula, C.I., o documento de identidad si aparece, sino ''",
         "CLAVE_RETIRO": "El código PIN principal",
         "CLAVE_ENVIO": "Código de envío (solo Guayaquil)"
        }
        NO inventes datos si no están en ninguna foto.
        """
        content_list.append({"type": "text", "text": prompt_experto})
        for img_bytes in image_bytes_list:
            base64_image = base64.b64encode(img_bytes).decode("utf-8")
            content_list.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}})
            
        response = openai_client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": content_list}],
            max_tokens=300,
            response_format={"type": "json_object"}
        )
        return json.loads(response.choices[0].message.content)
    except Exception as e:
        print(f"Error procesando OCR: {e}")
        return None

@app.route('/api/procesar_ocr', methods=['POST'])
def procesar_ocr():
    archivos = request.files.getlist('imagenes')
    if not archivos or archivos[0].filename == '':
        return jsonify({"error": "No se enviaron imágenes"}), 400
    image_bytes_list = [f.read() for f in archivos]
    datos_ia = extraer_datos_imagen_ocr(image_bytes_list)
    if datos_ia:
        return jsonify(datos_ia)
    else:
        return jsonify({"error": "Error procesando IA"}), 500

def hora_ecuador():
    return datetime.utcnow() - timedelta(hours=5)

def esta_expirado(hora_limite_str, fecha_creacion_str):
    if not hora_limite_str: return False
    try:
        ahora = hora_ecuador()
        creacion = datetime.strptime(fecha_creacion_str, "%d/%m/%Y %H:%M")
        h, m = map(int, hora_limite_str.split(':'))
        fecha_objetivo = creacion.replace(hour=h, minute=m, second=0, microsecond=0)
        if fecha_objetivo < creacion and (creacion.hour - fecha_objetivo.hour > 12):
            fecha_objetivo += timedelta(days=1)
        return ahora >= fecha_objetivo
    except Exception as e:
        return False

@app.before_request
def mantenimiento_datos():
    cambios_realizados = False
    hora_actual = hora_ecuador().strftime('%H:%M')
    tiempo_ahora = time.time()
# --- 🛠️ AUTO-REPARADOR DE IDs DUPLICADOS ---
    ids_vistos = set()
    # Leemos la lista al revés para que los códigos viejos conserven su ID original
    for r in reversed(registros):
        if r.get('id') in ids_vistos:
            # Si encontramos un clon, le damos un ID único basado en milisegundos
            r['id'] = int(tiempo_ahora * 1000) + random.randint(1, 9999)
            cambios_realizados = True
        ids_vistos.add(r.get('id'))
    
    for r in registros:
        if r['estado'] == 'activo':
            if 'expira_timestamp' in r:
                if tiempo_ahora >= r['expira_timestamp']:
                    r['estado'] = 'expirado'
                    r['historial'].append(f"[{hora_actual}] ❌ Expirado automáticamente (Tiempo agotado)")
                    cambios_realizados = True
            elif esta_expirado(r.get('hora_limite'), r.get('fecha')):
                r['estado'] = 'expirado'
                r['historial'].append(f"[{hora_actual}] ❌ Expirado automáticamente (Tiempo agotado)")
                cambios_realizados = True
            
    for k, v in enlaces_db.items():
        if 'grupo' not in v:
            v['grupo'] = 'General'
            cambios_realizados = True
            
    if cambios_realizados:
        guardar_datos()

def ruta_por_rol(rol, usuario):
    # Obtenemos los permisos exactos que tiene el usuario en la sesión
    permisos = session.get('permisos', [])
    
    # 1. El Supremo siempre va a la pantalla principal
    if rol == 'supremo':
        return '/'
        
    # 2. Si el usuario tiene casillas marcadas, lo enviamos a la primera que tenga activa
    if 'crear_links' in permisos:
        return '/'
    elif 'gestionar_grupos' in permisos:
        return '/grupos'
    elif 'ver_retiros' in permisos:
        return '/admin'
    elif 'procesar_retiros' in permisos:
        return f'/trabajador/{usuario}'
    elif 'ver_reportes' in permisos:
        return '/reportes'
    elif 'gestionar_usuarios' in permisos:
        return '/usuarios'
        
    # 3. Rutas por defecto (Solo se usan si el usuario no tiene NINGUNA casilla marcada)
    if rol == 'recaudador': 
        return '/admin'
    if rol == 'cobrador': 
        return f'/trabajador/{usuario}'
    if rol == 'reportes': 
        return '/reportes'
        
    return '/'

@app.route('/')
def index():
    if 'usuario' not in session: return redirect(url_for('login'))
    mis_permisos = session.get('permisos', [])
    if session.get('rol') != 'supremo' and 'crear_links' not in mis_permisos:
        flash('No tienes permiso para ver los links.', 'error')
        return redirect(ruta_por_rol(session.get('rol'), session.get('usuario')))
        
    horario = sistema_config.get('horario_activo', True)
    # Cargar el estado de los bancos (si no existe, por defecto todos activos)
    bancos_activos = sistema_config.get('bancos_activos', {'pichincha': True, 'guayaquil': True, 'produbanco': True})
    
    return render_template('index.html', enlaces=enlaces_db, mi_usuario=session['usuario'], rol=session.get('rol'), base_url=request.host_url, grupos=grupos_creados, horario_activo=horario, bancos_activos=bancos_activos)
# 2. AGREGA ESTA NUEVA RUTA PARA EL INTERRUPTOR
@app.route('/toggle_horario', methods=['POST'])
def toggle_horario():
    mis_permisos = session.get('permisos', [])
    if session.get('rol') != 'supremo' and 'crear_links' not in mis_permisos: 
        return redirect(url_for('login'))
        
    sistema_config['horario_activo'] = not sistema_config.get('horario_activo', True)
    guardar_datos()
    
    if sistema_config['horario_activo']:
        flash('🟢 Horario ABIERTO. Los clientes ya pueden enviar códigos.', 'success')
    else:
        flash('🔴 Horario CERRADO. Formularios de clientes bloqueados.', 'error')
    return redirect(url_for('index'))
@app.route('/toggle_banco', methods=['POST'])
def toggle_banco():
    mis_permisos = session.get('permisos', [])
    if session.get('rol') != 'supremo' and 'crear_links' not in mis_permisos: 
        return redirect(url_for('login'))
        
    banco = request.form.get('banco')
    if 'bancos_activos' not in sistema_config:
        sistema_config['bancos_activos'] = {'pichincha': True, 'guayaquil': True, 'produbanco': True}
        
    if banco in sistema_config['bancos_activos']:
        # Invierte el valor (True a False, o False a True)
        sistema_config['bancos_activos'][banco] = not sistema_config['bancos_activos'][banco]
        guardar_datos()
        
        estado = "ACTIVADO" if sistema_config['bancos_activos'][banco] else "DESACTIVADO (FUERA DE SERVICIO)"
        if sistema_config['bancos_activos'][banco]:
            flash(f'✅ Banco {banco.capitalize()} {estado}.', 'success')
        else:
            flash(f'🚫 Banco {banco.capitalize()} {estado}.', 'error')
            
    return redirect(url_for('index'))

@app.route('/editar_link', methods=['POST'])
def editar_link():
    mis_permisos = session.get('permisos', [])
    if session.get('rol') != 'supremo' and 'crear_links' not in mis_permisos: return redirect(url_for('login'))
    viejo_token = request.form.get('token')
    nuevo_usuario = request.form.get('usuario')
    nuevo_grupo = request.form.get('grupo', 'General')
    if not viejo_token or viejo_token not in enlaces_db:
        flash('Enlace no encontrado.', 'error')
        return redirect(url_for('index'))
    if not nuevo_usuario or not nuevo_usuario.strip():
        flash('El nombre de usuario no puede estar vacío.', 'error')
        return redirect(url_for('index'))
    nuevo_usuario = nuevo_usuario.strip()
    nuevo_token = nuevo_usuario.replace(' ', '-')
    nuevo_grupo = nuevo_grupo.strip()
    if nuevo_token != viejo_token and nuevo_token in enlaces_db:
        flash('Ya existe un cliente con ese nombre.', 'error')
        return redirect(url_for('index'))
    datos = enlaces_db[viejo_token]
    datos['usuario'] = nuevo_usuario
    datos['grupo'] = nuevo_grupo
    if nuevo_token != viejo_token:
        enlaces_db[nuevo_token] = datos
        del enlaces_db[viejo_token]
    if nuevo_grupo != 'General' and nuevo_grupo not in grupos_creados:
        grupos_creados.append(nuevo_grupo)
        
    guardar_datos()
    flash(f'✅ Cliente "{nuevo_usuario}" actualizado correctamente.', 'success')
    return redirect(url_for('index'))

@app.route('/eliminar_link', methods=['POST'])
def eliminar_link():
    mis_permisos = session.get('permisos', [])
    if session.get('rol') != 'supremo' and 'crear_links' not in mis_permisos: return redirect(url_for('login'))
    token = request.form.get('token')
    if token in enlaces_db:
        usuario_borrado = enlaces_db[token]['usuario']
        del enlaces_db[token]
        guardar_datos()
        flash(f'🗑️ El cliente "{usuario_borrado}" y su enlace han sido eliminados.', 'success')
    else:
        flash('Error: El cliente no existe.', 'error')
    return redirect(url_for('index'))

@app.route('/grupos')
@app.route('/grupos')
def vista_grupos():
    mis_permisos = session.get('permisos', [])
    if session.get('rol') != 'supremo' and 'gestionar_grupos' not in mis_permisos: return redirect(url_for('login'))
    
    # LIMPIEZA AUTOMÁTICA: Borramos las carpetas "Activaciones" que se hayan creado por error antes
    global grupos_creados
    grupos_creados = [g for g in grupos_creados if not g.startswith('Activaciones')]
    guardar_datos()
    
    grupos_validos = [g for g in grupos_creados if g != 'General']
    usuarios_por_grupo = {g: [] for g in grupos_validos}
    todos = sorted(list(set(data['usuario'] for data in enlaces_db.values())))
    
    for token, data in enlaces_db.items():
        g = data.get('grupo', 'General')
        if g in usuarios_por_grupo: 
            usuarios_por_grupo[g].append({'token': token, 'data': data})
            
    return render_template('grupos.html', grupos=grupos_validos, usuarios_por_grupo=usuarios_por_grupo, todos_los_usuarios=todos, mi_usuario=session['usuario'], rol=session.get('rol'), base_url=request.host_url)

@app.route('/crear_grupo_vacio', methods=['POST'])
def crear_grupo_vacio():
    mis_permisos = session.get('permisos', [])
    if session.get('rol') != 'supremo' and 'gestionar_grupos' not in mis_permisos: return redirect(url_for('login'))
    nombre = request.form.get('nombre_grupo')
    if nombre and nombre.strip() != 'General' and nombre.strip() not in grupos_creados:
        grupos_creados.append(nombre.strip())
        guardar_datos()
        flash(f'✅ Grupo "{nombre}" creado.', 'success')
    return redirect(url_for('vista_grupos'))

@app.route('/renombrar_grupo', methods=['POST'])
def renombrar_grupo():
    mis_permisos = session.get('permisos', [])
    if session.get('rol') != 'supremo' and 'gestionar_grupos' not in mis_permisos: return redirect(url_for('login'))
    viejo_nombre = request.form.get('viejo_nombre')
    nuevo_nombre = request.form.get('nuevo_nombre')
    if not viejo_nombre or not nuevo_nombre or not nuevo_nombre.strip() or nuevo_nombre.strip() == 'General':
        flash('Nombre de grupo inválido.', 'error')
        return redirect(url_for('vista_grupos'))
    nuevo_nombre = nuevo_nombre.strip()
    if nuevo_nombre in grupos_creados:
        flash('Ya existe un grupo con ese nombre. Elige otro.', 'error')
        return redirect(url_for('vista_grupos'))
    if viejo_nombre in grupos_creados:
        idx = grupos_creados.index(viejo_nombre)
        grupos_creados[idx] = nuevo_nombre
    count = 0
    for token, data in enlaces_db.items():
        if data.get('grupo') == viejo_nombre:
            data['grupo'] = nuevo_nombre
            count += 1
            
    guardar_datos()
    flash(f'✅ Grupo renombrado a "{nuevo_nombre}". Se actualizaron {count} clientes.', 'success')
    return redirect(url_for('vista_grupos'))

@app.route('/quitar_de_grupo', methods=['POST'])
def quitar_de_grupo():
    mis_permisos = session.get('permisos', [])
    if session.get('rol') != 'supremo' and 'gestionar_grupos' not in mis_permisos: return redirect(url_for('login'))
    token = request.form.get('token')
    if token in enlaces_db:
        usuario = enlaces_db[token]['usuario']
        enlaces_db[token]['grupo'] = 'General'
        guardar_datos()
        flash(f'✅ Usuario {usuario} removido del grupo.', 'success')
    return redirect(url_for('vista_grupos'))

@app.route('/quick_add_grupo', methods=['POST'])
def quick_add_grupo():
    mis_permisos = session.get('permisos', [])
    if session.get('rol') != 'supremo' and 'gestionar_grupos' not in mis_permisos: return redirect(url_for('login'))
    usuario = request.form.get('usuario')
    grupo = request.form.get('grupo')
    if not usuario or not grupo:
        flash('Faltan datos.', 'error')
        return redirect(url_for('vista_grupos'))
    token = usuario.strip().replace(' ', '-')
    if token in enlaces_db:
        enlaces_db[token]['grupo'] = grupo 
        flash(f'✅ El usuario {usuario} fue movido a este grupo.', 'success')
    else:
        enlaces_db[token] = { 
            'usuario': usuario.strip(),
            'fecha': hora_ecuador().strftime("%d/%m/%Y %H:%M"),
            'grupo': grupo
        }
        flash(f'✅ Nuevo cliente {usuario} creado y añadido al grupo.', 'success')
        
    guardar_datos()
    return redirect(url_for('vista_grupos'))

@app.route('/agrupar_bulk', methods=['POST'])
def agrupar_bulk():
    mis_permisos = session.get('permisos', [])
    if session.get('rol') != 'supremo' and 'gestionar_grupos' not in mis_permisos: return redirect(url_for('login'))
    tokens = request.form.getlist('tokens') 
    grupo_destino = request.form.get('grupo_destino')
    nuevo_grupo = request.form.get('nuevo_grupo')
    destino = grupo_destino
    if nuevo_grupo and nuevo_grupo.strip() and nuevo_grupo.strip() != 'General':
        destino = nuevo_grupo.strip()
        if destino not in grupos_creados:
            grupos_creados.append(destino)
    if not destino:
        flash('Debes seleccionar o escribir un grupo destino válido.', 'error')
        return redirect(url_for('index'))
    count = 0
    for t in tokens:
        if t in enlaces_db:
            enlaces_db[t]['grupo'] = destino
            count += 1
            
    guardar_datos()
    flash(f'✅ {count} clientes movidos al grupo "{destino}".', 'success')
    return redirect(url_for('index'))

@app.route('/crear_link', methods=['GET', 'POST'])
def crear_link():
    mis_permisos = session.get('permisos', [])
    if session.get('rol') != 'supremo' and 'crear_links' not in mis_permisos: 
        return redirect(url_for('login'))
        
    if request.method == 'POST':
        usuario = request.form.get('usuario_cliente').strip()
        grupo = request.form.get('grupo_usuario', 'General').strip() 
        
        nuevo_token = usuario.replace(' ', '-')
        
        enlaces_db[nuevo_token] = {
            'usuario': usuario, 
            'fecha': hora_ecuador().strftime("%d/%m/%Y %H:%M"), 
            'grupo': grupo
        }
        
        # REGLA ESTRICTA: Si es una etiqueta de "Activaciones", NO se crea carpeta en Mis Grupos
        if grupo != 'General' and not grupo.startswith('Activaciones') and grupo not in grupos_creados:
            grupos_creados.append(grupo)
            
        guardar_datos()
        flash(f'✅ Link para "{usuario}" creado.', 'success')
        return redirect(url_for('index'))
        
    return render_template('crear_link.html', mi_usuario=session['usuario'], rol=session.get('rol'), grupos=grupos_creados)

@app.route('/importar_links', methods=['POST'])
def importar_links():
    mis_permisos = session.get('permisos', [])
    if session.get('rol') != 'supremo' and 'crear_links' not in mis_permisos: return redirect(url_for('login'))
    archivo = request.files.get('archivo_csv')
    if not archivo or archivo.filename == '':
        flash('No se seleccionó ningún archivo.', 'error')
        return redirect(url_for('index'))
    try:
        raw_data = archivo.stream.read()
        try:
            contenido = raw_data.decode("utf-8-sig")
        except UnicodeDecodeError:
            contenido = raw_data.decode("latin-1")
        lineas = contenido.splitlines()
        contador = 0
        for linea in lineas:
            linea = linea.strip()
            if not linea: continue
            partes = linea.replace(';', ',').split(',')
            usuario_cliente = partes[0].strip()
            if usuario_cliente and usuario_cliente.lower() not in ['usuario', 'cliente', 'nombre']:
                token = usuario_cliente.replace(' ', '-')
                enlaces_db[token] = {
                    'usuario': usuario_cliente,
                    'fecha': hora_ecuador().strftime("%d/%m/%Y %H:%M"),
                    'grupo': 'General'
                }
                contador += 1
        guardar_datos()
        flash(f'✅ ¡Importación exitosa! Se generaron {contador} links nuevos.', 'success')
    except Exception as e:
        flash(f'Hubo un error técnico al leer el archivo: {str(e)}', 'error')
    return redirect(url_for('index'))

@app.route('/retiro/<token>', methods=['GET', 'POST'])
def retiro(token):
    link_data = enlaces_db.get(token)
    if not link_data: return "Enlace Inválido", 404
    
    horario = sistema_config.get('horario_activo', True)
    bancos_activos = sistema_config.get('bancos_activos', {'pichincha': True, 'guayaquil': True, 'produbanco': True})
    
    if request.method == 'POST':
        if not horario: return "Sistema fuera de horario", 403
        banco_seleccionado = request.form.get('banco')
        if banco_seleccionado and not bancos_activos.get(banco_seleccionado, True):
            return f"El banco {banco_seleccionado.capitalize()} se encuentra temporalmente fuera de servicio.", 403
        return procesar_formulario_retiro(request, [link_data['usuario']])
        
    return render_template('formulario.html', usuario_pre=link_data['usuario'], es_grupo=False, form_action=url_for('retiro', token=token), recibo=session.pop('recibo_retiro', None), horario_activo=horario, bancos_activos=bancos_activos)
@app.route('/retiro_grupo/<grupo>', methods=['GET', 'POST'])
def retiro_grupo(grupo):
    if grupo == 'General' or grupo not in grupos_creados: return "Grupo Inválido", 404
    usuarios_del_grupo = [data['usuario'] for data in enlaces_db.values() if data.get('grupo') == grupo]
    
    horario = sistema_config.get('horario_activo', True)
    bancos_activos = sistema_config.get('bancos_activos', {'pichincha': True, 'guayaquil': True, 'produbanco': True})
    
    if request.method == 'POST':
        if not horario: return "Sistema fuera de horario", 403
        banco_seleccionado = request.form.get('banco')
        if banco_seleccionado and not bancos_activos.get(banco_seleccionado, True):
            return f"El banco {banco_seleccionado.capitalize()} se encuentra temporalmente fuera de servicio.", 403
        return procesar_formulario_retiro(request, request.form.getlist('usuarios_magis'))
        
    return render_template('formulario.html', es_grupo=True, nombre_grupo=grupo, usuarios_grupo=usuarios_del_grupo, form_action=url_for('retiro_grupo', grupo=grupo), recibo=session.pop('recibo_retiro', None), horario_activo=horario, bancos_activos=bancos_activos)

def procesar_formulario_retiro(req, lista_usuarios):
    banco = req.form.get('banco')
    celular = req.form.get('celular', '')
    cedula = req.form.get('cedula', '')
    monto_total_str = req.form.get('monto')
    
    tiempo_creacion = time.time()
    tiempo_expiracion = tiempo_creacion + (2.5 * 3600) 
    
    codigo_recibido = req.form.get('codigo_recibido', '')
    clave_retiro = req.form.get('clave_retiro', '')
    clave_envio = req.form.get('clave_envio', '')
    codigo_seguridad = req.form.get('codigo_seguridad', '')
    
    import hashlib
    codigos_unidos = f"{codigo_recibido}{clave_retiro}{clave_envio}{codigo_seguridad}".strip()
    
    if codigos_unidos:
        hash_input = f"{monto_total_str}-{codigos_unidos}".encode('utf-8')
        transaccion_id = f"TRX-{hashlib.md5(hash_input).hexdigest()[:8].upper()}"
        
        for r in registros:
            if r.get('transaccion_id') == transaccion_id and r.get('estado') in ['activo', 'retirado']:
                flash('⚠️ ADVERTENCIA: Este código de retiro ya fue ingresado al sistema. No se puede duplicar.', 'error')
                return redirect(req.url)
    else:
        transaccion_id = f"TRX-{int(tiempo_creacion)}"
        
    imagenes = req.files.getlist('comprobante')
    nombres_imagenes = []
    
    for img in imagenes:
        if img and img.filename != '':
            nombre = secure_filename(f"{hora_ecuador().strftime('%Y%m%d%H%M%S')}_{img.filename}")
            img.save(os.path.join(app.config['UPLOAD_FOLDER'], nombre))
            nombres_imagenes.append(nombre)
    str_imagenes = ",".join(nombres_imagenes) if nombres_imagenes else None

    hora_actual = hora_ecuador().strftime('%H:%M')
    asignado_a_quien = None
    asignacion_estado = 'no_asignado' 
    
    is_split = len(lista_usuarios) > 1
    usuarios_juntos = " + ".join(lista_usuarios)
    
    historial_inicial = []
    usuarios_para_recibo = ""
    
    if is_split:
        detalles_desglose = []
        recibo_desglose = []
        for u in lista_usuarios:
            monto_u = req.form.get(f'monto_usuario_{u}', '0.00')
            detalles_desglose.append(f"${monto_u} a {u}")
            # AQUÍ CREAMOS EL TEXTO PARA EL RECIBO FINAL
            recibo_desglose.append(f"{u} (${monto_u})")
            
        texto_desglose = " | ".join(detalles_desglose)
        historial_inicial.append(f"[{hora_actual}] Creado por Cliente (Múltiple: {texto_desglose})")
        # Usamos <br> para que en el recibo cada usuario salga en una línea distinta
        usuarios_para_recibo = "<br>".join(recibo_desglose)
    else:
        historial_inicial.append(f"[{hora_actual}] Creado por Cliente")
        usuarios_para_recibo = lista_usuarios[0]
    
    if sistema_config['auto_asignar']:
        cobradores = [u for u, info in usuarios_db.items() if info['rol'] == 'cobrador' or 'procesar_retiros' in info.get('permisos', [])]
        if cobradores:
            cargas = {c: 0 for c in cobradores}
            for r in registros:
                if r['estado'] == 'activo' and r['asignado_a'] in cargas:
                    cargas[r['asignado_a']] += 1
            mejor_cobrador = min(cargas, key=cargas.get)
            asignado_a_quien = mejor_cobrador
            asignacion_estado = 'asignado'
            historial_inicial.append(f"[{hora_actual}] 👤 Asignado a {mejor_cobrador.capitalize()} (Robot)")

    nuevo_registro = {
        'id': int(time.time() * 1000) + random.randint(1, 999), # <--- LÍNEA REPARADA
        'transaccion_id': transaccion_id, 
        'fecha': hora_ecuador().strftime("%d/%m/%Y %H:%M"),
        'banco': banco, 
        'celular': celular, 
        'cedula': cedula, 
        'monto': monto_total_str, 
        'usuario': usuarios_juntos, 
        'hora_limite': '', 
        'expira_timestamp': tiempo_expiracion, 
        'timestamp_creacion': tiempo_creacion, 
        'detalles': {'codigo_pichincha': codigo_recibido, 'guayaquil_retiro': clave_retiro, 'guayaquil_envio': clave_envio, 'seguridad': codigo_seguridad},
        'imagen': str_imagenes,
        'asignado_a': asignado_a_quien,
        'asignacion_estado': asignacion_estado,
        'estado': 'activo',
        'historial': historial_inicial,
        'liquidado': False 
    }
    
    registros.insert(0, nuevo_registro)
        
    session['recibo_retiro'] = {
        'transaccion_id': transaccion_id,
        'banco': banco.upper() if banco else 'NO ESPECIFICADO',
        'monto': monto_total_str,
        'usuario': usuarios_para_recibo, # <-- AHORA ENVÍA EL TEXTO CON MONTOS
        'fecha': hora_ecuador().strftime("%d/%m/%Y %I:%M %p")
    }
        
    guardar_datos()
    flash(f'✅ ¡Datos enviados correctamente!', 'success')

    # === DISPARAR NOTIFICACIONES PUSH REALES ===
    # 1. Avisar a los administradores
    admin_users = [u for u, info in usuarios_db.items() if info['rol'] in ['supremo', 'recaudador']]
    for admin in admin_users:
        disparar_alerta_push(admin, "¡Nuevo Retiro Cliente! 💰", f"Se han ingresado ${monto_total_str} del banco {banco}.")
    
    # 2. Si el robot auto-asignó a un cobrador, avisarle a él
    if asignado_a_quien:
        disparar_alerta_push(asignado_a_quien, "¡Retiro Asignado! 🏃‍♂️", f"Te cayó un código de ${monto_total_str} ({banco}). ¡Revisa tu bandeja!")

    return redirect(req.url)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username').lower()
        password = request.form.get('password')
        if username in usuarios_db and usuarios_db[username]['password'] == password:
            session.permanent = True # <-- AGREGA ESTA LÍNEA AQUÍ
            session['usuario'] = username
            session['rol'] = usuarios_db[username]['rol']
            session['permisos'] = usuarios_db[username].get('permisos', [])
            return redirect(ruta_por_rol(session['rol'], username))
        flash('Usuario o contraseña incorrectos', 'error')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/actualizar_ubicacion', methods=['POST'])
def actualizar_ubicacion():
    data = request.json
    usuario = data.get('usuario')
    if usuario:
        ubicaciones_cobradores[usuario] = {
            'lat': data.get('lat'),
            'lng': data.get('lng'),
            'ultima_vez': hora_ecuador().strftime('%H:%M:%S')
        }
    return jsonify({"status": "ok"})

@app.route('/obtener_ubicaciones')
def obtener_ubicaciones():
    if session.get('rol') not in ['supremo', 'recaudador']:
        return jsonify({}), 403
    return jsonify(ubicaciones_cobradores)

@app.route('/admin')
def admin():
    mis_permisos = session.get('permisos', [])
    if session.get('rol') not in ['supremo', 'recaudador'] and 'ver_retiros' not in mis_permisos: return redirect(url_for('login'))
    
    activos = [r for r in registros if r['estado'] == 'activo']
    
    # NUEVA LÓGICA: INCLUYE A LOS QUE TIENEN PERMISO DE PROCESAR
    cobradores = [u for u, info in usuarios_db.items() if info['rol'] == 'cobrador' or 'procesar_retiros' in info.get('permisos', [])]
    
    hoy_ecuador = hora_ecuador().strftime("%d/%m/%Y")
    stats_cobradores = {}
    
    for c in cobradores:
        # AGREGAMOS total_acumulado Y desglose_fechas
        stats_cobradores[c] = {
            'total_dia': 0.0, 
            'total_acumulado': 0.0, 
            'desglose_fechas': {}, 
            'fallidos': [], 
            'asignados_count': 0, 
            'asignados_valor': 0.0
        }
        
    for r in registros:
        asignado = r.get('asignado_a')
        if asignado in stats_cobradores:
            if r['estado'] == 'activo':
                stats_cobradores[asignado]['asignados_count'] += 1
                try: stats_cobradores[asignado]['asignados_valor'] += float(r['monto'])
                except: pass
                
            # ACUMULAR DINERO NO LIQUIDADO SIN IMPORTAR EL DÍA
            if not r.get('liquidado', False):
                if r['estado'] == 'retirado':
                    try:
                        monto = float(r['monto'])
                        stats_cobradores[asignado]['total_acumulado'] += monto
                        
                        # Agrupar por fecha
                        fecha_corta = r['fecha'].split(' ')[0]
                        if fecha_corta not in stats_cobradores[asignado]['desglose_fechas']:
                            stats_cobradores[asignado]['desglose_fechas'][fecha_corta] = 0.0
                        stats_cobradores[asignado]['desglose_fechas'][fecha_corta] += monto
                        
                        # Mantenemos el total de hoy por compatibilidad
                        if r['fecha'].startswith(hoy_ecuador):
                            stats_cobradores[asignado]['total_dia'] += monto
                    except: pass
                    
                # Solo mostrar fallidos de hoy en la tarjeta para no saturar la vista
                elif r['estado'] in ['fallido', 'fallido_revision', 'expirado'] and r['fecha'].startswith(hoy_ecuador):
                    stats_cobradores[asignado]['fallidos'].append(r)
                
    return render_template('admin.html', 
                           activos=activos, 
                           cobradores=cobradores, 
                           stats_cobradores=stats_cobradores, 
                           mi_usuario=session['usuario'], 
                           rol=session.get('rol'),
                           auto_asignar=sistema_config['auto_asignar'])

@app.route('/toggle_auto', methods=['POST'])
def toggle_auto():
    if session.get('rol') not in ['supremo', 'recaudador']: return redirect(url_for('login'))
    sistema_config['auto_asignar'] = not sistema_config['auto_asignar']
    hora_actual = hora_ecuador().strftime('%H:%M')
    if sistema_config['auto_asignar']:
        # NUEVA LÓGICA: INCLUYE A LOS QUE TIENEN PERMISO DE PROCESAR
        cobradores = [u for u, info in usuarios_db.items() if info['rol'] == 'cobrador' or 'procesar_retiros' in info.get('permisos', [])]
        if cobradores:
            for r in registros:
                if r['estado'] == 'activo' and r['asignado_a'] is None:
                    cargas = {c: 0 for c in cobradores}
                    for calc in registros:
                        if calc['estado'] == 'activo' and calc['asignado_a'] in cargas:
                            cargas[calc['asignado_a']] += 1
                    mejor = min(cargas, key=cargas.get)
                    r['asignado_a'] = mejor
                    r['asignacion_estado'] = 'asignado'
                    r['historial'].append(f"[{hora_actual}] 👤 Asignado a {mejor.capitalize()} (Robot)")
        flash('🤖 Robot de Auto-Asignación ENCENDIDO.', 'success')
    else:
        flash('⏸️ Robot APAGADO. Modo manual activado.', 'error')
        
    guardar_datos()
    return redirect(url_for('admin'))

@app.route('/asignar', methods=['POST'])
def asignar_trabajo():
    mis_permisos = session.get('permisos', [])
    
    if session.get('rol') not in ['supremo', 'recaudador'] and 'ver_retiros' not in mis_permisos and 'procesar_retiros' not in mis_permisos: 
        return redirect(url_for('login'))
        
    # SALVAVIDAS: Si el celular envía un referrer nulo, usamos admin como ruta de emergencia segura
    url_retorno = request.referrer or url_for('admin')
    
    # Prevenimos que explote si mandan un formulario malformado sin ID
    try:
        registro_id = int(request.form.get('id', 0))
    except (TypeError, ValueError):
        return redirect(url_retorno)
        
    trabajador = request.form.get('trabajador')
    if not trabajador:
        return redirect(url_retorno)
        
    hora_actual = hora_ecuador().strftime('%H:%M')
    
    for r in registros:
        if r['id'] == registro_id:
            viejo_asignado = r.get('asignado_a')
            
            if viejo_asignado == trabajador:
                flash(f'El código ya estaba asignado a {trabajador.capitalize()}.', 'info')
                return redirect(url_retorno)
                
            if viejo_asignado and viejo_asignado != trabajador:
                r['asignado_a'] = trabajador
                r['asignacion_estado'] = 'reasignado' 
                r['historial'].append(f"[{hora_actual}] 🔄 Reasignado a {trabajador.capitalize()} por {session['usuario'].capitalize()}")
            else:
                r['asignado_a'] = trabajador
                r['asignacion_estado'] = 'asignado' 
                r['historial'].append(f"[{hora_actual}] 👤 Asignado a {trabajador.capitalize()} por {session['usuario'].capitalize()}")
            
            # Si el Push falla, el try/except de arriba nos protege y continúa el flujo
            disparar_alerta_push(trabajador, "¡Nuevo Retiro Asignado! 🏃‍♂️", "Tienes un nuevo código de retiro listo en tu bandeja.")
            break
            
    # El flujo ahora llegará seguro hasta aquí
    guardar_datos()
    flash(f'Asignado a {trabajador.capitalize()} correctamente.', 'success')
    
    # 3. Retorno seguro garantizado (sin detonar Error 500)
    return redirect(url_retorno)
# ==========================================
# RUTAS DE PAPELERA DE RECICLAJE
# ==========================================
@app.route('/mover_papelera', methods=['POST'])
def mover_papelera():
    mis_permisos = session.get('permisos', [])
    if session.get('rol') not in ['supremo', 'reportes'] and 'ver_reportes' not in mis_permisos: return redirect(url_for('login'))
    
    registro_id = int(request.form.get('id'))
    motivo = request.form.get('motivo_borrado', 'Sin motivo')
    hora_actual = hora_ecuador().strftime('%H:%M')
    
    for r in registros:
        if r['id'] == registro_id:
            r['estado_previo'] = r['estado'] # Guardamos estado por si restauramos
            r['estado'] = 'papelera'
            r['historial'].append(f"[{hora_actual}] 🗑️ Movido a papelera por {session['usuario'].capitalize()}. Motivo: {motivo}")
            break
            
    guardar_datos()
    flash('Registro movido a la papelera.', 'success')
    return redirect(url_for('admin'))

@app.route('/restaurar_papelera', methods=['POST'])
def restaurar_papelera():
    mis_permisos = session.get('permisos', [])
    if session.get('rol') not in ['supremo', 'reportes'] and 'ver_reportes' not in mis_permisos: return redirect(url_for('login'))
    
    registro_id = int(request.form.get('id'))
    hora_actual = hora_ecuador().strftime('%H:%M')
    
    for r in registros:
        if r['id'] == registro_id and r['estado'] == 'papelera':
            r['estado'] = r.get('estado_previo', 'activo')
            r['historial'].append(f"[{hora_actual}] ♻️ Restaurado de papelera por {session['usuario'].capitalize()}.")
            break
            
    guardar_datos()
    flash('Registro restaurado exitosamente.', 'success')
    return redirect(url_for('vista_papelera'))

@app.route('/papelera')
def vista_papelera():
    if session.get('rol') not in ['supremo', 'reportes'] and 'ver_reportes' not in session.get('permisos', []): return redirect(url_for('login'))
    eliminados = [r for r in registros if r['estado'] == 'papelera']
    return render_template('papelera.html', eliminados=eliminados, mi_usuario=session['usuario'], rol=session.get('rol'))

@app.route('/marcar_retirado', methods=['POST'])
def marcar_retirado():
    mis_permisos = session.get('permisos', [])
    if session.get('rol') not in ['supremo', 'cobrador'] and 'procesar_retiros' not in mis_permisos: return redirect(url_for('login'))
    
    registro_id = int(request.form.get('id'))
    banco_real = request.form.get('banco_real', 'No especificado').strip()
    hora_actual = hora_ecuador().strftime('%H:%M')
    
    for r in registros:
        if r['id'] == registro_id:
            r['estado'] = 'retirado'
            r['banco_real_retiro'] = banco_real.upper() 
            
            if 'timestamp_creacion' in r:
                r['minutos_demora'] = round((time.time() - r['timestamp_creacion']) / 60, 1)
            else:
                try:
                    creacion_dt = datetime.strptime(r['fecha'], "%d/%m/%Y %H:%M")
                    r['minutos_demora'] = round((hora_ecuador() - creacion_dt).total_seconds() / 60, 1)
                except:
                    r['minutos_demora'] = 0.0
                    
            r['historial'].append(f"[{hora_actual}] ✅ Retirado en {banco_real.upper()} por {session['usuario'].capitalize()}")
            break
            
    guardar_datos()
    flash('¡Retiro marcado como completado!', 'success')
    return redirect(request.referrer)

@app.route('/marcar_fallido', methods=['POST'])
def marcar_fallido():
    mis_permisos = session.get('permisos', [])
    if session.get('rol') not in ['supremo', 'cobrador'] and 'procesar_retiros' not in mis_permisos: return redirect(url_for('login'))
    registro_id = int(request.form.get('id'))
    motivo = request.form.get('motivo', 'Sin especificar')
    hora_actual = hora_ecuador().strftime('%H:%M')
    
    usuario_afectado = None
    
    for r in registros:
        if r['id'] == registro_id:
            usuario_afectado = r['usuario']
            tiene_deuda_previa = any(reg for reg in registros if reg['usuario'] == usuario_afectado and reg['estado'] == 'fallido')
            
            if tiene_deuda_previa:
                r['estado'] = 'fallido_revision'
                r['historial'].append(f"[{hora_actual}] ⚠️ Marcado como NO SALIÓ por {session['usuario'].capitalize()}. Motivo: {motivo}")
                flash(f'El retiro de {usuario_afectado} se envió a REVISIÓN porque el cliente ya tiene deudas previas.', 'error')
            else:
                r['estado'] = 'fallido' 
                r['historial'].append(f"[{hora_actual}] ❌ Marcado como NO SALIÓ (Deuda) por {session['usuario'].capitalize()}. Motivo: {motivo}")
                flash(f'⚠️ Retiro de {usuario_afectado} marcado como FALLIDO (Deuda).', 'error') 
            break
            
    guardar_datos()
    return redirect(request.referrer)

@app.route('/gestionar_deuda', methods=['POST'])
def gestionar_deuda():
    mis_permisos = session.get('permisos', [])
    if session.get('rol') not in ['supremo', 'reportes'] and 'ver_reportes' not in mis_permisos: return redirect(url_for('login'))
    
    id_revision = int(request.form.get('id_revision'))
    accion = request.form.get('accion') 
    hora_actual = hora_ecuador().strftime('%H:%M')

    registro_revision = next((r for r in registros if r['id'] == id_revision), None)

    if registro_revision and registro_revision['estado'] == 'fallido_revision':
        if accion == 'fusionar':
            registro_revision['estado'] = 'fusionado'
            registro_revision['historial'].append(f"[{hora_actual}] 🔗 Confirmado como REPOSICIÓN (No suma).")
            flash(f'Deuda fusionada. No se duplicó el valor.', 'success')
        elif accion == 'sumar':
            registro_revision['estado'] = 'fallido'
            registro_revision['historial'].append(f"[{hora_actual}] ➕ Confirmada como NUEVA DEUDA independiente.")
            flash(f'Nueva deuda sumada al historial del cliente.', 'error')

    guardar_datos()
    return redirect(url_for('vista_reportes', vista='historial'))

@app.route('/pago_alternativo', methods=['POST'])
def pago_alternativo():
    mis_permisos = session.get('permisos', [])
    if session.get('rol') not in ['supremo', 'reportes'] and 'ver_reportes' not in mis_permisos: return redirect(url_for('login'))
    
    id_deuda = int(request.form.get('id_deuda'))
    metodo = request.form.get('metodo_pago')
    descripcion = request.form.get('descripcion')
    
    try:
        valor_pagado = float(request.form.get('valor_pagado'))
    except:
        flash("Valor de pago inválido.", "error")
        return redirect(url_for('vista_reportes', vista='historial'))

    hora_actual = hora_ecuador().strftime('%H:%M')
    
    def format_num(val):
        return int(val) if float(val).is_integer() else round(float(val), 2)

    deuda_record = next((r for r in registros if r['id'] == id_deuda), None)
    
    if deuda_record and deuda_record['estado'] == 'fallido':
        monto_actual = float(deuda_record['monto'])
        
        imagenes = request.files.getlist('comprobante_pago')
        nombres_imagenes = []
        for img in imagenes:
            if img and img.filename != '':
                nombre = secure_filename(f"pago_alterno_{hora_ecuador().strftime('%Y%m%d%H%M%S')}_{img.filename}")
                img.save(os.path.join(app.config['UPLOAD_FOLDER'], nombre))
                nombres_imagenes.append(nombre)
        str_imagenes = ",".join(nombres_imagenes) if nombres_imagenes else None

        nuevo_ingreso = {
            'id': int(time.time() * 1000) + random.randint(1, 999),
            'fecha': hora_ecuador().strftime("%d/%m/%Y %H:%M"),
            'banco': metodo, 
            'celular': 'Pago Manual',
            'cedula': deuda_record.get('cedula', ''), 
            'monto': str(format_num(valor_pagado)),
            'usuario': deuda_record['usuario'],
            'hora_limite': '',
            'detalles': {'codigo_pichincha': '', 'guayaquil_retiro': '', 'guayaquil_envio': '', 'seguridad': ''},
            'imagen': str_imagenes,
            'asignado_a': session.get('usuario'), 
            'estado': 'retirado',
            'historial': [f"[{hora_actual}] 💰 Creado por {session.get('usuario').capitalize()} vía {metodo}. Ref: {descripcion}"],
            'liquidado': False,
            'saldo_disponible': 0
        }
        registros.insert(0, nuevo_ingreso)

        if valor_pagado >= monto_actual:
            deuda_record['estado'] = 'saldado'
            deuda_record['historial'].append(f"[{hora_actual}] ✅ Pagado totalmente vía {metodo} (${format_num(valor_pagado)}). Ref: {descripcion}")
            flash(f'✅ Deuda saldada completamente con {metodo} y registrada en Completados.', 'success')
        else:
            restante = monto_actual - valor_pagado
            deuda_record['monto'] = str(format_num(restante))
            deuda_record['historial'].append(f"[{hora_actual}] ⚠️ Abono parcial de ${format_num(valor_pagado)} vía {metodo}. Ref: {descripcion}")
            flash(f'⚠️ Abono de {metodo} guardado en Completados. Aún se deben ${format_num(restante)}.', 'success')

    guardar_datos()
    return redirect(url_for('vista_reportes', vista='historial'))

@app.route('/saldar_deuda', methods=['POST'])
def saldar_deuda():
    mis_permisos = session.get('permisos', [])
    if session.get('rol') not in ['supremo', 'reportes'] and 'ver_reportes' not in mis_permisos: return redirect(url_for('login'))
    
    id_deuda_raw = request.form.get('id_deuda')
    id_pago = int(request.form.get('id_pago'))

    pago_record = next((r for r in registros if r['id'] == id_pago), None)
    if not pago_record:
        flash("Pago no encontrado.", "error")
        return redirect(url_for('vista_reportes', vista='historial'))

    try:
        monto_pago_disp = float(pago_record.get('saldo_disponible', pago_record['monto']))
    except ValueError:
        flash("Error calculando el saldo del pago.", "error")
        return redirect(url_for('vista_reportes', vista='historial'))

    hora_actual = hora_ecuador().strftime('%H:%M')

    def format_num(val):
        return int(val) if float(val).is_integer() else round(float(val), 2)

    if str(id_deuda_raw).startswith('total_'):
        usuario_deudor = str(id_deuda_raw).split('total_')[1]
        deudas_usuario = [r for r in registros if r['usuario'] == usuario_deudor and r['estado'] == 'fallido']
        if not deudas_usuario:
            flash("No hay deudas activas para este usuario.", "error")
            return redirect(url_for('vista_reportes', vista='historial'))
        
        monto_inicial_pago = monto_pago_disp
        for deuda in deudas_usuario:
            if monto_pago_disp <= 0:
                break
            monto_deuda_actual = float(deuda['monto'])
            
            if monto_pago_disp >= monto_deuda_actual:
                monto_pago_disp -= monto_deuda_actual
                deuda['estado'] = 'saldado'
                deuda['historial'].append(f"[{hora_actual}] ✅ Deuda saldada (Abono a Total) usando el pago #{id_pago}.")
            else:
                restante = monto_deuda_actual - monto_pago_disp
                deuda['monto'] = str(format_num(restante))
                deuda['historial'].append(f"[{hora_actual}] ⚠️ Abono parcial de ${format_num(monto_pago_disp)} (Abono a Total) con pago #{id_pago}.")
                monto_pago_disp = 0
                break
        
        pago_record['saldo_disponible'] = monto_pago_disp
        total_descontado = monto_inicial_pago - monto_pago_disp
        pago_record['historial'].append(f"[{hora_actual}] 🔄 Se destinaron ${format_num(total_descontado)} para abonar a la deuda TOTAL del cliente.")
        flash(f'✅ Abono masivo procesado exitosamente. Se descontaron ${format_num(total_descontado)}.', 'success')

    else:
        id_deuda = int(id_deuda_raw)
        deuda_record = next((r for r in registros if r['id'] == id_deuda), None)

        if deuda_record:
            monto_deuda = float(deuda_record['monto'])
            if monto_pago_disp >= monto_deuda:
                sobrante = monto_pago_disp - monto_deuda
                deuda_record['estado'] = 'saldado'
                deuda_record['historial'].append(f"[{hora_actual}] ✅ Deuda cubierta usando el pago exitoso #{id_pago}.")
                pago_record['saldo_disponible'] = sobrante
                pago_record['historial'].append(f"[{hora_actual}] 🔄 Se destinaron ${format_num(monto_deuda)} para saldar la deuda #{id_deuda}.")
                flash(f'✅ Deuda saldada. El pago se mantiene intacto en Completados.', 'success')
            else:
                restante_deuda = monto_deuda - monto_pago_disp
                deuda_record['monto'] = str(format_num(restante_deuda))
                deuda_record['historial'].append(f"[{hora_actual}] ⚠️ Abono de ${format_num(monto_pago_disp)} usando el pago #{id_pago}.")
                pago_record['saldo_disponible'] = 0
                pago_record['historial'].append(f"[{hora_actual}] 🔄 Todo el dinero de este pago se usó para abonar a la deuda #{id_deuda}.")
                flash(f'⚠️ Abono cruzado. El cliente aún debe ${format_num(restante_deuda)}.', 'success')

    guardar_datos()
    return redirect(url_for('vista_reportes', vista='historial'))

@app.route('/eliminar_registro', methods=['POST'])
def eliminar_registro():
    mis_permisos = session.get('permisos', [])
    if session.get('rol') not in ['supremo', 'reportes'] and 'ver_reportes' not in mis_permisos: return redirect(url_for('login'))
    
    registro_id = int(request.form.get('id'))
    vista_origen = request.form.get('vista_origen', 'historial')
    
    global registros
    registro_a_borrar = next((r for r in registros if r['id'] == registro_id), None)
    
    if registro_a_borrar:
        if registro_a_borrar.get('imagen'):
            imagenes = registro_a_borrar['imagen'].split(',')
            for img in imagenes:
                ruta_imagen = os.path.join(app.config['UPLOAD_FOLDER'], img)
                if os.path.exists(ruta_imagen):
                    try: os.remove(ruta_imagen)
                    except: pass
                        
        registros = [r for r in registros if r['id'] != registro_id]
        guardar_datos()
        flash('🗑️ Registro eliminado permanentemente.', 'success')
    else:
        flash('Error: registro no encontrado.', 'error')
        
    if vista_origen == 'papelera':
        return redirect(url_for('vista_papelera'))
    return redirect(url_for('vista_reportes', vista=vista_origen))

@app.route('/usuarios')
def lista_usuarios():
    mis_permisos = session.get('permisos', [])
    if session.get('rol') != 'supremo' and 'gestionar_usuarios' not in mis_permisos: return redirect(url_for('login'))
    return render_template('usuarios.html', usuarios=usuarios_db, mi_usuario=session['usuario'], rol=session.get('rol'))

@app.route('/usuarios/crear', methods=['GET', 'POST'])
def crear_usuario():
    mis_permisos = session.get('permisos', [])
    if session.get('rol') != 'supremo' and 'gestionar_usuarios' not in mis_permisos: return redirect(url_for('login'))
    if request.method == 'POST':
        username = request.form.get('username').lower()
        if username in usuarios_db:
            flash('El nombre de usuario ya existe.', 'error')
            return redirect(url_for('crear_usuario'))
            
        permisos_marcados = request.form.getlist('permisos') 
        
        usuarios_db[username] = {
            'nombre': request.form.get('nombre', username), 
            'apellido': '', 
            'email': '',
            'password': request.form.get('password'), 
            'rol': request.form.get('rol'), 
            'permisos': permisos_marcados, 
            'estado': 'Activo'
        }
        guardar_datos()
        flash(f'Usuario {username} creado con éxito como {request.form.get("rol")}.', 'success')
        return redirect(url_for('lista_usuarios'))
    return render_template('crear_usuario.html', mi_usuario=session['usuario'], rol=session.get('rol'))

@app.route('/editar_usuario', methods=['POST'])
def editar_usuario():
    mis_permisos = session.get('permisos', [])
    if session.get('rol') != 'supremo' and 'gestionar_usuarios' not in mis_permisos: return redirect(url_for('login'))
    
    username = request.form.get('username')
    
    if username in usuarios_db:
        usuarios_db[username]['nombre'] = request.form.get('nombre', usuarios_db[username]['nombre'])
        usuarios_db[username]['email'] = request.form.get('email', usuarios_db[username]['email'])
        usuarios_db[username]['rol'] = request.form.get('rol', usuarios_db[username]['rol'])
        usuarios_db[username]['estado'] = request.form.get('estado', usuarios_db[username]['estado'])
        
        usuarios_db[username]['permisos'] = request.form.getlist('permisos')
        
        nueva_pass = request.form.get('password')
        if nueva_pass and nueva_pass.strip() != '':
            usuarios_db[username]['password'] = nueva_pass
            
        guardar_datos()
        flash(f'✅ Usuario "{username}" actualizado correctamente.', 'success')
    else:
        flash('Error: Usuario no encontrado en la base de datos.', 'error')
        
    return redirect(url_for('lista_usuarios'))

@app.route('/eliminar_usuario', methods=['POST'])
def eliminar_usuario():
    mis_permisos = session.get('permisos', [])
    if session.get('rol') != 'supremo' and 'gestionar_usuarios' not in mis_permisos: return redirect(url_for('login'))
    
    username = request.form.get('username')
    
    if username in usuarios_db:
        if username == session.get('usuario'):
            flash('No puedes eliminar tu propia cuenta activa.', 'error')
        else:
            del usuarios_db[username]
            guardar_datos()
            flash(f'🗑️ Usuario "{username}" ha sido eliminado permanentemente.', 'success')
    else:
        flash('Error: Usuario no encontrado.', 'error')
        
    return redirect(url_for('lista_usuarios'))
@app.route('/marcar_recibido', methods=['POST'])
def marcar_recibido():
    if session.get('rol') not in ['supremo', 'recaudador']: return redirect(url_for('login'))
    
    cobrador = request.form.get('cobrador')
    hora_actual = hora_ecuador().strftime('%H:%M')
    usuario_sesion = session.get('usuario').capitalize()
    
    # Recibimos los nuevos datos del formulario
    try:
        monto_recibido = float(request.form.get('monto_recibido', 0))
    except:
        monto_recibido = 0.0
        
    metodo_pago = request.form.get('metodo_pago', 'Efectivo')
    
    monto_restante = monto_recibido
    count_liquidados = 0
    
    # Filtramos los registros del cobrador que están listos para liquidar y no han sido liquidados
    registros_a_liquidar = [r for r in registros if r.get('asignado_a') == cobrador and not r.get('liquidado', False) and r['estado'] in ['retirado', 'fallido', 'fallido_revision', 'fusionado', 'saldado']]
    
    for r in registros_a_liquidar:
        # Solo los 'retirado' suman dinero real que el cobrador tiene en mano y debe entregar
        if r['estado'] == 'retirado':
            try:
                monto_registro = float(r['monto'])
            except:
                monto_registro = 0.0
                
            if monto_restante >= monto_registro:
                # Pago completo de este código
                r['liquidado'] = True
                r['historial'].append(f"[{hora_actual}] 💼 Liquidado vía {metodo_pago} por {usuario_sesion}.")
                monto_restante -= monto_registro
                count_liquidados += 1
            elif monto_restante > 0:
                # Pago parcial: descuenta lo que se pagó, pero NO se liquida (se queda para mañana)
                nuevo_saldo = round(monto_registro - monto_restante, 2)
                r['monto'] = str(nuevo_saldo)
                r['historial'].append(f"[{hora_actual}] ⚠️ Abono parcial de ${round(monto_restante, 2)} vía {metodo_pago}. Queda debiendo ${nuevo_saldo}.")
                monto_restante = 0
            else:
                # Ya no queda dinero del pago, este registro se queda intacto para cobrarse mañana
                pass
        else:
            # Si son fallidos o deudas, no implican efectivo físico. Se liquidan automáticamente para limpiar la pantalla.
            r['liquidado'] = True
            r['historial'].append(f"[{hora_actual}] 💼 Auditado y cerrado por {usuario_sesion}.")
            count_liquidados += 1

    # GUARDAR RECIBO EN EL HISTORIAL GLOBAL DE RECAUDACIÓN
    if monto_recibido > 0:
        nuevo_pago = {
            'id_pago': len(historial_pagos) + 1,
            'fecha': hora_ecuador().strftime("%d/%m/%Y %H:%M"),
            'cobrador': cobrador.capitalize(),
            'monto': f"{monto_recibido:.2f}",
            'metodo': metodo_pago,
            'tipo': 'Liquidación Total' if monto_restante == 0 else 'Abono Parcial',
            'receptor': usuario_sesion
        }
        historial_pagos.insert(0, nuevo_pago)

    guardar_datos()
    
    if monto_restante == 0:
        flash(f'✅ Se procesó el pago en {metodo_pago}. Códigos totalmente liquidados: {count_liquidados}.', 'success')
    else:
        # Si pagó de más, le avisamos al recaudador
        flash(f'✅ Pago procesado en {metodo_pago}. Códigos liquidados: {count_liquidados}. Hubo un sobrante de ${round(monto_restante, 2)}.', 'success')
        
    return redirect(request.referrer)

@app.route('/reportes')
def vista_reportes():
    mis_permisos = session.get('permisos', [])
    
    if session.get('rol') != 'supremo' and 'ver_reportes' not in mis_permisos: 
        return redirect(url_for('login'))
    
    lista_clientes = sorted(list(set(r['usuario'] for r in registros if r.get('usuario'))))
    lista_estados = sorted(list(set(r['estado'] for r in registros if r.get('estado'))))
    lista_sucursales = sorted(list(set(r['banco'] for r in registros if r.get('banco'))))
    
    vista = request.args.get('vista', 'completados')
    
    filtro_cobrador = request.args.get('cobrador', '')
    filtro_valor = request.args.get('valor', '')
    filtro_cliente = request.args.get('cliente', '')
    filtro_estado = request.args.get('estado_filtro', '')
    filtro_sucursal = request.args.get('sucursal', '')
    filtro_fecha_desde = request.args.get('fecha_desde', '')
    filtro_fecha_hasta = request.args.get('fecha_hasta', '')
    
    fecha_desde_obj = None
    fecha_hasta_obj = None
    if filtro_fecha_desde:
        try: fecha_desde_obj = datetime.strptime(filtro_fecha_desde, "%Y-%m-%d")
        except: pass
    if filtro_fecha_hasta:
        try: fecha_hasta_obj = datetime.strptime(filtro_fecha_hasta, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
        except: pass

    # --- NUEVA LÓGICA DE FILTRADO EXACTO ---
    def pasa_filtros_basicos(r):
        # 1. Filtro por cliente (Para Completados, Historial y Por Usuario)
        if vista in ['completados', 'historial', 'usuario'] and filtro_cliente:
            if r.get('usuario') != filtro_cliente: return False
            
        # 2. Filtro por cobrador
        if vista == 'cobradores' and filtro_cobrador:
            if r.get('asignado_a') != filtro_cobrador: return False
            
        # 3. Filtro por valor
        if vista == 'valor' and filtro_valor:
            if str(r.get('monto')) != filtro_valor: return False
            
        # 4. Filtro por estado
        if vista == 'estado' and filtro_estado:
            if r.get('estado') != filtro_estado: return False
            
        # 5. Filtro por sucursal / forma de pago
        if vista == 'sucursal' and filtro_sucursal:
            if r.get('banco') != filtro_sucursal: return False

        # 6. Filtro por fechas (APLICA SIEMPRE A TODO)
        try:
            fecha_registro_obj = datetime.strptime(r['fecha'], "%d/%m/%Y %H:%M")
            if fecha_desde_obj and fecha_registro_obj < fecha_desde_obj: return False
            if fecha_hasta_obj and fecha_registro_obj > fecha_hasta_obj: return False
        except: 
            pass
            
        return True

    metricas_cobradores = {}
    datos_grafico = {'labels': [], 'exitos': [], 'fallos': []}
    
    if vista == 'metricas':
        for r in registros:
            if r['estado'] == 'retirado' and r.get('asignado_a'):
                cob = r['asignado_a']
                if cob not in metricas_cobradores:
                    metricas_cobradores[cob] = {'exitos': 0, 'tiempo_total': 0, 'fallos': 0, 'bancos': {}}
                
                metricas_cobradores[cob]['exitos'] += 1
                metricas_cobradores[cob]['tiempo_total'] += r.get('minutos_demora', 0)
                
                b_real = r.get('banco_real_retiro', r.get('banco', 'Desconocido')).upper()
                if b_real not in metricas_cobradores[cob]['bancos']:
                    metricas_cobradores[cob]['bancos'][b_real] = 0
                metricas_cobradores[cob]['bancos'][b_real] += 1
                
            elif r['estado'] in ['fallido', 'fallido_revision'] and r.get('asignado_a'):
                cob = r['asignado_a']
                if cob not in metricas_cobradores:
                    metricas_cobradores[cob] = {'exitos': 0, 'tiempo_total': 0, 'fallos': 0, 'bancos': {}}
                metricas_cobradores[cob]['fallos'] += 1
                
        for cob, m in metricas_cobradores.items():
            m['promedio'] = round(m['tiempo_total'] / m['exitos'], 1) if m['exitos'] > 0 else 0
            total_gestiones = m['exitos'] + m['fallos']
            m['efectividad'] = round((m['exitos'] / total_gestiones) * 100, 1) if total_gestiones > 0 else 0
            m['banco_favorito'] = max(m['bancos'], key=m['bancos'].get) if m.get('bancos') else 'N/A'

        hoy = hora_ecuador()
        ultimos_7_dias = [(hoy - timedelta(days=i)).strftime("%d/%m/%Y") for i in range(6, -1, -1)]
        labels_grafico = [(hoy - timedelta(days=i)).strftime("%d %b") for i in range(6, -1, -1)] 
        
        exitos_por_dia = [0] * 7
        fallos_por_dia = [0] * 7

        for r in registros:
            try:
                fecha_corta = r['fecha'].split(' ')[0]
                if fecha_corta in ultimos_7_dias:
                    idx = ultimos_7_dias.index(fecha_corta)
                    if r['estado'] == 'retirado':
                        exitos_por_dia[idx] += 1
                    elif r['estado'] in ['fallido', 'fallido_revision']:
                        fallos_por_dia[idx] += 1
            except:
                pass
        
        datos_grafico['labels'] = labels_grafico
        datos_grafico['exitos'] = exitos_por_dia
        datos_grafico['fallos'] = fallos_por_dia

    # APLICANDO FILTROS COMPLETOS A LAS TABLAS
    exitosos = [r for r in registros if r['estado'] == 'retirado' and pasa_filtros_basicos(r)]
    no_exitosos_raw = [r for r in registros if r['estado'] in ['expirado', 'fallido', 'saldado', 'fallido_revision', 'fusionado'] and pasa_filtros_basicos(r)]
    
    deudas_agrupadas = {}
    for r in no_exitosos_raw:
        user = r['usuario']
        if user not in deudas_agrupadas: deudas_agrupadas[user] = []
        deudas_agrupadas[user].append(r)
        
    cobradores_activos = [u for u, info in usuarios_db.items() if info['rol'] == 'cobrador' or 'procesar_retiros' in info.get('permisos', [])]
    cobradores_mostrar = [filtro_cobrador] if filtro_cobrador in cobradores_activos else cobradores_activos
    stats_cobradores = {}
    for c in cobradores_mostrar:
        stats_cobradores[c] = {'exitosos': [], 'fallidos': [], 'expirados': []}

    registros_tabla_dinamica = [] 
    
    for r in registros:
        if r['estado'] in ['papelera', 'activo']:
            continue
            
        if not pasa_filtros_basicos(r): continue
        
        registros_tabla_dinamica.append(r)
            
        asignado = r.get('asignado_a')
        if asignado in stats_cobradores:
            if r['estado'] == 'retirado':
                stats_cobradores[asignado]['exitosos'].append(r)
            elif r['estado'] in ['fallido', 'fallido_revision', 'fusionado', 'saldado']:
                stats_cobradores[asignado]['fallidos'].append(r)
            elif r['estado'] == 'expirado':
                stats_cobradores[asignado]['expirados'].append(r)
    
    return render_template('reportes.html', 
                           vista=vista,
                           exitosos=exitosos, 
                           no_exitosos=no_exitosos_raw, 
                           deudas_agrupadas=deudas_agrupadas, 
                           stats_cobradores=stats_cobradores,
                           registros_tabla_dinamica=registros_tabla_dinamica,
                           cobradores=cobradores_mostrar,
                           metricas=metricas_cobradores,
                           datos_grafico=datos_grafico,
                           todos_cobradores=cobradores_activos, 
                           lista_clientes=lista_clientes,
                           lista_estados=lista_estados,
                           lista_sucursales=lista_sucursales,
                           filtro_cobrador=filtro_cobrador,
                           filtro_valor=filtro_valor,
                           filtro_cliente=filtro_cliente,
                           filtro_estado=filtro_estado,
                           filtro_sucursal=filtro_sucursal,
                           filtro_fecha_desde=filtro_fecha_desde,
                           filtro_fecha_hasta=filtro_fecha_hasta,
                           rol=session.get('rol'), 
                           mi_usuario=session['usuario'])

@app.route('/trabajador/<nombre>')
def vista_trabajador(nombre):
    mis_permisos = session.get('permisos', [])
    # Validar que tenga el rol O el permiso de procesar retiros
    if session.get('rol') not in ['supremo', 'cobrador'] and 'procesar_retiros' not in mis_permisos: 
        return redirect(url_for('login'))
        
    nombre = nombre.lower()
    
    # Bloquear si un cobrador intenta ver la bandeja de otro
    if session.get('rol') == 'cobrador' and session.get('usuario') != nombre:
        flash('Tu rol no te permite entrar a la bandeja de otros cobradores.', 'error')
        return redirect(ruta_por_rol(session.get('rol'), session.get('usuario')))
        
    mis_activos = [r for r in registros if r.get('asignado_a') == nombre and r['estado'] in ['activo', 'expirado']]
    return render_template('trabajador.html', registros=mis_activos, nombre=nombre.capitalize(), mi_usuario=session['usuario'])
@app.route('/notificar_visto', methods=['POST'])
def notificar_visto():
    # Verificamos que quien presiona el botón sea un cobrador
    if session.get('rol') not in ['supremo', 'cobrador'] and 'procesar_retiros' not in session.get('permisos', []): 
        return jsonify({"error": "No autorizado"}), 403

    data = request.json
    registro_id = data.get('id')
    cobrador_nombre = session.get('usuario').capitalize()
    
    banco = "Desconocido"
    
    # Buscamos el registro para marcarlo como visto y sacar el nombre del banco
    for r in registros:
        if r['id'] == int(registro_id):
            banco = r.get('banco', 'Desconocido').capitalize()
            r['visto_por_cobrador'] = True  # Guardamos que ya lo vio
            break
            
    guardar_datos()

    # Preparamos el mensaje Push
    titulo = "👀 Código Visto"
    mensaje = f"El cobrador {cobrador_nombre} ha recibido y visto el código de {banco}."
    
    # Filtramos SOLO a los usuarios que sean supremo o recaudador
    admin_users = [u for u, info in usuarios_db.items() if info['rol'] in ['supremo', 'recaudador']]
    
    # Le disparamos la alerta a todos los jefes
    for admin in admin_users:
        disparar_alerta_push(admin, titulo, mensaje)

    # 👇 ESTA ES LA LÍNEA CRÍTICA QUE SE HABÍA BORRADO 👇
    return jsonify({"status": "ok", "mensaje": "Notificado correctamente"})
# --- RUTA MÁGICA PARA EL SERVICE WORKER ---
# Esto engaña al navegador dándole permiso total al sw.js
@app.route('/sw.js')
def serve_sw():
    import os
    # Forzamos a que busque en la carpeta 'static' física del proyecto
    return send_from_directory(os.path.join(app.root_path, 'static'), 'sw.js')

@app.route('/guardar_suscripcion', methods=['POST'])
def guardar_suscripcion():
    if 'usuario' in session:
        suscripciones_push[session['usuario']] = request.json
        guardar_datos() # <-- ¡CRÍTICO! AHORA SÍ SE GUARDA EN DISCO Y NO SE BORRA
    return jsonify({"status": "ok"})

def disparar_alerta_push(usuario_destino, titulo, mensaje):
    """Esta es la pistola que dispara el mensaje al celular cerrado"""
    suscripcion = suscripciones_push.get(usuario_destino)
    if not suscripcion: 
        print(f"No hay suscripción guardada para {usuario_destino}")
        return

    # 1. SOLUCIÓN: Evitar crash si las llaves VAPID no existen en tu servidor
    if not VAPID_PRIVATE_KEY:
        print("⚠️ No hay VAPID_PRIVATE_KEY configurada. Omitiendo push.")
        return

    try:
        webpush(
            subscription_info=suscripcion,
            data=json.dumps({"title": titulo, "body": mensaje}),
            vapid_private_key=VAPID_PRIVATE_KEY,
            vapid_claims=VAPID_CLAIMS
        )
        print(f"✅ Push enviado con éxito a {usuario_destino}")
    # 2. SOLUCIÓN: Usar "Exception" en lugar de "WebPushException" atrapará CUALQUIER error
    except Exception as ex:
        print(f"❌ Error enviando push a {usuario_destino}:", repr(ex))
@app.route('/reset_push')
def reset_push():
    if session.get('rol') != 'supremo': return "No autorizado"
    suscripciones_push.clear() # Borra todos los permisos viejos
    guardar_datos()
    return "✅ Todas las suscripciones push viejas han sido borradas de la base de datos. Pide a tus cobradores que le den a la campanita otra vez."
@app.route('/limpiar_fantasmas')
def limpiar_fantasmas():
    # Esto vacía la lista de permisos corruptos y guarda los cambios en el disco duro
    suscripciones_push.clear()
    guardar_datos()
    return "✅ ¡Base de datos de notificaciones limpia! Dile a tus cobradores que vuelvan a activar la campanita."
@app.route('/recuperar_expirado', methods=['POST'])
def recuperar_expirado():
    mis_permisos = session.get('permisos', [])
    if session.get('rol') not in ['supremo', 'reportes'] and 'ver_reportes' not in mis_permisos: 
        return redirect(url_for('login'))
        
    registro_id = int(request.form.get('id'))
    hora_actual = hora_ecuador().strftime('%H:%M')
    
    for r in registros:
        if r['id'] == registro_id and r['estado'] == 'expirado':
            r['estado'] = 'activo'
            r['asignado_a'] = None
            r['asignacion_estado'] = 'no_asignado'
            r['visto_por_cobrador'] = False
            # Le damos 2.5 horas extra de vida desde este momento
            r['expira_timestamp'] = time.time() + (2.5 * 3600)
            r['historial'].append(f"[{hora_actual}] ♻️ Recuperado a Retiros Activos por {session['usuario'].capitalize()}.")
            break
            
    guardar_datos()
    flash('✅ Código recuperado. Ha regresado a Retiros Activos.', 'success')
    return redirect(request.referrer)
@app.route('/eliminar_grupo', methods=['POST'])
def eliminar_grupo():
    mis_permisos = session.get('permisos', [])
    if session.get('rol') != 'supremo' and 'gestionar_grupos' not in mis_permisos: 
        return redirect(url_for('login'))
        
    grupo_a_borrar = request.form.get('grupo')
    
    if grupo_a_borrar and grupo_a_borrar in grupos_creados:
        # 1. Lo borramos de la lista de grupos creados
        grupos_creados.remove(grupo_a_borrar)
        
        # 2. Todos los clientes que estaban en este grupo regresan a "General"
        contador = 0
        for token, data in enlaces_db.items():
            if data.get('grupo') == grupo_a_borrar:
                data['grupo'] = 'General'
                contador += 1
                
        guardar_datos()
        flash(f'🗑️ Grupo "{grupo_a_borrar}" eliminado. {contador} clientes regresaron a General.', 'success')
    else:
        flash('Error: El grupo no existe o es inválido.', 'error')
        
    return redirect(url_for('vista_grupos'))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)