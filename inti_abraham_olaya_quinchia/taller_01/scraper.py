import os
import re
import json
import sqlite3
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timezone, timedelta

# Configuración de Zona Horaria (Colombia UTC-5)
COLOMBIA_TZ = timezone(timedelta(hours=-5))

RAW_DIR = "raw"
DB_PATH = "comparador_precios.sqlite"

def obtener_timestamp_iso():
    """Retorna la fecha y hora actual en formato ISO 8601 con zona horaria de Colombia."""
    return datetime.now(COLOMBIA_TZ).isoformat()

def asegurar_directorio_raw():
    """Crea la carpeta raw/ si no existe."""
    if not os.path.exists(RAW_DIR):
        os.makedirs(RAW_DIR)

# ==========================================
# 1. BASE DE DATOS SQLITE
# ==========================================

def inicializar_db(db_path=DB_PATH):
    """Crea las tablas necesarias en SQLite si no existen."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Tabla de ejecuciones (corridas del scraper)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS ejecuciones (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fecha_hora TEXT NOT NULL,
        tipo TEXT NOT NULL, -- 'ONLINE' u 'OFFLINE'
        estado TEXT NOT NULL -- 'INICIADO', 'COMPLETADO', 'ERROR'
    )
    """)
    
    # Tabla de productos (catálogo general normalizado)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS productos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        marca TEXT,
        nombre_normalizado TEXT NOT NULL,
        subcategoria TEXT
    )
    """)
    
    # Tabla de anuncios por comercio
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS anuncios (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        comercio TEXT NOT NULL,
        sku_comercio TEXT,
        url TEXT NOT NULL,
        texto_original TEXT NOT NULL,
        producto_id INTEGER,
        FOREIGN KEY (producto_id) REFERENCES productos (id)
    )
    """)
    
    # Tabla de observaciones (capturas puntuales de precio)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS observaciones (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        anuncio_id INTEGER NOT NULL,
        ejecucion_id INTEGER NOT NULL,
        precio_cop REAL NOT NULL,
        en_promocion INTEGER NOT NULL, -- 1 = Sí, 0 = No
        precio_regular_cop REAL,
        fecha_consulta TEXT NOT NULL,
        FOREIGN KEY (anuncio_id) REFERENCES anuncios (id),
        FOREIGN KEY (ejecucion_id) REFERENCES ejecuciones (id)
    )
    """)
    
    conn.commit()
    conn.close()

def registrar_ejecucion(tipo="ONLINE", db_path=DB_PATH):
    """Registra una nueva ejecución y retorna su ID."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    fecha_iso = obtener_timestamp_iso()
    cursor.execute("INSERT INTO ejecuciones (fecha_hora, tipo, estado) VALUES (?, ?, ?)",
                   (fecha_iso, tipo, "INICIADO"))
    ejecucion_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return ejecucion_id

def finalizar_ejecucion(ejecucion_id, estado="COMPLETADO", db_path=DB_PATH):
    """Actualiza el estado de la ejecución."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("UPDATE ejecuciones SET estado = ? WHERE id = ?", (estado, ejecucion_id))
    conn.commit()
    conn.close()

def guardar_observaciones(datos_productos, ejecucion_id, db_path=DB_PATH):
    """Inserta o actualiza anuncios y guarda las observaciones capturadas."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    for item in datos_productos:
        # 1. Buscar o insertar anuncio
        cursor.execute("SELECT id FROM anuncios WHERE comercio = ? AND url = ?", 
                       (item["comercio"], item["url"]))
        anuncio = cursor.fetchone()
        
        if anuncio:
            anuncio_id = anuncio[0]
        else:
            cursor.execute("""
                INSERT INTO anuncios (comercio, sku_comercio, url, texto_original)
                VALUES (?, ?, ?, ?)
            """, (item["comercio"], item.get("sku"), item["url"], item["texto_original"]))
            anuncio_id = cursor.lastrowid
            
        # 2. Insertar observación
        cursor.execute("""
            INSERT INTO observaciones (anuncio_id, ejecucion_id, precio_cop, en_promocion, precio_regular_cop, fecha_consulta)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            anuncio_id,
            ejecucion_id,
            item["precio_cop"],
            1 if item["en_promocion"] else 0,
            item.get("precio_regular_cop"),
            item["fecha_consulta"]
        ))
        
    conn.commit()
    conn.close()


# ==========================================
# 2. FUNCIONES DE SCRAPING POR COMERCIO
# ==========================================

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "es-CO,es;q=0.9"
}

def scrape_farmatodo(modo_offline=False, ejecucion_id=None):
    """
    Extracción de productos de 'Afeitado y Depilación' en Farmatodo.
    Guarda / lee respaldo local en raw/ para modo offline.
    """
    comercio = "Farmatodo"
    asegurar_directorio_raw()
    archivo_raw = os.path.join(RAW_DIR, "farmatodo_afeitado.json")
    
    contenido_json = None
    
    if modo_offline:
        if os.path.exists(archivo_raw):
            with open(archivo_raw, "r", encoding="utf-8") as f:
                contenido_json = json.load(f)
        else:
            print(f"[{comercio}] Archivo sin conexión {archivo_raw} no encontrado.")
            return []
    else:
        url_api = "https://www.farmatodo.com.co/api/v1/products/category/afeitado-y-depilacion"
        try:
            res = requests.get(url_api, headers=HEADERS, timeout=10)
            if res.status_code == 200:
                contenido_json = res.json()
                with open(archivo_raw, "w", encoding="utf-8") as f:
                    json.dump(contenido_json, f, ensure_ascii=False, indent=2)
            else:
                if os.path.exists(archivo_raw):
                    with open(archivo_raw, "r", encoding="utf-8") as f:
                        contenido_json = json.load(f)
        except Exception as e:
            print(f"[{comercio}] Error en conexión online: {e}. Usando respaldo en raw/...")
            if os.path.exists(archivo_raw):
                with open(archivo_raw, "r", encoding="utf-8") as f:
                    contenido_json = json.load(f)

    productos = []
    fecha_consulta = obtener_timestamp_iso()
    
    if contenido_json and "items" in contenido_json:
        for p in contenido_json["items"]:
            texto_raw = f"{p.get('title', '')} {p.get('description', '')}"
            productos.append({
                "comercio": comercio,
                "nombre_producto": p.get("title"),
                "marca": p.get("brand", "Sin Marca"),
                "subcategoria": p.get("subcategory", "Afeitado"),
                "precio_cop": float(p.get("price", 0)),
                "en_promocion": p.get("is_discount", False),
                "precio_regular_cop": float(p.get("original_price", p.get("price", 0))),
                "url": f"https://www.farmatodo.com.co/producto/{p.get('id')}",
                "sku": str(p.get("id")),
                "texto_original": texto_raw,
                "fecha_consulta": fecha_consulta,
                "ejecucion_id": ejecucion_id
            })
            
    return productos


def scrape_cruzverde(modo_offline=False, ejecucion_id=None):
    """
    Extracción de productos de 'Afeitado y Depilación' en Cruz Verde.
    """
    comercio = "Cruz Verde"
    asegurar_directorio_raw()
    archivo_raw = os.path.join(RAW_DIR, "cruzverde_afeitado.html")
    
    html_content = ""
    
    if modo_offline:
        if os.path.exists(archivo_raw):
            with open(archivo_raw, "r", encoding="utf-8") as f:
                html_content = f.read()
        else:
            print(f"[{comercio}] Archivo sin conexión {archivo_raw} no encontrado.")
            return []
    else:
        url = "https://www.cruzverde.com.co/cuidado-personal/afeitado-y-depilacion/"
        try:
            res = requests.get(url, headers=HEADERS, timeout=10)
            if res.status_code == 200:
                html_content = res.text
                with open(archivo_raw, "w", encoding="utf-8") as f:
                    f.write(html_content)
            else:
                if os.path.exists(archivo_raw):
                    with open(archivo_raw, "r", encoding="utf-8") as f:
                        html_content = f.read()
        except Exception as e:
            print(f"[{comercio}] Error online: {e}. Usando respaldo en raw/...")
            if os.path.exists(archivo_raw):
                with open(archivo_raw, "r", encoding="utf-8") as f:
                    html_content = f.read()

    productos = []
    fecha_consulta = obtener_timestamp_iso()
    
    if html_content:
        soup = BeautifulSoup(html_content, "html.parser")
        items = soup.find_all("div", class_="product-tile")
        
        for item in items:
            nombre_elem = item.find("a", class_="link")
            precio_elem = item.find("span", class_="value")
            
            if nombre_elem and precio_elem:
                nombre = nombre_elem.get_text(strip=True)
                href = nombre_elem.get("href", "")
                url_prod = f"https://www.cruzverde.com.co{href}" if href.startswith("/") else href
                
                texto_raw = item.get_text(separator=" ", strip=True)
                precio_clean = re.sub(r"[^\d]", "", precio_elem.get_text())
                precio_cop = float(precio_clean) if precio_clean else 0.0
                
                productos.append({
                    "comercio": comercio,
                    "nombre_producto": nombre,
                    "marca": "Gillette",
                    "subcategoria": "Afeitado",
                    "precio_cop": precio_cop,
                    "en_promocion": False,
                    "precio_regular_cop": precio_cop,
                    "url": url_prod,
                    "sku": item.get("data-item-id", ""),
                    "texto_original": texto_raw,
                    "fecha_consulta": fecha_consulta,
                    "ejecucion_id": ejecucion_id
                })
                
    return productos


def scrape_exito(modo_offline=False, ejecucion_id=None):
    """
    Extracción de productos de 'Afeitado y Depilación' en Éxito.
    """
    comercio = "Éxito"
    asegurar_directorio_raw()
    archivo_raw = os.path.join(RAW_DIR, "exito_afeitado.json")
    
    contenido_json = None
    
    if modo_offline:
        if os.path.exists(archivo_raw):
            with open(archivo_raw, "r", encoding="utf-8") as f:
                contenido_json = json.load(f)
        else:
            print(f"[{comercio}] Archivo sin conexión {archivo_raw} no encontrado.")
            return []
    else:
        url_api = "https://www.exito.com/api/catalog_system/pub/products/search/salud-y-belleza/afeitado-y-depilacion"
        try:
            res = requests.get(url_api, headers=HEADERS, timeout=10)
            if res.status_code == 200:
                contenido_json = res.json()
                with open(archivo_raw, "w", encoding="utf-8") as f:
                    json.dump(contenido_json, f, ensure_ascii=False, indent=2)
            else:
                if os.path.exists(archivo_raw):
                    with open(archivo_raw, "r", encoding="utf-8") as f:
                        contenido_json = json.load(f)
        except Exception as e:
            print(f"[{comercio}] Error online: {e}. Usando respaldo en raw/...")
            if os.path.exists(archivo_raw):
                with open(archivo_raw, "r", encoding="utf-8") as f:
                    contenido_json = json.load(f)

    productos = []
    fecha_consulta = obtener_timestamp_iso()
    
    if contenido_json and isinstance(contenido_json, list):
        for p in contenido_json:
            items = p.get("items", [{}])[0]
            sellers = items.get("sellers", [{}])[0]
            comm_offer = sellers.get("commertialOffer", {})
            
            precio_cop = float(comm_offer.get("Price", 0))
            precio_list = float(comm_offer.get("ListPrice", precio_cop))
            
            texto_raw = f"{p.get('productName', '')} {p.get('description', '')}"
            
            productos.append({
                "comercio": comercio,
                "nombre_producto": p.get("productName"),
                "marca": p.get("brand", "Sin Marca"),
                "subcategoria": "Afeitado",
                "precio_cop": precio_cop,
                "en_promocion": precio_cop < precio_list,
                "precio_regular_cop": precio_list,
                "url": p.get("link", ""),
                "sku": p.get("productId", ""),
                "texto_original": texto_raw,
                "fecha_consulta": fecha_consulta,
                "ejecucion_id": ejecucion_id
            })
            
    return productos


# ==========================================
# 3. FUNCIÓN ORQUESTADORA PRINCIPAL
# ==========================================

def ejecutar_recoleccion(modo_offline=False, db_path=DB_PATH):
    """
    Ejecuta el proceso completo de recolección de los 3 comercios,
    registra la ejecución en SQLite y guarda las observaciones.
    """
    print(f"=== INICIANDO RECOLECCIÓN EN MODO {'OFFLINE' if modo_offline else 'ONLINE'} ===")
    
    # 1. Inicializar base de datos
    inicializar_db(db_path)
    
    # 2. Registrar ejecución
    tipo_str = "OFFLINE" if modo_offline else "ONLINE"
    ejecucion_id = registrar_ejecucion(tipo=tipo_str, db_path=db_path)
    print(f"Ejecución registrada con ID: {ejecucion_id}")
    
    todos_los_productos = []
    
    try:
        # 3. Ejecutar scrapers por comercio
        p_farmatodo = scrape_farmatodo(modo_offline=modo_offline, ejecucion_id=ejecucion_id)
        p_cruzverde = scrape_cruzverde(modo_offline=modo_offline, ejecucion_id=ejecucion_id)
        p_exito = scrape_exito(modo_offline=modo_offline, ejecucion_id=ejecucion_id)
        
        todos_los_productos = p_farmatodo + p_cruzverde + p_exito
        
        print(f"Resultados obtenidos:")
        print(f" - Farmatodo: {len(p_farmatodo)} productos")
        print(f" - Cruz Verde: {len(p_cruzverde)} productos")
        print(f" - Éxito: {len(p_exito)} productos")
        print(f" Total capturado: {len(todos_los_productos)} productos")
        
        # 4. Guardar en SQLite
        if todos_los_productos:
            guardar_observaciones(todos_los_productos, ejecucion_id, db_path=db_path)
            print("Observaciones guardadas con éxito en SQLite.")
            
        finalizar_ejecucion(ejecucion_id, estado="COMPLETADO", db_path=db_path)
        print("=== RECOLECCIÓN FINALIZADA EXITOSAMENTE ===")
        
    except Exception as e:
        print(f"Error durante la ejecución: {e}")
        finalizar_ejecucion(ejecucion_id, estado="ERROR", db_path=db_path)
        
    return todos_los_productos


if __name__ == "__main__":
    # Prueba de ejecución offline
    ejecutar_recoleccion(modo_offline=True)
