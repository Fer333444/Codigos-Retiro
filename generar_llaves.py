import base64
from cryptography.hazmat.primitives.asymmetric import ec

def encode_vapid_key(value):
    """Codifica la llave en el formato Base64 URL Safe que exigen los navegadores."""
    return base64.urlsafe_b64encode(value).replace(b'=', b'').decode('utf-8')

def generar_claves():
    # 1. Generar la llave criptográfica en la curva elíptica correcta (prime256v1)
    private_key = ec.generate_private_key(ec.SECP256R1())
    private_numbers = private_key.private_numbers()

    # 2. Extraer la llave pública correspondiente
    public_key = private_key.public_key()
    public_numbers = public_key.public_numbers()

    # 3. Convertir los números a los bytes crudos que pide el estándar Web Push
    priv_bytes = private_numbers.private_value.to_bytes(32, 'big')
    pub_bytes = b'\x04' + public_numbers.x.to_bytes(32, 'big') + public_numbers.y.to_bytes(32, 'big')

    # 4. Mostrar los resultados
    print("\n=======================================================")
    print("🔑 TUS CLAVES VAPID GENERADAS CON ÉXITO 🔑")
    print("=======================================================\n")
    
    print("🔴 LLAVE PRIVADA (VAPID_PRIVATE_KEY)")
    print("¡Mantenla en secreto! Esta va SOLO en tu archivo app.py:")
    print("-" * 50)
    print(encode_vapid_key(priv_bytes))
    print("-" * 50)
    
    print("\n🟢 LLAVE PÚBLICA (VAPID_PUBLIC_KEY)")
    print("Esta llave va en tu frontend (HTML/JS) para pedir permiso al navegador:")
    print("-" * 50)
    print(encode_vapid_key(pub_bytes))
    print("-" * 50 + "\n")

if __name__ == "__main__":
    generar_claves()