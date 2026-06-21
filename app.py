import os
import random
import string
import csv
import io
import json
import base64
import time
import threading
import copy
import httpx
import requests
from pywebpush import webpush, WebPushException
from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify, send_from_directory, Blueprint, has_request_context
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

WEBHOOK_SOCIO_URL = os.environ.get('WEBHOOK_SOCIO_URL', 'https://api-socio.com/api/v1/webhooks/codigos-retiro')
WEBHOOK_SOCIO_API_KEY = os.environ.get('WEBHOOK_SOCIO_API_KEY', 'LaClaveSecretaQueElijamos123')
CODIGOS_RETIRO_WEBHOOK_API_KEY = os.environ.get('CODIGOS_RETIRO_WEBHOOK_API_KEY', '')
FERCHO_WEBHOOK_URL = 'https://whatsapp-registros-diarios.onrender.com/api/v1/webhooks/retiros'
FERCHO_WEBHOOK_KEY = os.environ.get('FERCHO_WEBHOOK_KEY', '')

app = Flask(__name__)
app.secret_key = "flujo_secreto_123"
app.permanent_session_lifetime = timedelta(days=365)

# ==========================================
# 💾 SISTEMA DE DISCO DURO PERSISTENTE 💾
# ==========================================
# Si el código detecta que está en Render, guarda JSON y FOTOS en el disco blindado (/var/data)
if os.path.exists('/var/data'):
    DATA_FILE = '/var/data/base_datos_erp.json'
    STAGING_DATA_FILE = '/var/data/registros_pruebas.json'
    STAGING_USERS_FILE = '/var/data/usuarios_pruebas.json'
    STAGING_COBRADORES_FILE = '/var/data/cobradores_pruebas.json'
    UPLOAD_FOLDER = '/var/data/uploads'
else:
    DATA_FILE = 'base_datos_local.json'
    STAGING_DATA_FILE = 'registros_pruebas.json'
    STAGING_USERS_FILE = 'usuarios_pruebas.json'
    STAGING_COBRADORES_FILE = 'cobradores_pruebas.json'
    UPLOAD_FOLDER = 'static/uploads'

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
os.makedirs(UPLOAD_FOLDER, exist_ok=True) # Construye la bóveda de fotos si no existe

def es_entorno_staging():
    if not has_request_context():
        return False
    return request.path.startswith('/pruebas')

@app.context_processor
def inject_entorno():
    # Retorna True si la URL actual empieza con /pruebas
    return dict(entorno_staging=es_entorno_staging())

# ==========================================
# 📸 RUTA MÁGICA PARA LEER LAS FOTOS DEL DISCO
# ==========================================
@app.route('/ver_imagen/<filename>')
def ver_imagen(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

# --- Variables Globales ---
registros = []
registros_pruebas = []
usuarios_db = {}
usuarios_pruebas = {}
cobradores_pruebas = {}
sistema_config = {'auto_asignar': False}
enlaces_db = {}
grupos_creados = [] 
liquidaciones_db = {}
ubicaciones_cobradores = {}
historial_pagos = []
suscripciones_push = {}

def guardar_datos():
    if es_entorno_staging():
        guardar_registros_pruebas()
        guardar_usuarios_pruebas()
        guardar_cobradores_pruebas()
        return
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

def cargar_registros_pruebas():
    global registros_pruebas
    if os.path.exists(STAGING_DATA_FILE):
        try:
            with open(STAGING_DATA_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                registros_pruebas = data.get('registros', [])
            print("✅ Registros de staging cargados desde", STAGING_DATA_FILE)
        except Exception as e:
            print("Error al cargar registros de staging:", e)

def guardar_registros_pruebas():
    try:
        with open(STAGING_DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump({'registros': registros_pruebas}, f, ensure_ascii=False, indent=4)
    except Exception as e:
        print("Error al guardar registros de staging:", e)

def cargar_usuarios_pruebas():
    global usuarios_pruebas
    if os.path.exists(STAGING_USERS_FILE):
        try:
            with open(STAGING_USERS_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                usuarios_pruebas = data.get('usuarios_db', data.get('usuarios', {}))
        except Exception as e:
            print("Error al cargar usuarios de staging:", e)

def guardar_usuarios_pruebas():
    try:
        with open(STAGING_USERS_FILE, 'w', encoding='utf-8') as f:
            json.dump({'usuarios_db': usuarios_pruebas}, f, ensure_ascii=False, indent=4)
    except Exception as e:
        print("Error al guardar usuarios de staging:", e)

def cargar_cobradores_pruebas():
    global cobradores_pruebas
    if os.path.exists(STAGING_COBRADORES_FILE):
        try:
            with open(STAGING_COBRADORES_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                cobradores_pruebas = data.get('cobradores_db', data.get('cobradores', {}))
        except Exception as e:
            print("Error al cargar cobradores de staging:", e)

def guardar_cobradores_pruebas():
    try:
        with open(STAGING_COBRADORES_FILE, 'w', encoding='utf-8') as f:
            json.dump({'cobradores_db': cobradores_pruebas}, f, ensure_ascii=False, indent=4)
    except Exception as e:
        print("Error al guardar cobradores de staging:", e)

def simulador_usuarios_vacio():
    return not usuarios_pruebas and not cobradores_pruebas

def _es_cobrador_simulador(info):
    return info.get('rol') == 'cobrador' or 'procesar_retiros' in info.get('permisos', [])

def sembrar_simulador_desde_produccion():
    if not usuarios_db:
        return False
    return sincronizar_usuarios_desde_produccion() > 0

def sincronizar_usuarios_desde_produccion():
    """Copia exacta de usuarios_db hacia los JSON de staging."""
    global usuarios_pruebas, cobradores_pruebas
    usuarios_pruebas = {}
    cobradores_pruebas = {}
    for username, info in usuarios_db.items():
        clave = str(username).lower()
        copia = copy.deepcopy(info)
        if _es_cobrador_simulador(copia):
            cobradores_pruebas[clave] = copia
        else:
            usuarios_pruebas[clave] = copia
    guardar_usuarios_pruebas()
    guardar_cobradores_pruebas()
    total = len(usuarios_pruebas) + len(cobradores_pruebas)
    print(f"✅ Simulador: {total} usuario(s) sincronizado(s) desde producción.")
    return total

def usuario_existe_en_staging(username):
    clave = str(username).lower()
    return clave in usuarios_pruebas or clave in cobradores_pruebas

def obtener_usuario_staging(username):
    clave = str(username).lower()
    if clave in usuarios_pruebas:
        return usuarios_pruebas[clave]
    return cobradores_pruebas.get(clave)

def guardar_usuario_en_staging(username, info):
    global usuarios_pruebas, cobradores_pruebas
    clave = str(username).lower()
    usuarios_pruebas.pop(clave, None)
    cobradores_pruebas.pop(clave, None)
    if _es_cobrador_simulador(info):
        cobradores_pruebas[clave] = info
    else:
        usuarios_pruebas[clave] = info
    guardar_usuarios_pruebas()
    guardar_cobradores_pruebas()

def eliminar_usuario_de_staging(username):
    global usuarios_pruebas, cobradores_pruebas
    clave = str(username).lower()
    eliminado = usuarios_pruebas.pop(clave, None) or cobradores_pruebas.pop(clave, None)
    if eliminado is not None:
        guardar_usuarios_pruebas()
        guardar_cobradores_pruebas()
        return True
    return False

def crear_usuario_simulador_por_defecto():
    global usuarios_pruebas, cobradores_pruebas
    usuarios_pruebas = {
        'fernando': {
            'password': '12345',
            'rol': 'recaudador',
            'permisos': ['ver_retiros', 'procesar_retiros'],
            'nombre': 'Fernando',
            'estado': 'Activo',
            'disponible': True,
        }
    }
    cobradores_pruebas = {}
    guardar_usuarios_pruebas()
    guardar_cobradores_pruebas()
    print("✅ Simulador: usuario por defecto creado (fernando / 12345).")

def asegurar_datos_simulador():
    """Si los JSON de prueba no existen o están vacíos, copia producción o crea Fernando."""
    if not simulador_usuarios_vacio():
        return
    if not sembrar_simulador_desde_produccion():
        crear_usuario_simulador_por_defecto()

def inicializar_simulador_por_defecto():
    asegurar_datos_simulador()

def db_registros():
    return registros_pruebas if es_entorno_staging() else registros

def db_usuarios():
    if es_entorno_staging():
        return {**usuarios_pruebas, **cobradores_pruebas}
    return usuarios_db

def login_url_simulador():
    return '/pruebas/login'

def asegurar_sesion_produccion():
    if 'usuario' not in session or session.get('entorno') != 'produccion':
        return redirect(url_for('login'))
    return None

def asegurar_sesion_simulador():
    if 'usuario' not in session or session.get('entorno') != 'pruebas':
        return redirect('/pruebas/login')
    return None

cargar_datos()
cargar_registros_pruebas()
cargar_usuarios_pruebas()
cargar_cobradores_pruebas()
inicializar_simulador_por_defecto()
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

def descargar_imagen_desde_url(url):
    try:
        with httpx.Client(timeout=30.0, follow_redirects=True) as client:
            response = client.get(url)
            response.raise_for_status()
            nombre_base = secure_filename(url.split('/')[-1].split('?')[0]) or 'comprobante.jpg'
            if '.' not in nombre_base:
                nombre_base = f"{nombre_base}.jpg"
            nombre = secure_filename(f"fercho_{hora_ecuador().strftime('%Y%m%d%H%M%S')}_{nombre_base}")
            with open(os.path.join(app.config['UPLOAD_FOLDER'], nombre), 'wb') as f:
                f.write(response.content)
            return nombre
    except Exception as ex:
        print(f"❌ Error descargando imagen Fercho desde {url}:", repr(ex))
        return None

@app.route('/api/v1/recibir_ticket_socio', methods=['POST'])
def recibir_ticket_socio():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({'error': 'JSON inválido'}), 400

    cfo_ticket_id = data.get('cfo_ticket_id')
    if not cfo_ticket_id:
        return jsonify({'error': 'cfo_ticket_id es requerido'}), 400

    for r in db_registros():
        if r.get('referencia_externa') == cfo_ticket_id and r.get('origen_socio') == 'fercho':
            return jsonify({'error': 'Ticket duplicado'}), 409

    banco = str(data.get('banco', '')).strip().lower()
    monto_total_str = str(data.get('monto', '')).strip()
    usuario = str(data.get('usuario', 'Desconocido')).strip()
    celular = str(data.get('celular', '')).strip()
    cedula = str(data.get('cedula', '')).strip()

    codigo_recibido = str(data.get('codigo_pichincha', '')).strip()
    clave_retiro = str(data.get('guayaquil_retiro', '')).strip()
    clave_envio = str(data.get('guayaquil_envio', '')).strip()
    codigo_seguridad = str(data.get('seguridad', '')).strip()

    url_imagen = (
        data.get('url_imagen')
        or data.get('imagen_url')
        or data.get('image_url')
        or data.get('url')
    )
    str_imagenes = None
    if url_imagen:
        nombre_guardado = descargar_imagen_desde_url(str(url_imagen).strip())
        if nombre_guardado:
            str_imagenes = nombre_guardado

    transaccion_id, error = insertar_registro_retiro(
        banco, celular, cedula, monto_total_str,
        codigo_recibido, clave_retiro, clave_envio, codigo_seguridad,
        str_imagenes, [f"FERCHO - {usuario}"],
        origen_historial='Recibido vía API Fercho',
        referencia_externa=cfo_ticket_id,
        origen_socio='fercho',
    )

    if error == 'duplicado':
        return jsonify({'error': 'Código de retiro duplicado'}), 409

    return jsonify({'status': 'ok'})

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

ESTADOS_DEUDA_CERRABLE_ERP = ['fallido', 'expirado', 'fallido_revision']

def normalizar_referencia_venta(val):
    if val is None:
        return None
    ref = str(val).strip()
    return ref if ref else None

def extraer_referencias_payload_erp(data):
    referencias = []
    for key in ('referencia_externa', 'sale_id', 'meta_sale_id'):
        ref = normalizar_referencia_venta(data.get(key))
        if ref and ref not in referencias:
            referencias.append(ref)
    return referencias

def registro_coincide_referencia_erp(registro, referencias):
    ref_registro = normalizar_referencia_venta(registro.get('referencia_externa'))
    if not ref_registro:
        return False
    return ref_registro in referencias

def format_num_deuda(val):
    return int(val) if float(val).is_integer() else round(float(val), 2)

def aplicar_pago_erp_a_deuda(deuda_record, monto_aprobado, referencia_usada):
    """Cierra o abona una deuda marcada como No salió / expirada según el pago del ERP."""
    hora_actual = hora_ecuador().strftime('%d/%m/%Y %H:%M')
    monto_deuda = float(deuda_record.get('monto', 0) or 0)
    estado_anterior = deuda_record.get('estado')

    deuda_record['recuperacion_erp'] = True
    deuda_record['referencia_erp_pago'] = referencia_usada

    if monto_aprobado is None:
        deuda_record['estado'] = 'saldado'
        deuda_record['historial'].append(
            f"[{hora_actual}] ✅ Saldada por ERP — Pago alternativo aprobado (ref. {referencia_usada}). "
            f"Estado anterior: {estado_anterior}."
        )
        return 'saldado_total'

    if monto_aprobado >= monto_deuda:
        deuda_record['estado'] = 'saldado'
        deuda_record['historial'].append(
            f"[{hora_actual}] ✅ Deuda recuperada — Pago aprobado por ERP "
            f"(ref. {referencia_usada}, ${format_num_deuda(monto_aprobado)}). "
            f"Estado anterior: {estado_anterior}."
        )
        return 'saldado_total'

    restante = monto_deuda - monto_aprobado
    deuda_record['monto'] = str(format_num_deuda(restante))
    deuda_record['historial'].append(
        f"[{hora_actual}] ⚠️ Abono parcial registrado por ERP "
        f"(${format_num_deuda(monto_aprobado)}). Ref: {referencia_usada}. "
        f"Saldo pendiente: ${format_num_deuda(restante)}."
    )
    return 'abono_parcial'

def procesar_pago_aprobado_erp(data):
    referencias = extraer_referencias_payload_erp(data)
    if not referencias:
        return None, 'Se requiere referencia_externa, sale_id o meta_sale_id', 400

    es_prueba = bool(data.get('es_prueba'))
    regs = registros_pruebas if es_prueba else registros

    monto_raw = data.get('monto')
    if monto_raw is None:
        monto_raw = data.get('monto_aprobado', data.get('amount'))

    monto_aprobado = None
    if monto_raw is not None and str(monto_raw).strip() != '':
        try:
            monto_aprobado = float(monto_raw)
            if monto_aprobado < 0:
                return None, 'El monto aprobado no puede ser negativo', 400
        except (ValueError, TypeError):
            return None, 'monto inválido', 400

    referencia_principal = referencias[0]
    actualizados = []

    for r in regs:
        if r.get('estado') not in ESTADOS_DEUDA_CERRABLE_ERP:
            continue
        if not registro_coincide_referencia_erp(r, referencias):
            continue

        resultado = aplicar_pago_erp_a_deuda(r, monto_aprobado, referencia_principal)
        actualizados.append({
            'id': r['id'],
            'usuario': r.get('usuario'),
            'resultado': resultado,
            'estado_nuevo': r.get('estado'),
            'monto_restante': r.get('monto'),
        })

    if not actualizados:
        return None, 'No se encontró deuda activa (No salió / expirada) asociada a esa referencia', 404

    if es_prueba:
        guardar_registros_pruebas()
    else:
        guardar_datos()

    return actualizados, 'ok', 200

@app.route('/api/webhook/erp/pago-aprobado', methods=['POST'])
def webhook_erp_pago_aprobado():
    """Recibe alertas del ERP cuando un cliente paga una factura retrasada/rechazada."""
    if not CODIGOS_RETIRO_WEBHOOK_API_KEY:
        return jsonify({'error': 'Webhook no configurado en el servidor'}), 503

    api_key = request.headers.get('X-API-Key')
    if not api_key:
        auth_header = request.headers.get('Authorization', '')
        if auth_header.lower().startswith('bearer '):
            api_key = auth_header[7:].strip()

    if api_key != CODIGOS_RETIRO_WEBHOOK_API_KEY:
        return jsonify({'error': 'No autorizado'}), 401

    data = request.get_json(silent=True)
    if not data:
        return jsonify({'error': 'JSON inválido'}), 400

    actualizados, mensaje, codigo = procesar_pago_aprobado_erp(data)
    if codigo != 200:
        return jsonify({'error': mensaje}), codigo

    print(f"✅ Webhook ERP pago aprobado: {len(actualizados)} deuda(s) procesada(s) — ref. {extraer_referencias_payload_erp(data)}")
    return jsonify({
        'status': 'ok',
        'mensaje': f'{len(actualizados)} deuda(s) procesada(s)',
        'registros_actualizados': actualizados,
    }), 200

@app.before_request
def mantenimiento_datos():
    cambios_realizados = False
    hora_actual = hora_ecuador().strftime('%d/%m/%Y %H:%M')
    tiempo_ahora = time.time()
    regs = db_registros()

    # --- 🛠️ AUTO-REPARADOR RETROACTIVO DE DEUDAS ---
    deudas_vistas = set()
    # Iteramos desde el código más viejo al más nuevo usando el ID
    for r in sorted(regs, key=lambda x: x.get('id', 0)):
        estado_actual = r.get('estado')
        usuario = r.get('usuario')

        if estado_actual == 'fallido':
            # Si ya habíamos visto una deuda más vieja de este usuario, lo pasamos a revisión
            if usuario in deudas_vistas:
                r['estado'] = 'fallido_revision'
                cambios_realizados = True

        # Si el estado cuenta como deuda, anotamos a este usuario para los códigos que le sigan
        if r.get('estado') in ['fallido', 'expirado', 'fallido_revision']:
            if usuario:
                deudas_vistas.add(usuario)
    # ------------------------------------------------

    # --- 🛠️ AUTO-REPARADOR DE IDs DUPLICADOS ---
    ids_vistos = set()
    # Leemos la lista al revés para que los códigos viejos conserven su ID original
    for r in reversed(regs):
        if r.get('id') in ids_vistos:
            # Si encontramos un clon, le damos un ID único basado en milisegundos
            r['id'] = int(tiempo_ahora * 1000) + random.randint(1, 9999)
            cambios_realizados = True
        ids_vistos.add(r.get('id'))
    
    for r in regs:
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

        # === NUEVO: ALERTA DE DEUDA PASADA 24 HORAS ===
        if not es_entorno_staging() and r['estado'] in ['fallido', 'fallido_revision', 'expirado'] and not r.get('notificado_deuda_1dia'):
            debe_notificar = False
            
            if 'timestamp_creacion' in r:
                if (tiempo_ahora - r['timestamp_creacion']) >= 86400: 
                    debe_notificar = True
            else:
                try:
                    creacion_dt = datetime.strptime(r['fecha'], "%d/%m/%Y %H:%M")
                    if (hora_ecuador() - creacion_dt).total_seconds() >= 86400:
                        debe_notificar = True
                except:
                    pass
            
            if debe_notificar:
                r['notificado_deuda_1dia'] = True
                cambios_realizados = True
                
                # Filtrar SOLO usuarios que tengan el permiso 'notificar_deuda'
                auditores = [u for u, info in usuarios_db.items() if 'notificar_deuda' in info.get('permisos', [])]
                
                for auditor in auditores:
                    disparar_alerta_push(auditor, "⚠️ Deuda Pendiente (24h)", f"El código de ${r.get('monto')} de {r.get('usuario')} lleva 1 día caído y no ha sido saldado.")
        # ===============================================
            
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
    if rol in ['reportes', 'notificacion_deuda']: 
        return '/reportes'
        
    return '/'

def ruta_por_rol_simulador(rol, usuario):
    permisos = session.get('permisos', [])
    prefijo = '/pruebas'
    if rol == 'supremo':
        return f'{prefijo}/admin'
    if 'ver_retiros' in permisos:
        return f'{prefijo}/admin'
    if 'procesar_retiros' in permisos:
        return f'{prefijo}/trabajador/{usuario}'
    if rol == 'recaudador':
        return f'{prefijo}/admin'
    if rol == 'cobrador':
        return f'{prefijo}/trabajador/{usuario}'
    return f'{prefijo}/admin'

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

@app.route('/api/historial_cliente/<path:usuario>')
def api_historial_cliente(usuario):
    """Historial absoluto de movimientos del cliente para estado de cuenta en index."""
    if 'usuario' not in session:
        return jsonify({'error': 'No autorizado'}), 403
    mis_permisos = session.get('permisos', [])
    if session.get('rol') != 'supremo' and 'crear_links' not in mis_permisos:
        return jsonify({'error': 'No autorizado'}), 403

    usuario_buscar = (usuario or '').strip()
    if not usuario_buscar:
        return jsonify([])

    resultados = []
    for r in registros:
        if r.get('estado') == 'papelera':
            continue
        if usuario_buscar in r.get('usuario', ''):
            resultados.append({
                'id': r.get('id'),
                'fecha': r.get('fecha', ''),
                'banco': r.get('banco') or '',
                'monto': r.get('monto') or '',
                'estado': r.get('estado') or '',
                'asignado_a': r.get('asignado_a'),
                'imagen': r.get('imagen', ''),
                'imagen_fallo': r.get('imagen_fallo', ''),
            })

    resultados.sort(
        key=lambda x: x['id'] if isinstance(x.get('id'), (int, float)) else 0,
        reverse=True,
    )
    return jsonify(resultados)

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

def mapear_banco_desde_ocr(banco_raw):
    b = (banco_raw or '').lower()
    if 'pichincha' in b:
        return 'pichincha'
    if 'guayaquil' in b:
        return 'guayaquil'
    if 'produbanco' in b:
        return 'produbanco'
    return b.strip() or 'otro'

def mapear_codigos_desde_ocr(banco, datos_ia):
    codigo_recibido = clave_retiro = clave_envio = codigo_seguridad = ''
    clave = datos_ia.get('CLAVE_RETIRO', '')
    envio = datos_ia.get('CLAVE_ENVIO', '')
    if banco in ('pichincha', 'produbanco'):
        codigo_recibido = clave
    elif banco == 'guayaquil':
        clave_retiro = clave
        clave_envio = envio
    else:
        codigo_seguridad = clave
    return codigo_recibido, clave_retiro, clave_envio, codigo_seguridad

def guardar_comprobantes_desde_bytes(items):
    nombres_imagenes = []
    for img_bytes, filename in items:
        if not filename:
            continue
        nombre = secure_filename(f"{hora_ecuador().strftime('%Y%m%d%H%M%S')}_{filename}")
        with open(os.path.join(app.config['UPLOAD_FOLDER'], nombre), 'wb') as f:
            f.write(img_bytes)
        nombres_imagenes.append(nombre)
    return ",".join(nombres_imagenes) if nombres_imagenes else None

def insertar_registro_retiro(banco, celular, cedula, monto_total_str, codigo_recibido, clave_retiro, clave_envio, codigo_seguridad, str_imagenes, lista_usuarios, origen_historial='Creado por Cliente', req=None, referencia_externa=None, origen_socio=None, es_prueba=False):
    import hashlib

    regs = db_registros()
    if es_entorno_staging():
        es_prueba = True

    tiempo_creacion = time.time()
    horas_expiracion = 12 if banco == 'guayaquil' else 2.5
    tiempo_expiracion = tiempo_creacion + (horas_expiracion * 3600)

    codigos_unidos = f"{codigo_recibido}{clave_retiro}{clave_envio}{codigo_seguridad}".strip()

    if codigos_unidos:
        hash_input = f"{monto_total_str}-{codigos_unidos}".encode('utf-8')
        transaccion_id = f"TRX-{hashlib.md5(hash_input).hexdigest()[:8].upper()}"
        for r in regs:
            if r.get('transaccion_id') == transaccion_id and r.get('estado') in ['activo', 'retirado']:
                return None, 'duplicado'
    else:
        transaccion_id = f"TRX-{int(tiempo_creacion)}"

    hora_actual = hora_ecuador().strftime('%d/%m/%Y %H:%M')
    asignado_a_quien = None
    asignacion_estado = 'no_asignado'

    is_split = len(lista_usuarios) > 1
    usuarios_juntos = " + ".join(lista_usuarios)
    historial_inicial = []

    if is_split:
        detalles_desglose = []
        for u in lista_usuarios:
            monto_u = req.form.get(f'monto_usuario_{u}', '0.00') if req else '0.00'
            detalles_desglose.append(f"${monto_u} a {u}")
        texto_desglose = " | ".join(detalles_desglose)
        historial_inicial.append(f"[{hora_actual}] {origen_historial} (Múltiple: {texto_desglose})")
    else:
        historial_inicial.append(f"[{hora_actual}] {origen_historial}")

    if es_prueba:
        historial_inicial.insert(0, f"[{hora_actual}] 🧪 CÓDIGO DE PRUEBA — No es dinero real (Staging ERP)")

    if sistema_config['auto_asignar']:
        cobradores = [u for u, info in db_usuarios().items() if info['rol'] == 'cobrador' or 'procesar_retiros' in info.get('permisos', [])]
        if cobradores:
            cargas = {c: 0 for c in cobradores}
            for r in regs:
                if r['estado'] == 'activo' and r['asignado_a'] in cargas:
                    cargas[r['asignado_a']] += 1
            mejor_cobrador = min(cargas, key=cargas.get)
            asignado_a_quien = mejor_cobrador
            asignacion_estado = 'asignado'
            historial_inicial.append(f"[{hora_actual}] 👤 Asignado a {mejor_cobrador.capitalize()} (Robot)")

    nuevo_registro = {
        'id': int(time.time() * 1000) + random.randint(1, 999),
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

    if referencia_externa is not None and str(referencia_externa).strip():
        nuevo_registro['referencia_externa'] = str(referencia_externa).strip()
    if origen_socio:
        nuevo_registro['origen_socio'] = origen_socio
    nuevo_registro['es_prueba'] = es_prueba
    if es_prueba:
        nuevo_registro['codigo_prueba'] = True
    if es_entorno_staging():
        nuevo_registro['entorno_staging'] = True

    regs.insert(0, nuevo_registro)
    guardar_datos()

    if not es_entorno_staging():
        admin_users = [u for u, info in db_usuarios().items() if info['rol'] in ['supremo', 'recaudador', 'cobrador'] or 'procesar_retiros' in info.get('permisos', [])]
        for admin in admin_users:
            disparar_alerta_push(admin, "¡Nuevo Retiro Cliente! 💰", f"Se han ingresado ${monto_total_str} del banco {banco}.")

        if asignado_a_quien:
            disparar_alerta_push(asignado_a_quien, "¡Retiro Asignado! 🏃‍♂️", f"Te cayó un código de ${monto_total_str} ({banco}). ¡Revisa tu bandeja!")

    return transaccion_id, None

@app.route('/widget_retiro', methods=['GET', 'POST'])
def widget_retiro():
    return vista_widget_retiro(form_action=url_for('widget_retiro'))

@app.route('/pruebas/widget_retiro', methods=['GET', 'POST'])
def widget_retiro_pruebas():
    """Widget de staging para tráfico de prueba del ERP socio — aislado y marcado visualmente."""
    return vista_widget_retiro(
        form_action=url_for('widget_retiro_pruebas'),
        forzar_codigo_prueba=True,
    )

def vista_widget_retiro(form_action=None, forzar_codigo_prueba=False):
    horario = sistema_config.get('horario_activo', True)
    bancos_activos = sistema_config.get('bancos_activos', {'pichincha': True, 'guayaquil': True, 'produbanco': True})

    token = request.args.get('token', request.form.get('token', '')).strip()
    usuario_param = request.args.get('usuario', request.form.get('usuario', '')).strip()

    if token and token in enlaces_db:
        usuario_widget = enlaces_db[token]['usuario']
    elif usuario_param:
        usuario_widget = usuario_param
    else:
        usuario_widget = 'Widget-Externo'

    if request.method == 'GET':
        cliente_externo = request.args.get('cliente', 'Desconocido')
        referencia_externa = request.args.get('referencia_externa', '').strip()
        modo_prueba = 'prueba' if forzar_codigo_prueba else request.args.get('modo', 'real')
        return render_template('widget_retiro.html', usuario=usuario_widget, token=token, horario_activo=horario, bancos_activos=bancos_activos, cliente_externo=cliente_externo, referencia_externa=referencia_externa, modo_prueba=modo_prueba, form_action=form_action, es_codigo_prueba=forzar_codigo_prueba)

    referencia_externa = (request.form.get('referencia_externa') or request.args.get('referencia_externa') or '').strip()
    modo_prueba = 'prueba' if forzar_codigo_prueba else request.form.get('modo_prueba', 'real')
    nombre_cliente = request.form.get('cliente_externo', 'Desconocido')
    cliente_externo = nombre_cliente

    # === ETIQUETAS VISUALES PARA LA BANDEJA Y EL HISTORIAL ===
    if forzar_codigo_prueba or es_entorno_staging() or modo_prueba == 'prueba':
        usuario_registro = f"🔴 [PRUEBA] {usuario_widget.upper()} - {cliente_externo}"
        es_prueba = True
        origen = '🔴 CÓDIGO DE PRUEBA (Staging)'
    else:
        usuario_registro = f"WIDGET - {nombre_cliente}"
        es_prueba = False
        origen = 'Creado por Widget Externo'

    if not horario:
        return render_template('widget_retiro.html', usuario=usuario_widget, token=token, horario_activo=horario, bancos_activos=bancos_activos, cliente_externo=cliente_externo, referencia_externa=referencia_externa, modo_prueba=modo_prueba, form_action=form_action, es_codigo_prueba=forzar_codigo_prueba, error='Sistema fuera de horario.'), 403

    banco_seleccionado = request.form.get('banco')
    if banco_seleccionado and not bancos_activos.get(banco_seleccionado, True):
        return render_template('widget_retiro.html', usuario=usuario_widget, token=token, horario_activo=horario, bancos_activos=bancos_activos, cliente_externo=cliente_externo, referencia_externa=referencia_externa, modo_prueba=modo_prueba, form_action=form_action, es_codigo_prueba=forzar_codigo_prueba, error=f'El banco {banco_seleccionado.capitalize()} se encuentra temporalmente fuera de servicio.'), 403

    # Se envía el origen modificado a la función principal de guardado
    return procesar_formulario_retiro(
        request, [usuario_registro], modo_widget=True, origen_historial=origen, es_prueba=es_prueba,
        modo_prueba=modo_prueba, form_action=form_action,
    )

def procesar_formulario_retiro(req, usuarios, modo_widget=False, origen_historial=None, es_prueba=False, modo_prueba='real', form_action=None):
    referencia_externa = (req.form.get('referencia_externa') or req.args.get('referencia_externa') or '').strip() or None
    if origen_historial is None:
        origen_historial = 'Creado por Cliente'

    banco = req.form.get('banco')
    celular = req.form.get('celular', '')
    cedula = req.form.get('cedula', '')
    monto_total_str = req.form.get('monto')

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

    transaccion_id, error = insertar_registro_retiro(
        banco, celular, cedula, monto_total_str,
        codigo_recibido, clave_retiro, clave_envio, codigo_seguridad,
        str_imagenes, usuarios,
        origen_historial=origen_historial,
        req=req,
        referencia_externa=referencia_externa,
        origen_socio='alex' if modo_widget else None,
        es_prueba=es_prueba
    )

    if error == 'duplicado':
        if modo_widget:
            horario = sistema_config.get('horario_activo', True)
            bancos_activos = sistema_config.get('bancos_activos', {'pichincha': True, 'guayaquil': True, 'produbanco': True})
            token = req.form.get('token', '').strip()
            usuario_widget = req.form.get('usuario', 'Widget-Externo')
            cliente_externo = req.form.get('cliente_externo', 'Desconocido')
            referencia_externa = (req.form.get('referencia_externa') or req.args.get('referencia_externa') or '').strip()
            return render_template('widget_retiro.html', usuario=usuario_widget, token=token, horario_activo=horario, bancos_activos=bancos_activos, cliente_externo=cliente_externo, referencia_externa=referencia_externa, modo_prueba=modo_prueba, form_action=form_action, error='Este código de retiro ya fue ingresado al sistema.'), 409
        flash('⚠️ ADVERTENCIA: Este código de retiro ya fue ingresado al sistema. No se puede duplicar.', 'error')
        return redirect(req.url)

    if modo_widget:
        monto_js = json.dumps(monto_total_str)
        return f'<script>window.parent.postMessage({{tipo: "RETIRO_COMPLETADO", monto: {monto_js}}}, "*");</script>'

    is_split = len(usuarios) > 1
    usuarios_para_recibo = ""
    if is_split:
        recibo_desglose = []
        for u in usuarios:
            monto_u = req.form.get(f'monto_usuario_{u}', '0.00')
            recibo_desglose.append(f"{u} (${monto_u})")
        usuarios_para_recibo = "<br>".join(recibo_desglose)
    else:
        usuarios_para_recibo = usuarios[0]

    session['recibo_retiro'] = {
        'transaccion_id': transaccion_id,
        'banco': banco.upper() if banco else 'NO ESPECIFICADO',
        'monto': monto_total_str,
        'usuario': usuarios_para_recibo,
        'fecha': hora_ecuador().strftime("%d/%m/%Y %I:%M %p")
    }

    flash(f'✅ ¡Datos enviados correctamente!', 'success')
    return redirect(req.url)

@app.route('/login', methods=['GET', 'POST'])
def login():
    return vista_login(url_prefix='')

def vista_login(url_prefix=''):
    if url_prefix:
        asegurar_datos_simulador()

    entorno_solicitado = 'pruebas' if url_prefix else 'produccion'
    if 'usuario' in session and session.get('entorno') == entorno_solicitado and request.method == 'GET':
        if url_prefix:
            return redirect(ruta_por_rol_simulador(session['rol'], session['usuario']))
        return redirect(ruta_por_rol(session['rol'], session['usuario']))

    if request.method == 'POST':
        username = request.form.get('username').lower()
        password = request.form.get('password')
        users = db_usuarios()
        if username in users and users[username]['password'] == password:
            session.permanent = True
            session['usuario'] = username
            session['rol'] = users[username]['rol']
            session['permisos'] = users[username].get('permisos', [])
            session['entorno'] = entorno_solicitado
            if url_prefix:
                return redirect(ruta_por_rol_simulador(session['rol'], username))
            return redirect(ruta_por_rol(session['rol'], username))
        flash('Usuario o contraseña incorrectos', 'error')

    form_action = f'{url_prefix}/login' if url_prefix else url_for('login')
    return render_template('login.html', form_action=form_action, entorno_staging=bool(url_prefix))

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
# --- MODIFICAR LA RUTA /admin EN APP.PY ---
# (Solo te muestro la parte que cambia dentro de la función admin())
@app.route('/admin')
def admin():
    return vista_admin(url_prefix='')

def vista_admin(url_prefix=''):
    bloqueo = asegurar_sesion_simulador() if url_prefix else asegurar_sesion_produccion()
    if bloqueo:
        return bloqueo

    mis_permisos = session.get('permisos', [])
    login_route = f'{url_prefix}/login' if url_prefix else url_for('login')
    if session.get('rol') not in ['supremo', 'recaudador'] and 'ver_retiros' not in mis_permisos:
        return redirect(login_route)
    
    regs = db_registros()
    activos = [r for r in regs if r['estado'] == 'activo']
    users = db_usuarios()
    
    cobradores_raw = [u for u, info in users.items() if info['rol'] == 'cobrador' or 'procesar_retiros' in info.get('permisos', [])]
    
    cobradores = []
    for c in cobradores_raw:
        esta_disponible = users[c].get('disponible', True)
        cobradores.append({
            'username': c,
            'nombre_mostrar': users[c].get('nombre', c).capitalize(),
            'disponible': esta_disponible
        })
    
    hoy_ecuador = hora_ecuador().strftime("%d/%m/%Y")
    stats_cobradores = {}
    
    for c_dict in cobradores:
        c_nombre = c_dict['username']
        stats_cobradores[c_nombre] = {
            'total_dia': 0.0, 
            'total_acumulado': 0.0, 
            'desglose_fechas': {}, 
            'fallidos': [], 
            'asignados_count': 0, 
            'asignados_valor': 0.0
        }
        
    for r in regs:
        asignado = r.get('asignado_a')
        if asignado in stats_cobradores:
            if url_prefix or not r.get('es_prueba', False):
                if r['estado'] == 'activo':
                    stats_cobradores[asignado]['asignados_count'] += 1
                    try: stats_cobradores[asignado]['asignados_valor'] += float(r['monto'])
                    except: pass
                    
                if not r.get('liquidado', False):
                    if r['estado'] == 'retirado':
                        try:
                            monto = float(r['monto'])
                            stats_cobradores[asignado]['total_acumulado'] += monto
                            
                            fecha_corta = r['fecha'].split(' ')[0]
                            if fecha_corta not in stats_cobradores[asignado]['desglose_fechas']:
                                stats_cobradores[asignado]['desglose_fechas'][fecha_corta] = 0.0
                            stats_cobradores[asignado]['desglose_fechas'][fecha_corta] += monto
                            
                            if r['fecha'].startswith(hoy_ecuador):
                                stats_cobradores[asignado]['total_dia'] += monto
                        except: pass
                        
                    # Mostrar las deudas SIEMPRE hasta que se salden. 
                    # Los expirados los dejamos solo por hoy para que no se acumule basura.
                    elif r['estado'] in ['fallido', 'fallido_revision']:
                        stats_cobradores[asignado]['fallidos'].append(r)
                    elif r['estado'] == 'expirado' and r['fecha'].startswith(hoy_ecuador):
                        stats_cobradores[asignado]['fallidos'].append(r)
                
    return render_template('admin.html', 
                           activos=activos, 
                           cobradores=cobradores, # Ahora es una lista de diccionarios
                           stats_cobradores=stats_cobradores, 
                           mi_usuario=session['usuario'], 
                           rol=session.get('rol'),
                           auto_asignar=sistema_config['auto_asignar'],
                           usuarios_db=users,
                           url_prefix=url_prefix,
                           entorno_staging=bool(url_prefix))

@app.route('/toggle_auto', methods=['POST'])
def toggle_auto():
    if session.get('rol') not in ['supremo', 'recaudador']: return redirect(url_for('login'))
    sistema_config['auto_asignar'] = not sistema_config['auto_asignar']
    hora_actual = hora_ecuador().strftime('%d/%m/%Y %H:%M')
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
    return ejecutar_asignar(url_prefix='')

def ejecutar_asignar(url_prefix=''):
    bloqueo = asegurar_sesion_simulador() if url_prefix else asegurar_sesion_produccion()
    if bloqueo:
        return bloqueo

    mis_permisos = session.get('permisos', [])
    login_route = f'{url_prefix}/login' if url_prefix else url_for('login')
    
    if session.get('rol') not in ['supremo', 'recaudador'] and 'ver_retiros' not in mis_permisos and 'procesar_retiros' not in mis_permisos: 
        return redirect(login_route)
        
    url_retorno = request.referrer or (f'{url_prefix}/admin' if url_prefix else url_for('admin'))
    
    try:
        registro_id = int(request.form.get('id', 0))
    except (TypeError, ValueError):
        return redirect(url_retorno)
        
    trabajador = request.form.get('trabajador')
    if not trabajador:
        return redirect(url_retorno)
        
    hora_actual = hora_ecuador().strftime('%d/%m/%Y %H:%M')
    regs = db_registros()
    
    for r in regs:
        if r['id'] == registro_id:
            viejo_asignado = r.get('asignado_a')
            
            # --- NUEVO BLOQUE PARA DESASIGNAR ---
            if trabajador == '__SIN_ASIGNAR__':
                r['asignado_a'] = None
                r['asignacion_estado'] = 'no_asignado'
                r['visto_por_cobrador'] = False
                r['historial'].append(f"[{hora_actual}] 🔄 Movido a 'Sin Asignar' por {session['usuario'].capitalize()}")
                break
            # ------------------------------------

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
            
            if not es_entorno_staging():
                disparar_alerta_push(trabajador, "¡Nuevo Retiro Asignado! 🏃‍♂️", "Tienes un nuevo código de retiro listo en tu bandeja.")
            break
            
    guardar_datos()
    if trabajador == '__SIN_ASIGNAR__':
        flash('El código ha sido desasignado exitosamente.', 'success')
    else:
        flash(f'Asignado a {trabajador.capitalize()} correctamente.', 'success')
    
    return redirect(url_retorno)
# ==========================================
# RUTAS DE PAPELERA DE RECICLAJE
# ==========================================
@app.route('/mover_papelera', methods=['POST'])
def mover_papelera():
    mis_permisos = session.get('permisos', [])
    if session.get('rol') not in ['supremo', 'reportes'] and 'ver_reportes' not in mis_permisos:
        return redirect(url_for('login'))

    registro_id = int(request.form.get('id'))
    motivo = request.form.get('motivo_borrado', 'Sin motivo')
    prefix = request.form.get('url_prefix', '')
    hora_actual = hora_ecuador().strftime('%d/%m/%Y %H:%M')

    regs = db_registros()
    for r in regs:
        if r['id'] == registro_id:
            r['estado_previo'] = r['estado']
            r['estado'] = 'papelera'
            r['historial'].append(f"[{hora_actual}] 🗑️ Movido a papelera por {session['usuario'].capitalize()}. Motivo: {motivo}")
            break

    guardar_datos()
    flash('Registro movido a la papelera.', 'success')

    # Retornar al administrador o a reportes manteniendo el entorno
    if 'reportes' in request.referrer:
        return redirect(request.referrer)
    return redirect(f"{prefix}/admin")

@app.route('/restaurar_papelera', methods=['POST'])
def restaurar_papelera():
    mis_permisos = session.get('permisos', [])
    if session.get('rol') not in ['supremo', 'reportes'] and 'ver_reportes' not in mis_permisos: return redirect(url_for('login'))
    
    registro_id = int(request.form.get('id'))
    hora_actual = hora_ecuador().strftime('%d/%m/%Y %H:%M')
    
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
    return render_vista_papelera(url_prefix='')

def render_vista_papelera(url_prefix=''):
    bloqueo = asegurar_sesion_simulador() if url_prefix else asegurar_sesion_produccion()
    if bloqueo:
        return bloqueo
    login_route = f'{url_prefix}/login' if url_prefix else url_for('login')
    if session.get('rol') not in ['supremo', 'reportes'] and 'ver_reportes' not in session.get('permisos', []):
        return redirect(login_route)
    eliminados = [r for r in db_registros() if r['estado'] == 'papelera']
    return render_template('papelera.html', eliminados=eliminados, mi_usuario=session['usuario'], rol=session.get('rol'), url_prefix=url_prefix)

@app.route('/marcar_retirado', methods=['POST'])
def marcar_retirado():
    return ejecutar_marcar_retirado()

def ejecutar_marcar_retirado(registro_id=None, banco_real=None):
    bloqueo = asegurar_sesion_simulador() if es_entorno_staging() else asegurar_sesion_produccion()
    if bloqueo:
        return bloqueo

    mis_permisos = session.get('permisos', [])
    login_route = login_url_simulador() if es_entorno_staging() else url_for('login')
    if session.get('rol') not in ['supremo', 'cobrador'] and 'procesar_retiros' not in mis_permisos:
        return redirect(login_route)

    if registro_id is None:
        registro_id = int(request.form.get('id'))
    if banco_real is None:
        banco_real = request.form.get('banco_real', 'No especificado').strip()

    hora_actual = hora_ecuador().strftime('%d/%m/%Y %H:%M')
    registro_afectado = None
    regs = db_registros()

    for r in regs:
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
            registro_afectado = r
            break

    guardar_datos()

    if registro_afectado:
        referencia_externa = registro_afectado.get('referencia_externa')
        if registro_afectado.get('origen_socio') == 'fercho':
            disparar_webhook_fercho(registro_afectado, 'RETIRADO', request.host_url)
        else:
            notificar_webhook_socio_desde_registro(registro_afectado, 'completado', referencia_externa=referencia_externa)

    flash('¡Retiro marcado como completado!', 'success')
    return redirect(request.referrer)

@app.route('/marcar_fallido', methods=['POST'])
def marcar_fallido():
    return ejecutar_marcar_fallido()

def ejecutar_marcar_fallido(registro_id=None, motivo=None):
    bloqueo = asegurar_sesion_simulador() if es_entorno_staging() else asegurar_sesion_produccion()
    if bloqueo:
        return bloqueo

    mis_permisos = session.get('permisos', [])
    login_route = login_url_simulador() if es_entorno_staging() else url_for('login')
    if session.get('rol') not in ['supremo', 'cobrador'] and 'procesar_retiros' not in mis_permisos:
        return redirect(login_route)

    if registro_id is None:
        registro_id = int(request.form.get('id'))
    if motivo is None:
        motivo = request.form.get('motivo', 'Sin especificar')

    hora_actual = hora_ecuador().strftime('%d/%m/%Y %H:%M')

    imagenes = request.files.getlist('evidencia_fallo')
    nombres_imagenes = []
    for img in imagenes:
        if img and img.filename != '':
            nombre = secure_filename(f"evidencia_fallo_{hora_ecuador().strftime('%Y%m%d%H%M%S')}_{img.filename}")
            img.save(os.path.join(app.config['UPLOAD_FOLDER'], nombre))
            nombres_imagenes.append(nombre)
    str_imagenes_fallo = ",".join(nombres_imagenes) if nombres_imagenes else None

    usuario_afectado = None
    registro_afectado = None
    regs = db_registros()

    for r in regs:
        if r['id'] == registro_id:
            usuario_afectado = r['usuario']

            if str_imagenes_fallo:
                r['imagen_fallo'] = str_imagenes_fallo

            tiene_deuda_previa = any(reg for reg in regs if reg['usuario'] == usuario_afectado and reg['estado'] in ['fallido', 'expirado'])

            if tiene_deuda_previa:
                r['estado'] = 'fallido_revision'
                r['historial'].append(f"[{hora_actual}] ⚠️ Marcado como NO SALIÓ por {session['usuario'].capitalize()}. Motivo: {motivo}")
                flash(f'El retiro de {usuario_afectado} se envió a REVISIÓN porque el cliente ya tiene deudas previas.', 'error')
            else:
                r['estado'] = 'fallido'
                r['historial'].append(f"[{hora_actual}] ❌ Marcado como NO SALIÓ (Deuda) por {session['usuario'].capitalize()}. Motivo: {motivo}")
                flash(f'⚠️ Retiro de {usuario_afectado} marcado como FALLIDO (Deuda).', 'error')
            registro_afectado = r
            break

    guardar_datos()

    if registro_afectado:
        referencia_externa = registro_afectado.get('referencia_externa')
        if registro_afectado.get('origen_socio') == 'fercho':
            estado_fercho = 'FALLIDO_REVISION' if registro_afectado.get('estado') == 'fallido_revision' else 'FALLIDO'
            disparar_webhook_fercho(registro_afectado, estado_fercho, request.host_url)
        else:
            notificar_webhook_socio_desde_registro(registro_afectado, 'fallido', referencia_externa=referencia_externa)

    return redirect(request.referrer)

@app.route('/gestionar_deuda', methods=['POST'])
def gestionar_deuda():
    mis_permisos = session.get('permisos', [])
    if session.get('rol') not in ['supremo', 'reportes'] and 'ver_reportes' not in mis_permisos: return redirect(url_for('login'))
    
    id_revision = int(request.form.get('id_revision'))
    accion = request.form.get('accion') 
    hora_actual = hora_ecuador().strftime('%d/%m/%Y %H:%M')

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

    hora_actual = hora_ecuador().strftime('%d/%m/%Y %H:%M')
    
    def format_num(val):
        return int(val) if float(val).is_integer() else round(float(val), 2)

    deuda_record = next((r for r in registros if r['id'] == id_deuda), None)
    
    if deuda_record and deuda_record['estado'] in ['fallido', 'expirado']:
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

    hora_actual = hora_ecuador().strftime('%d/%m/%Y %H:%M')

    def format_num(val):
        return int(val) if float(val).is_integer() else round(float(val), 2)

    if str(id_deuda_raw).startswith('total_'):
        usuario_deudor = str(id_deuda_raw).split('total_')[1]
        
        # CANDADO 1: Solo tomamos deudas donde el id_pago sea MAYOR (posterior) al id de la deuda
        deudas_usuario = [r for r in registros if r['usuario'] == usuario_deudor and r['estado'] in ['fallido', 'expirado'] and r['id'] < id_pago]
        
        if not deudas_usuario:
            flash("No hay deudas válidas anteriores a este pago para cruzar.", "error")
            return redirect(url_for('vista_reportes', vista='historial'))
        
        monto_inicial_pago = monto_pago_disp
        for deuda in deudas_usuario:
            if monto_pago_disp <= 0:
                break
            monto_deuda_actual = float(deuda['monto'])
            
            if monto_pago_disp >= monto_deuda_actual:
                monto_pago_disp -= monto_deuda_actual
                deuda['estado'] = 'saldado'
                deuda['historial'].append(f"[{hora_actual}] ✅ Deuda saldada (Abono a Total) usando el pago posterior #{id_pago}.")
            else:
                restante = monto_deuda_actual - monto_pago_disp
                deuda['monto'] = str(format_num(restante))
                deuda['historial'].append(f"[{hora_actual}] ⚠️ Abono parcial de ${format_num(monto_pago_disp)} (Abono a Total) con pago posterior #{id_pago}.")
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
            # CANDADO 2: Verificamos directamente que el pago sea posterior a esta deuda puntual
            if id_pago < id_deuda:
                flash("Error: El pago debe haber ingresado DESPUÉS de la deuda para poder cruzarlo.", "error")
                return redirect(url_for('vista_reportes', vista='historial'))

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
    if session.get('rol') not in ['supremo', 'reportes'] and 'ver_reportes' not in mis_permisos:
        return redirect(url_for('login'))

    registro_id = int(request.form.get('id'))
    vista_origen = request.form.get('vista_origen', 'historial')
    prefix = request.form.get('url_prefix', '')

    regs = db_registros()
    registro_a_borrar = next((r for r in regs if r['id'] == registro_id), None)

    if registro_a_borrar:
        if registro_a_borrar.get('imagen'):
            imagenes = registro_a_borrar['imagen'].split(',')
            for img in imagenes:
                ruta_imagen = os.path.join(app.config['UPLOAD_FOLDER'], img)
                if os.path.exists(ruta_imagen):
                    try: os.remove(ruta_imagen)
                    except: pass
        regs.remove(registro_a_borrar)
        guardar_datos()
        flash('🗑️ Registro eliminado permanentemente.', 'success')
    else:
        flash('Error: registro no encontrado.', 'error')

    if vista_origen == 'papelera':
        return redirect(f"{prefix}/papelera")
    return redirect(f"{prefix}/reportes?vista={vista_origen}")

@app.route('/usuarios')
def lista_usuarios():
    return vista_lista_usuarios(url_prefix='')

def vista_lista_usuarios(url_prefix=''):
    bloqueo = asegurar_sesion_simulador() if url_prefix else asegurar_sesion_produccion()
    if bloqueo:
        return bloqueo
    mis_permisos = session.get('permisos', [])
    login_route = f'{url_prefix}/login' if url_prefix else url_for('login')
    if session.get('rol') != 'supremo' and 'gestionar_usuarios' not in mis_permisos:
        return redirect(login_route)
    users = {**usuarios_pruebas, **cobradores_pruebas} if url_prefix else usuarios_db
    return render_template('usuarios.html', usuarios=users, mi_usuario=session['usuario'], rol=session.get('rol'), url_prefix=url_prefix, entorno_staging=bool(url_prefix))

@app.route('/usuarios/crear', methods=['GET', 'POST'])
def crear_usuario():
    return vista_crear_usuario(url_prefix='')

def vista_crear_usuario(url_prefix=''):
    bloqueo = asegurar_sesion_simulador() if url_prefix else asegurar_sesion_produccion()
    if bloqueo:
        return bloqueo
    mis_permisos = session.get('permisos', [])
    login_route = f'{url_prefix}/login' if url_prefix else url_for('login')
    lista_route = f'{url_prefix}/usuarios' if url_prefix else url_for('lista_usuarios')
    if session.get('rol') != 'supremo' and 'gestionar_usuarios' not in mis_permisos:
        return redirect(login_route)
    if request.method == 'POST':
        username = request.form.get('username').lower()
        permisos_marcados = request.form.getlist('permisos')
        nuevo_usuario = {
            'nombre': request.form.get('nombre', username),
            'apellido': '',
            'email': '',
            'password': request.form.get('password'),
            'rol': request.form.get('rol'),
            'permisos': permisos_marcados,
            'estado': 'Activo',
        }
        if url_prefix:
            if usuario_existe_en_staging(username):
                flash('El nombre de usuario ya existe en el simulador.', 'error')
                return redirect(f'{url_prefix}/usuarios/crear')
            if _es_cobrador_simulador(nuevo_usuario):
                nuevo_usuario['disponible'] = True
            guardar_usuario_en_staging(username, nuevo_usuario)
        else:
            if username in usuarios_db:
                flash('El nombre de usuario ya existe.', 'error')
                return redirect(url_for('crear_usuario'))
            usuarios_db[username] = nuevo_usuario
            guardar_datos()
        flash(f'Usuario {username} creado con éxito como {request.form.get("rol")}.', 'success')
        return redirect(lista_route)
    return render_template('crear_usuario.html', mi_usuario=session['usuario'], rol=session.get('rol'), url_prefix=url_prefix, entorno_staging=bool(url_prefix))

@app.route('/editar_usuario', methods=['POST'])
def editar_usuario():
    return ejecutar_editar_usuario(url_prefix='')

def ejecutar_editar_usuario(url_prefix=''):
    bloqueo = asegurar_sesion_simulador() if url_prefix else asegurar_sesion_produccion()
    if bloqueo:
        return bloqueo
    mis_permisos = session.get('permisos', [])
    login_route = f'{url_prefix}/login' if url_prefix else url_for('login')
    lista_route = f'{url_prefix}/usuarios' if url_prefix else url_for('lista_usuarios')
    if session.get('rol') != 'supremo' and 'gestionar_usuarios' not in mis_permisos:
        return redirect(login_route)

    username = request.form.get('username', '').lower()
    permisos_marcados = request.form.getlist('permisos')

    if url_prefix:
        existente = obtener_usuario_staging(username)
        if not existente:
            flash('Error: Usuario no encontrado en el simulador.', 'error')
            return redirect(lista_route)
        actualizado = copy.deepcopy(existente)
        actualizado['nombre'] = request.form.get('nombre', actualizado.get('nombre'))
        actualizado['email'] = request.form.get('email', actualizado.get('email', ''))
        actualizado['rol'] = request.form.get('rol', actualizado.get('rol'))
        actualizado['estado'] = request.form.get('estado', actualizado.get('estado'))
        actualizado['permisos'] = permisos_marcados
        nueva_pass = request.form.get('password')
        if nueva_pass and nueva_pass.strip():
            actualizado['password'] = nueva_pass
        if _es_cobrador_simulador(actualizado):
            actualizado.setdefault('disponible', True)
        guardar_usuario_en_staging(username, actualizado)
    else:
        if username in usuarios_db:
            usuarios_db[username]['nombre'] = request.form.get('nombre', usuarios_db[username]['nombre'])
            usuarios_db[username]['email'] = request.form.get('email', usuarios_db[username]['email'])
            usuarios_db[username]['rol'] = request.form.get('rol', usuarios_db[username]['rol'])
            usuarios_db[username]['estado'] = request.form.get('estado', usuarios_db[username]['estado'])
            usuarios_db[username]['permisos'] = permisos_marcados
            nueva_pass = request.form.get('password')
            if nueva_pass and nueva_pass.strip() != '':
                usuarios_db[username]['password'] = nueva_pass
            guardar_datos()
        else:
            flash('Error: Usuario no encontrado en la base de datos.', 'error')
            return redirect(lista_route)

    flash(f'✅ Usuario "{username}" actualizado correctamente.', 'success')
    return redirect(lista_route)

@app.route('/eliminar_usuario', methods=['POST'])
def eliminar_usuario():
    return ejecutar_eliminar_usuario(url_prefix='')

def ejecutar_eliminar_usuario(url_prefix=''):
    bloqueo = asegurar_sesion_simulador() if url_prefix else asegurar_sesion_produccion()
    if bloqueo:
        return bloqueo
    mis_permisos = session.get('permisos', [])
    login_route = f'{url_prefix}/login' if url_prefix else url_for('login')
    lista_route = f'{url_prefix}/usuarios' if url_prefix else url_for('lista_usuarios')
    if session.get('rol') != 'supremo' and 'gestionar_usuarios' not in mis_permisos:
        return redirect(login_route)

    username = request.form.get('username', '').lower()

    if username == session.get('usuario'):
        flash('No puedes eliminar tu propia cuenta activa.', 'error')
        return redirect(lista_route)

    if url_prefix:
        if eliminar_usuario_de_staging(username):
            flash(f'🗑️ Usuario "{username}" eliminado del simulador.', 'success')
        else:
            flash('Error: Usuario no encontrado en el simulador.', 'error')
    else:
        if username in usuarios_db:
            del usuarios_db[username]
            guardar_datos()
            flash(f'🗑️ Usuario "{username}" ha sido eliminado permanentemente.', 'success')
        else:
            flash('Error: Usuario no encontrado.', 'error')

    return redirect(lista_route)
@app.route('/marcar_recibido', methods=['POST'])
def marcar_recibido():
    if session.get('rol') not in ['supremo', 'recaudador']: return redirect(url_for('login'))
    
    cobrador = request.form.get('cobrador')
    hora_actual = hora_ecuador().strftime('%d/%m/%Y %H:%M')
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

@app.route('/reportes', endpoint='vista_reportes')
def vista_reportes_produccion():
    return vista_reportes(url_prefix='')

def vista_reportes(url_prefix=''):
    bloqueo = asegurar_sesion_simulador() if url_prefix else asegurar_sesion_produccion()
    if bloqueo:
        return bloqueo

    mis_permisos = session.get('permisos', [])
    login_route = f'{url_prefix}/login' if url_prefix else url_for('login')
    if session.get('rol') != 'supremo' and 'ver_reportes' not in mis_permisos:
        return redirect(login_route)

    regs = db_registros()
    users = db_usuarios()

    lista_clientes = sorted(list(set(r['usuario'] for r in regs if r.get('usuario'))))
    lista_estados = sorted(list(set(r['estado'] for r in regs if r.get('estado'))))
    lista_sucursales = sorted(list(set(r['banco'] for r in regs if r.get('banco'))))
    
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
        for r in regs:
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

        for r in regs:
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
    exitosos = [r for r in regs if r['estado'] == 'retirado' and pasa_filtros_basicos(r)]
    no_exitosos_raw = [r for r in regs if r['estado'] in ['expirado', 'fallido', 'saldado', 'fallido_revision', 'fusionado'] and pasa_filtros_basicos(r)]
    
    deudas_agrupadas = {}
    for r in no_exitosos_raw:
        user = r['usuario']
        if user not in deudas_agrupadas: deudas_agrupadas[user] = []
        deudas_agrupadas[user].append(r)
        
    cobradores_activos = [u for u, info in users.items() if info['rol'] == 'cobrador' or 'procesar_retiros' in info.get('permisos', [])]
    cobradores_mostrar = [filtro_cobrador] if filtro_cobrador in cobradores_activos else cobradores_activos
    stats_cobradores = {}
    for c in cobradores_mostrar:
        stats_cobradores[c] = {'exitosos': [], 'fallidos': [], 'expirados': []}

    registros_tabla_dinamica = [] 
    
    for r in regs:
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
                           mi_usuario=session['usuario'],
                           url_prefix=url_prefix,
                           entorno_staging=bool(url_prefix))

@app.route('/trabajador/<nombre>')
def vista_trabajador(nombre):
    return render_vista_trabajador(nombre, url_prefix='')

def render_vista_trabajador(nombre, url_prefix=''):
    bloqueo = asegurar_sesion_simulador() if url_prefix else asegurar_sesion_produccion()
    if bloqueo:
        return bloqueo

    mis_permisos = session.get('permisos', [])
    login_route = f'{url_prefix}/login' if url_prefix else url_for('login')
    if session.get('rol') not in ['supremo', 'cobrador'] and 'procesar_retiros' not in mis_permisos: 
        return redirect(login_route)
        
    nombre = nombre.lower()
    
    if session.get('rol') == 'cobrador' and session.get('usuario') != nombre:
        flash('Tu rol no te permite entrar a la bandeja de otros cobradores.', 'error')
        return redirect(ruta_por_rol(session.get('rol'), session.get('usuario')))
        
    regs = db_registros()
    # 1. Códigos Pendientes
    mis_activos = [r for r in regs if r.get('asignado_a') == nombre and r['estado'] in ['activo', 'expirado']]
    
    # 2. Historial de retiros completados (Agrupados por fecha)
    historial_agrupado = {}
    for r in regs:
        if r.get('asignado_a') == nombre and r['estado'] in ['retirado', 'liquidado', 'saldado']:
            fecha_corta = r.get('fecha', '').split(' ')[0] if r.get('fecha') else 'Sin fecha'
            if fecha_corta not in historial_agrupado:
                historial_agrupado[fecha_corta] = {'total': 0.0, 'registros': []}
            
            historial_agrupado[fecha_corta]['registros'].append(r)
            try:
                historial_agrupado[fecha_corta]['total'] += float(r.get('monto', 0))
            except: pass

    # Ordenar por fecha (más reciente primero)
    try:
        historial_ordenado = dict(sorted(historial_agrupado.items(), key=lambda item: datetime.strptime(item[0], "%d/%m/%Y") if item[0] != 'Sin fecha' else datetime.min, reverse=True))
    except:
        historial_ordenado = historial_agrupado

    mi_estado_disp = db_usuarios().get(nombre, {}).get('disponible', True) if nombre in db_usuarios() else True

    return render_template('trabajador.html', 
                           registros=mis_activos, 
                           historial_agrupado=historial_ordenado,
                           nombre=nombre.capitalize(), 
                           mi_usuario=session['usuario'],
                           mi_estado_disp=mi_estado_disp,
                           rol=session.get('rol'),
                           url_prefix=url_prefix,
                           entorno_staging=bool(url_prefix))
@app.route('/notificar_visto', methods=['POST'])
def notificar_visto():
    return ejecutar_notificar_visto()

def ejecutar_notificar_visto():
    bloqueo = asegurar_sesion_simulador() if es_entorno_staging() else asegurar_sesion_produccion()
    if bloqueo:
        return bloqueo

    if session.get('rol') not in ['supremo', 'cobrador'] and 'procesar_retiros' not in session.get('permisos', []): 
        return jsonify({"error": "No autorizado"}), 403

    data = request.json
    registro_id = data.get('id')
    cobrador_nombre = session.get('usuario').capitalize()
    
    banco = "Desconocido"
    regs = db_registros()
    
    for r in regs:
        if r['id'] == int(registro_id):
            banco = r.get('banco', 'Desconocido').capitalize()
            r['visto_por_cobrador'] = True
            break
            
    guardar_datos()

    if not es_entorno_staging():
        titulo = "👀 Código Visto"
        mensaje = f"El cobrador {cobrador_nombre} ha recibido y visto el código de {banco}."
        admin_users = [u for u, info in db_usuarios().items() if info['rol'] in ['supremo', 'recaudador']]
        for admin in admin_users:
            disparar_alerta_push(admin, titulo, mensaje)

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
        usuario = session['usuario']
        nueva_sub = request.json
        
        # 1. Si no existe o es un dato viejo, lo convertimos a lista para soportar PC + Celular
        if usuario not in suscripciones_push or isinstance(suscripciones_push.get(usuario), dict):
            suscripciones_push[usuario] = []
            
        # 2. Filtramos para no tener dispositivos repetidos exactos
        suscripciones_push[usuario] = [s for s in suscripciones_push[usuario] if s.get('endpoint') != nueva_sub.get('endpoint')]
        
        # 3. Agregamos el nuevo dispositivo
        suscripciones_push[usuario].append(nueva_sub)
        guardar_datos() 
        
    return jsonify({"status": "ok"})

def extraer_nombre_cliente_widget(usuario):
    prefijo = 'WIDGET - '
    if usuario and str(usuario).startswith(prefijo):
        return str(usuario)[len(prefijo):].strip()
    return None

def extraer_nombre_cliente_alex(usuario):
    u = str(usuario or '').strip()
    prefijo_prueba = '🔴 [PRUEBA] '
    if u.startswith(prefijo_prueba):
        u = u[len(prefijo_prueba):]
        if ' - ' in u:
            return u.split(' - ', 1)[1].strip()
    prefijo = 'ALEX - '
    if u.startswith(prefijo):
        return u[len(prefijo):].strip()
    return None

def es_prueba_desde_registro(registro_afectado):
    if es_entorno_staging() or registro_afectado.get('entorno_staging'):
        return True
    primera_linea_historial = registro_afectado.get('historial', [''])[0]
    return "PRUEBA" in primera_linea_historial or registro_afectado.get('es_prueba', False)

def mapear_moneda_desde_banco(banco_str):
    if banco_str in ['bcp', 'interbank', 'bbva', 'scotiabank']:
        return 'PEN'
    if banco_str in ['bnb', 'bisa', 'union', 'banco union']:
        return 'BOB'
    if 'chile' in banco_str or banco_str in ['estado', 'falabella']:
        return 'CLP'
    if 'binance' in banco_str or 'cripto' in banco_str:
        return 'USDT'
    return 'USD'

def notificar_webhook_socio_desde_registro(registro_afectado, estado, referencia_externa=None):
    ref = referencia_externa if referencia_externa is not None else registro_afectado.get('referencia_externa')
    es_prueba = es_prueba_desde_registro(registro_afectado)

    # Extraer banco y mapear moneda para CxC del socio
    banco_str = registro_afectado.get('banco', '').lower().strip()
    moneda_calculada = mapear_moneda_desde_banco(banco_str)

    if registro_afectado.get('origen_socio') == 'alex':
        cliente = extraer_nombre_cliente_alex(registro_afectado.get('usuario', '')) or registro_afectado.get('usuario', 'Widget-Externo')
        disparar_webhook_socio(
            cliente=cliente,
            estado=estado,
            monto=registro_afectado.get('monto'),
            referencia_externa=ref,
            es_prueba=es_prueba,
            banco=banco_str,
            moneda=moneda_calculada,
        )
        return

    nombre_cliente = extraer_nombre_cliente_widget(registro_afectado.get('usuario', ''))
    if nombre_cliente is None:
        nombre_cliente = extraer_nombre_cliente_alex(registro_afectado.get('usuario', ''))
    if nombre_cliente is None and (es_prueba or registro_afectado.get('origen_socio') == 'alex'):
        nombre_cliente = registro_afectado.get('usuario', 'Widget-Externo')
    if nombre_cliente is not None:
        disparar_webhook_socio(
            cliente=nombre_cliente,
            estado=estado,
            monto=registro_afectado.get('monto'),
            referencia_externa=ref,
            es_prueba=es_prueba,
            banco=banco_str,
            moneda=moneda_calculada,
        )
    elif es_prueba:
        disparar_webhook_socio(
            cliente=registro_afectado.get('usuario', 'Widget-Externo'),
            estado=estado,
            monto=registro_afectado.get('monto'),
            referencia_externa=ref,
            es_prueba=True,
            banco=banco_str,
            moneda=moneda_calculada,
        )

def disparar_webhook_fercho(registro, estado_final, host_url, evidencia_url=None):
    """Notifica a Fercho (contrato estricto) en un hilo aparte."""
    external_id = registro.get('referencia_externa')
    if not external_id:
        print('⚠️ Webhook Fercho omitido: registro sin referencia_externa')
        return

    payload = {
        'external_id': external_id,
        'estado_final': estado_final,
    }

    if estado_final in ('FALLIDO', 'FALLIDO_REVISION'):
        if evidencia_url:
            payload['evidencia'] = evidencia_url
        elif registro.get('imagen_fallo'):
            primera_imagen = registro['imagen_fallo'].split(',')[0].strip()
            payload['evidencia'] = host_url.rstrip('/') + url_for('ver_imagen', filename=primera_imagen)

    headers = {}
    if FERCHO_WEBHOOK_KEY:
        headers['X-WEBHOOK-KEY'] = FERCHO_WEBHOOK_KEY

    payload_copia = dict(payload)
    headers_copia = dict(headers)

    def enviar_en_hilo():
        try:
            with httpx.Client(timeout=10.0) as client:
                response = client.post(FERCHO_WEBHOOK_URL, json=payload_copia, headers=headers_copia)
                print(f"✅ Webhook Fercho ({estado_final}) → {external_id}: HTTP {response.status_code}")
        except Exception as ex:
            print(f"❌ Error webhook Fercho ({estado_final}) → {external_id}:", repr(ex))

    threading.Thread(target=enviar_en_hilo, daemon=True).start()

def disparar_webhook_socio(cliente, estado, monto, referencia_externa=None, es_prueba=False, banco=None, moneda=None):
    """Envía la notificación automática al ERP del socio con el rastreador asignado."""
    webhook_url = os.environ.get('WEBHOOK_SOCIO_URL') or WEBHOOK_SOCIO_URL
    if not webhook_url:
        print("⚠️ WEBHOOK_SOCIO_URL no configurada en las variables de entorno.")
        return False

    if es_entorno_staging():
        es_prueba = True

    payload = {
        "cliente": cliente,
        "estado": estado,
        "monto": monto,
        "referencia_externa": referencia_externa,
        "es_prueba": es_prueba,
        "banco": banco,
        "moneda": moneda,
    }
    headers = {"X-API-Key": WEBHOOK_SOCIO_API_KEY}

    def enviar_en_hilo():
        try:
            response = requests.post(webhook_url, json=payload, headers=headers, timeout=10)
            print(f"📡 Webhook enviado. Status: {response.status_code}")
        except Exception as e:
            print(f"❌ Error al disparar el webhook: {str(e)}")

    threading.Thread(target=enviar_en_hilo, daemon=True).start()
    return True

def disparar_alerta_push(usuario_destino, titulo, mensaje):
    """Dispara la notificación push en un hilo secundario para no bloquear Flask."""
    subs = suscripciones_push.get(usuario_destino)
    if not subs:
        print(f"No hay suscripción guardada para {usuario_destino}")
        return

    if isinstance(subs, dict):
        subs = [subs]

    if not VAPID_PRIVATE_KEY:
        print("⚠️ No hay VAPID_PRIVATE_KEY configurada. Omitiendo push.")
        return

    payload = json.dumps({
        "title": titulo,
        "body": mensaje,
        "titulo": titulo,
        "mensaje": mensaje,
        "icon": "/static/flujo-notificacion.png",
        "url": "/"
    })

    subs_copia = list(subs)

    def enviar_en_hilo():
        for sub in subs_copia:
            try:
                webpush(
                    subscription_info=sub,
                    data=payload,
                    vapid_private_key=VAPID_PRIVATE_KEY,
                    vapid_claims=VAPID_CLAIMS,
                    ttl=28800,
                    headers={"Urgency": "high"}
                )
                print(f"✅ Push enviado con éxito a un dispositivo de {usuario_destino}")
            except WebPushException as ex:
                status = getattr(getattr(ex, 'response', None), 'status_code', None)
                print(f"❌ WebPushException a {usuario_destino} (status {status}):", repr(ex))
                if status in (404, 410):
                    endpoint = sub.get('endpoint')
                    if usuario_destino in suscripciones_push:
                        suscripciones_push[usuario_destino] = [
                            s for s in suscripciones_push[usuario_destino]
                            if s.get('endpoint') != endpoint
                        ]
                        guardar_datos()
                        print(f"🗑️ Suscripción inválida eliminada para {usuario_destino}")
            except Exception as ex:
                print(f"❌ Error enviando push a {usuario_destino}:", repr(ex))

    threading.Thread(target=enviar_en_hilo, daemon=True).start()
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
    rol = session.get('rol')
    mis_permisos = session.get('permisos', [])
    
    # Validación básica para acceder a la función
    if rol not in ['supremo', 'reportes'] and 'ver_reportes' not in mis_permisos: 
        return redirect(url_for('login'))
        
    registro_id = int(request.form.get('id'))
    hora_actual = hora_ecuador().strftime('%d/%m/%Y %H:%M')
    
    for r in registros:
        if r['id'] == registro_id:
            
            # 🛑 DOBLE CANDADO DE SEGURIDAD: Si es deuda, SOLO SUPREMO puede recuperarlo
            if r['estado'] in ['fallido', 'fallido_revision'] and rol != 'supremo':
                flash('❌ Acción denegada: Solo el rango Supremo puede recuperar códigos caídos.', 'error')
                return redirect(request.referrer)

            # Si pasa la seguridad, recuperamos el código
            if r['estado'] in ['expirado', 'fallido', 'fallido_revision']:
                r['estado'] = 'activo'
                r['asignado_a'] = None
                r['asignacion_estado'] = 'no_asignado'
                r['visto_por_cobrador'] = False
                
                # Le damos 2.5 horas extra de vida desde este momento
                r['expira_timestamp'] = time.time() + (2.5 * 3600)
                
                r['historial'].append(f"[{hora_actual}] ♻️ Recuperado a Retiros Activos por {session['usuario'].capitalize()}.")
                break
                
    guardar_datos()
    flash('✅ Código recuperado. Ha regresado a Retiros Activos sin asignación.', 'success')
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
# --- AÑADIR ESTA NUEVA RUTA EN APP.PY ---
@app.route('/toggle_disponibilidad', methods=['POST'])
def toggle_disponibilidad():
    mis_permisos = session.get('permisos', [])
    if session.get('rol') not in ['supremo', 'cobrador'] and 'procesar_retiros' not in mis_permisos: 
        return jsonify({"error": "No autorizado"}), 403
        
    usuario = session.get('usuario')
    if usuario in usuarios_db:
        # Si no existe la clave, por defecto asumimos que estaba disponible (True)
        estado_actual = usuarios_db[usuario].get('disponible', True)
        usuarios_db[usuario]['disponible'] = not estado_actual
        guardar_datos()
        
        nuevo_estado = "🟢 Disponible" if usuarios_db[usuario]['disponible'] else "🔴 No Disponible"
        return jsonify({"status": "ok", "estado": usuarios_db[usuario]['disponible'], "mensaje": f"Estado cambiado a: {nuevo_estado}"})
    
    return jsonify({"error": "Usuario no encontrado"}), 404

# ==========================================
# 🧪 SIMULADOR CERRADO (/pruebas)
# ==========================================
pruebas_bp = Blueprint('pruebas', __name__, url_prefix='/pruebas')

@pruebas_bp.before_request
def sembrar_simulador_si_vacio():
    asegurar_datos_simulador()

@pruebas_bp.route('/login', methods=['GET', 'POST'])
def login_pruebas():
    return vista_login(url_prefix='/pruebas')

@pruebas_bp.route('/logout')
def logout_pruebas():
    session.clear()
    return redirect('/pruebas/login')

@pruebas_bp.route('/admin')
def admin_pruebas():
    return vista_admin(url_prefix='/pruebas')

@pruebas_bp.route('/asignar', methods=['POST'])
def asignar_pruebas():
    return ejecutar_asignar(url_prefix='/pruebas')

@pruebas_bp.route('/trabajador/<nombre>')
def trabajador_pruebas(nombre):
    return render_vista_trabajador(nombre, url_prefix='/pruebas')

@pruebas_bp.route('/sincronizar_usuarios')
def sincronizar_usuarios_pruebas():
    if not usuarios_db:
        flash('⚠️ No hay usuarios de producción para copiar.', 'error')
        return redirect('/pruebas/login')
    sincronizar_usuarios_desde_produccion()
    flash('✅ Sincronización exitosa. Ya puedes entrar con tu contraseña real.', 'success')
    return redirect('/pruebas/login')

@pruebas_bp.route('/usuarios')
def usuarios_pruebas_view():
    return vista_lista_usuarios(url_prefix='/pruebas')

@pruebas_bp.route('/usuarios/crear', methods=['GET', 'POST'])
def crear_usuario_pruebas():
    return vista_crear_usuario(url_prefix='/pruebas')

@pruebas_bp.route('/editar_usuario', methods=['POST'])
def editar_usuario_pruebas():
    return ejecutar_editar_usuario(url_prefix='/pruebas')

@pruebas_bp.route('/eliminar_usuario', methods=['POST'])
def eliminar_usuario_pruebas():
    return ejecutar_eliminar_usuario(url_prefix='/pruebas')

@pruebas_bp.route('/reportes')
def reportes_pruebas():
    return vista_reportes(url_prefix='/pruebas')

@pruebas_bp.route('/papelera')
def papelera_pruebas():
    return render_vista_papelera(url_prefix='/pruebas')

@pruebas_bp.route('/grupos')
def grupos_pruebas():
    bloqueo = asegurar_sesion_simulador()
    if bloqueo:
        return bloqueo
    flash('El módulo de grupos no está disponible en el simulador. Usa Retiros Activos o Reportes.', 'error')
    return redirect('/pruebas/admin')

@pruebas_bp.route('/eliminar_registro', methods=['POST'])
def eliminar_registro_pruebas():
    return eliminar_registro()

@pruebas_bp.route('/mover_papelera', methods=['POST'])
def mover_papelera_pruebas():
    return mover_papelera()

@pruebas_bp.route('/marcar_retirado', methods=['POST'])
def marcar_retirado_pruebas():
    return ejecutar_marcar_retirado()

@pruebas_bp.route('/marcar_fallido', methods=['POST'])
def marcar_fallido_pruebas():
    return ejecutar_marcar_fallido()

@pruebas_bp.route('/notificar_visto', methods=['POST'])
def notificar_visto_pruebas():
    return ejecutar_notificar_visto()

app.register_blueprint(pruebas_bp)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)