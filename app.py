import os
import random
import string
import csv
import io
import json
import base64
import time
from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify, send_from_directory
from werkzeug.utils import secure_filename
from datetime import datetime, timedelta

# --- CONFIGURACIÓN DE INTELIGENCIA ARTIFICIAL ---
from openai import OpenAI
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY") 
openai_client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

app = Flask(__name__)
app.secret_key = "flujo_secreto_123"

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

def guardar_datos():
    data_a_guardar = {
        'registros': registros,
        'sistema_config': sistema_config,
        'enlaces_db': enlaces_db,
        'grupos_creados': grupos_creados,
        'usuarios_db': usuarios_db
    }
    try:
        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(data_a_guardar, f, ensure_ascii=False, indent=4)
    except Exception as e:
        print("Error crítico al guardar en disco:", e)

def cargar_datos():
    global registros, sistema_config, enlaces_db, grupos_creados, usuarios_db
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

def ruta_por_rol(rol, username):
    if rol == 'supremo': return url_for('admin')
    if rol == 'recaudador': return url_for('admin')
    if rol == 'cobrador': return url_for('vista_trabajador', nombre=username)
    if rol == 'reportes': return url_for('vista_reportes')
    return url_for('login')

@app.route('/')
def index():
    if 'usuario' not in session: return redirect(url_for('login'))
    mis_permisos = session.get('permisos', [])
    if session.get('rol') != 'supremo' and 'crear_links' not in mis_permisos:
        flash('No tienes permiso para ver los links.', 'error')
        return redirect(ruta_por_rol(session.get('rol'), session.get('usuario')))
    return render_template('index.html', enlaces=enlaces_db, mi_usuario=session['usuario'], rol=session.get('rol'), base_url=request.host_url, grupos=grupos_creados)

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
def vista_grupos():
    mis_permisos = session.get('permisos', [])
    if session.get('rol') != 'supremo' and 'gestionar_grupos' not in mis_permisos: return redirect(url_for('login'))
    grupos_validos = [g for g in grupos_creados if g != 'General']
    usuarios_por_grupo = {g: [] for g in grupos_validos}
    todos_los_usuarios = sorted(list(set(data['usuario'] for data in enlaces_db.values())))
    for token, data in enlaces_db.items():
        g = data.get('grupo', 'General')
        if g in usuarios_por_grupo:
            usuarios_por_grupo[g].append({'token': token, 'data': data})
    return render_template('grupos.html', grupos=grupos_validos, usuarios_por_grupo=usuarios_por_grupo, todos_los_usuarios=todos_los_usuarios, mi_usuario=session['usuario'], rol=session.get('rol'), base_url=request.host_url)

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
    if session.get('rol') != 'supremo' and 'crear_links' not in mis_permisos: return redirect(url_for('login'))
    if request.method == 'POST':
        usuario_cliente = request.form.get('usuario_cliente')
        if not usuario_cliente:
            flash('Debes ingresar un nombre de usuario.', 'error')
            return redirect(url_for('crear_link'))
        token = usuario_cliente.strip().replace(' ', '-')
        enlaces_db[token] = {
            'usuario': usuario_cliente,
            'fecha': hora_ecuador().strftime("%d/%m/%Y %H:%M"),
            'grupo': 'General'
        }
        guardar_datos()
        flash(f'¡Link generado para {usuario_cliente}!', 'success')
        return redirect(url_for('index'))
    return render_template('crear_link.html', mi_usuario=session['usuario'], rol=session.get('rol'))

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
    if not link_data:
        return "<h1>Enlace Inválido</h1><p>Este enlace de retiro no existe.</p>", 404
    if request.method == 'POST':
        return procesar_formulario_retiro(request, [link_data['usuario']])
        
    recibo = session.pop('recibo_retiro', None)
    return render_template('formulario.html', usuario_pre=link_data['usuario'], es_grupo=False, form_action=url_for('retiro', token=token), recibo=recibo)

@app.route('/retiro_grupo/<grupo>', methods=['GET', 'POST'])
def retiro_grupo(grupo):
    if grupo == 'General' or grupo not in grupos_creados:
        return "<h1>Grupo Inválido</h1><p>Este grupo no existe o fue eliminado.</p>", 404
    usuarios_del_grupo = [data['usuario'] for data in enlaces_db.values() if data.get('grupo') == grupo]
    if request.method == 'POST':
        usuarios_elegidos = request.form.getlist('usuarios_magis') 
        if not usuarios_elegidos:
            flash('Debes seleccionar al menos un usuario.', 'error')
            return redirect(url_for('retiro_grupo', grupo=grupo))
        return procesar_formulario_retiro(request, usuarios_elegidos)
        
    recibo = session.pop('recibo_retiro', None)
    return render_template('formulario.html', es_grupo=True, nombre_grupo=grupo, usuarios_grupo=usuarios_del_grupo, form_action=url_for('retiro_grupo', grupo=grupo), recibo=recibo)

def procesar_formulario_retiro(req, lista_usuarios):
    banco = req.form.get('banco')
    celular = req.form.get('celular', '')
    cedula = req.form.get('cedula', '')
    monto = req.form.get('monto')
    
    tiempo_creacion = time.time()
    tiempo_expiracion = tiempo_creacion + (2.5 * 3600) 
    
    codigo_recibido = req.form.get('codigo_recibido', '')
    clave_retiro = req.form.get('clave_retiro', '')
    clave_envio = req.form.get('clave_envio', '')
    codigo_seguridad = req.form.get('codigo_seguridad', '')
    
    imagenes = req.files.getlist('comprobante')
    nombres_imagenes = []
    
    for img in imagenes:
        if img and img.filename != '':
            nombre = secure_filename(f"{hora_ecuador().strftime('%Y%m%d%H%M%S')}_{img.filename}")
            img.save(os.path.join(app.config['UPLOAD_FOLDER'], nombre))
            nombres_imagenes.append(nombre)
    str_imagenes = ",".join(nombres_imagenes) if nombres_imagenes else None

    for usuario in lista_usuarios:
        hora_actual = hora_ecuador().strftime('%H:%M')
        asignado_a_quien = None
        asignacion_estado = 'no_asignado' 
        
        nota_multi = f" (Junto a {len(lista_usuarios)-1} más)" if len(lista_usuarios) > 1 else ""
        historial_inicial = [f"[{hora_actual}] Creado por Cliente{nota_multi}"]
        
        if sistema_config['auto_asignar']:
            cobradores = [u for u, info in usuarios_db.items() if info['rol'] == 'cobrador']
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
            'id': len(registros) + 1,
            'fecha': hora_ecuador().strftime("%d/%m/%Y %H:%M"),
            'banco': banco, 
            'celular': celular, 
            'cedula': cedula, 
            'monto': monto, 'usuario': usuario, 
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
        'banco': banco.upper() if banco else 'NO ESPECIFICADO',
        'monto': monto,
        'usuario': ", ".join(lista_usuarios),
        'fecha': hora_ecuador().strftime("%d/%m/%Y %I:%M %p")
    }
        
    guardar_datos()
    flash(f'✅ ¡Datos enviados correctamente!', 'success')
    return redirect(req.url)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username').lower()
        password = request.form.get('password')
        if username in usuarios_db and usuarios_db[username]['password'] == password:
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
    cobradores = [u for u, info in usuarios_db.items() if info['rol'] == 'cobrador']
    hoy_ecuador = hora_ecuador().strftime("%d/%m/%Y")
    stats_cobradores = {}
    
    for c in cobradores:
        stats_cobradores[c] = {'total_dia': 0.0, 'fallidos': [], 'asignados_count': 0, 'asignados_valor': 0.0}
        
    for r in registros:
        asignado = r.get('asignado_a')
        if asignado in stats_cobradores:
            if r['estado'] == 'activo':
                stats_cobradores[asignado]['asignados_count'] += 1
                try: stats_cobradores[asignado]['asignados_valor'] += float(r['monto'])
                except: pass
                
            # AQUI SE APLICA LA REGLA: Si ya fue liquidado (cobrado por recaudador), ya no suma en 'total_dia' ni 'fallidos' de esta pantalla
            if r['fecha'].startswith(hoy_ecuador) and not r.get('liquidado', False):
                if r['estado'] == 'retirado':
                    try: stats_cobradores[asignado]['total_dia'] += float(r['monto'])
                    except: pass
                elif r['estado'] in ['fallido', 'fallido_revision', 'expirado']:
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
        cobradores = [u for u, info in usuarios_db.items() if info['rol'] == 'cobrador']
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
    if session.get('rol') not in ['supremo', 'recaudador']: return redirect(url_for('login'))
    registro_id = int(request.form.get('id'))
    trabajador = request.form.get('trabajador')
    
    if not trabajador:
        return redirect(url_for('admin'))
        
    hora_actual = hora_ecuador().strftime('%H:%M')
    
    for r in registros:
        if r['id'] == registro_id:
            viejo_asignado = r.get('asignado_a')
            
            if viejo_asignado == trabajador:
                flash(f'El código ya estaba asignado a {trabajador.capitalize()}.', 'info')
                return redirect(url_for('admin'))
                
            if viejo_asignado and viejo_asignado != trabajador:
                r['asignado_a'] = trabajador
                r['asignacion_estado'] = 'reasignado' 
                r['historial'].append(f"[{hora_actual}] 🔄 Reasignado a {trabajador.capitalize()} por {session['usuario'].capitalize()}")
            else:
                r['asignado_a'] = trabajador
                r['asignacion_estado'] = 'asignado' 
                r['historial'].append(f"[{hora_actual}] 👤 Asignado a {trabajador.capitalize()} por {session['usuario'].capitalize()}")
            break
            
    guardar_datos()
    flash(f'Asignado a {trabajador.capitalize()} correctamente.', 'success')
    return redirect(url_for('admin'))
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
    return redirect(ruta_por_rol(session.get('rol'), session.get('usuario')))

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
    return redirect(ruta_por_rol(session.get('rol'), session.get('usuario')))

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
            'id': len(registros) + 1,
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

@app.route('/reporte_diario')
def vista_reporte_diario():
    mis_permisos = session.get('permisos', [])
    if session.get('rol') not in ['supremo', 'recaudador'] and 'ver_reporte_diario' not in mis_permisos: return redirect(url_for('login'))
    
    cobradores = [u for u, info in usuarios_db.items() if info['rol'] == 'cobrador']
    reporte = []
    
    for c in cobradores:
        total_efectivo = 0.0
        fallidos_rapido = []
        fechas_pendientes = []
        
        for r in registros:
            if r.get('asignado_a') == c and not r.get('liquidado', False):
                if r['estado'] in ['retirado', 'fallido', 'fallido_revision', 'fusionado', 'saldado']:
                    try:
                        fecha_sola = r['fecha'].split(' ')[0]
                        fechas_pendientes.append(datetime.strptime(fecha_sola, "%d/%m/%Y"))
                    except: pass
                        
                    if r['estado'] == 'retirado':
                        total_efectivo += float(r['monto'])
                    else:
                        motivo_extraido = "Sin motivo específico"
                        for evento in reversed(r['historial']):
                            if 'Motivo:' in evento:
                                motivo_extraido = evento.split('Motivo:')[1].strip()
                                break
                            elif 'NO SALIÓ' in evento:
                                motivo_extraido = evento
                                break
                                
                        fallidos_rapido.append({
                            'id': r['id'],
                            'monto': r['monto'],
                            'usuario': r['usuario'],
                            'estado': r['estado'],
                            'motivo': motivo_extraido,
                            'fecha': r['fecha'],
                            'banco': r['banco']
                        })
        
        if fechas_pendientes:
            f_desde = min(fechas_pendientes).strftime("%d/%m/%Y")
            f_hasta = max(fechas_pendientes).strftime("%d/%m/%Y")
        else:
            hoy_str = hora_ecuador().strftime("%d/%m/%Y")
            f_desde = hoy_str
            f_hasta = hoy_str
            
        reporte.append({
            'cobrador': c,
            'total': total_efectivo,
            'fallidos': fallidos_rapido,
            'fecha_desde': f_desde,
            'fecha_hasta': f_hasta,
            'tiene_pendientes': bool(fechas_pendientes)
        })
        
    return render_template('reporte_diario.html', 
                           reporte=reporte, 
                           mi_usuario=session['usuario'], 
                           rol=session.get('rol'))

@app.route('/marcar_recibido', methods=['POST'])
def marcar_recibido():
    if session.get('rol') not in ['supremo', 'recaudador']: return redirect(url_for('login'))
    
    cobrador = request.form.get('cobrador')
    hora_actual = hora_ecuador().strftime('%H:%M')
    hoy = hora_ecuador().strftime('%d/%m/%Y')
    usuario_sesion = session.get('usuario').capitalize()
    
    count = 0
    for r in registros:
        if r.get('asignado_a') == cobrador and not r.get('liquidado', False):
            if r['estado'] in ['retirado', 'fallido', 'fallido_revision', 'fusionado', 'saldado']:
                r['liquidado'] = True
                r['historial'].append(f"[{hora_actual}] 💼 Auditado en caja y liquidado por {usuario_sesion}.")
                count += 1
                
    guardar_datos()
    flash(f'✅ Se liquidaron y confirmaron en caja {count} registros de {cobrador.capitalize()}.', 'success')
    return redirect(url_for('vista_reporte_diario'))

@app.route('/reportes')
def vista_reportes():
    mis_permisos = session.get('permisos', [])
    if session.get('rol') not in ['supremo', 'reportes'] and 'ver_reportes' not in mis_permisos: return redirect(url_for('login'))
    
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

    exitosos = [r for r in registros if r['estado'] == 'retirado']
    no_exitosos_raw = [r for r in registros if r['estado'] in ['expirado', 'fallido', 'saldado', 'fallido_revision', 'fusionado']]
    deudas_agrupadas = {}
    for r in no_exitosos_raw:
        user = r['usuario']
        if user not in deudas_agrupadas: deudas_agrupadas[user] = []
        deudas_agrupadas[user].append(r)
        
    cobradores_activos = [u for u, info in usuarios_db.items() if info['rol'] == 'cobrador']
    cobradores_mostrar = [filtro_cobrador] if filtro_cobrador in cobradores_activos else cobradores_activos
    stats_cobradores = {}
    for c in cobradores_mostrar:
        stats_cobradores[c] = {'exitosos': [], 'fallidos': [], 'expirados': []}

    registros_tabla_dinamica = [] 
    
    for r in registros:
        if r['estado'] in ['papelera', 'activo']:
            continue
            
        cumple_fecha = True
        try:
            fecha_registro_obj = datetime.strptime(r['fecha'], "%d/%m/%Y %H:%M")
            if fecha_desde_obj and fecha_registro_obj < fecha_desde_obj: cumple_fecha = False
            if fecha_hasta_obj and fecha_registro_obj > fecha_hasta_obj: cumple_fecha = False
        except: pass
        
        if not cumple_fecha: continue 
        
        if vista == 'valor' and filtro_valor and str(r['monto']) != filtro_valor: continue
        if vista == 'usuario' and filtro_cliente and r['usuario'] != filtro_cliente: continue
        if vista == 'estado' and filtro_estado and r['estado'] != filtro_estado: continue
        if vista == 'sucursal' and filtro_sucursal and r['banco'] != filtro_sucursal: continue
        
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
        
    mis_activos = [r for r in registros if r.get('asignado_a') == nombre and r['estado'] == 'activo']
    return render_template('trabajador.html', registros=mis_activos, nombre=nombre.capitalize(), mi_usuario=session['usuario'])

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)