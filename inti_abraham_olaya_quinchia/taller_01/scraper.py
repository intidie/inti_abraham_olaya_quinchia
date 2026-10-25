"""
scraper.py - Comparador de precios: categoría "Afeitado y depilación" (Colombia).

Estrategia: los tres comercios usan VTEX. Se consultan los servicios JSON que el
propio sitio usa para pintar su catálogo (sin navegador):
  1. /api/catalog_system/pub/category/tree/3   -> árbol de categorías
  2. /api/catalog_system/pub/products/search   -> productos (paginado con _from/_to)
El encabezado de respuesta `resources: 0-49/230` informa el total del sitio y
permite verificar que la recolección está completa.

Uso:
    python scraper.py                       # online
    python scraper.py --modo offline        # reprocesa raw/ sin red
    python scraper.py --modo offline --fecha 2026-10-24
"""

import argparse
import csv
import glob
from itertools import combinations
import gzip
import json
import os
import re
import sqlite3
import time
import unicodedata
from datetime import datetime, timedelta, timezone

import requests

TZ = timezone(timedelta(hours=-5))  # hora de Colombia
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RAW_DIR = os.path.join(BASE_DIR, "raw")
DB_PATH = os.path.join(BASE_DIR, "comparador_precios.sqlite")
UA = "Mozilla/5.0 (proyecto academico UNAL - Mineria de Datos; contacto: abrahamolayaquinchia@gmail.com)"
PAUSA_S = 1.0       # pausa entre peticiones (buenas prácticas)
PAGINA = 50         # VTEX permite máx. 50 productos por petición
MAX_OFFSET = 2500   # VTEX no pagina más allá de 2500 resultados
SCHEMA_VERSION = 2

# Los comercios son configurables: para cambiar uno basta editar este diccionario.
COMERCIOS = {
    "La Rebaja": {"slug": "la_rebaja", "base": "https://www.larebajavirtual.com"},
    "Locatel": {"slug": "locatel", "base": "https://www.locatelcolombia.com"},
    "Olímpica": {"slug": "olimpica", "base": "https://www.olimpica.com"},
    "Jumbo": {"slug": "jumbo", "base": "https://www.jumbocolombia.com"},
    "Carulla": {"slug": "carulla", "base": "https://www.carulla.com"},
    "Éxito": {"slug": "exito", "base": "https://www.exito.com"},
}

# Nodos del árbol de categorías equivalentes a "Afeitado y depilación" (planilla).
RE_CATEGORIA = re.compile(r"afeit|depil|rasur|cuchilla|maquinilla|after ?shave|despues del afeit", re.I)
# Productos que el sitio mezcla en esas secciones pero que no pertenecen a la categoría.
RE_EXCLUIR = re.compile(
    r"toalla\w* higien|protector diario|tampon|preservativo|condon|panal|shampoo|champu|"
    r"acondicionador|desodorante|perfume|colonia|crema dental|cepillo dental|enjuague bucal|"
    r"hilo dental|algodon|copitos|jeringa|guante|tapabocas|mascarilla", re.I)

COLUMNAS = [
    "comercio", "sku", "ean", "product_id", "url", "nombre_original", "descripcion_original",
    "texto_original", "marca_original", "categoria_sitio", "precio_cop", "precio_regular_cop",
    "en_promocion", "disponible", "fecha_consulta",
]


# ----------------------------------------------------------------------------
# Utilidades
# ----------------------------------------------------------------------------
def ahora_iso():
    return datetime.now(TZ).isoformat(timespec="seconds")


def norm(s):
    """minúsculas, sin tildes, solo alfanumérico y espacios."""
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9., ]", " ", s.lower())).strip()


def _get(url, params=None, reintentos=4):
    ultimo = None
    for i in range(reintentos):
        try:
            r = requests.get(url, params=params, timeout=30,
                             headers={"User-Agent": UA, "Accept": "application/json",
                                      "Accept-Language": "es-CO,es;q=0.9"})
            if r.status_code in (200, 206):
                return r
            ultimo = f"HTTP {r.status_code}"
        except requests.RequestException as e:
            ultimo = str(e)
        time.sleep(2 ** (i + 1))
    raise RuntimeError(f"Fallo al consultar {url}: {ultimo}")


def _guardar_gz(ruta, obj):
    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    with gzip.open(ruta, "wt", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)


def _leer_gz(ruta):
    with gzip.open(ruta, "rt", encoding="utf-8") as f:
        return json.load(f)


# ----------------------------------------------------------------------------
# Scraping genérico VTEX (una función por comercio la envuelve más abajo)
# ----------------------------------------------------------------------------
def _nodos_categoria(arbol, ruta=()):
    """Recorre el árbol y devuelve los nodos que coinciden, sin hijos redundantes."""
    for n in arbol:
        r = ruta + (n["id"],)
        if RE_CATEGORIA.search(norm(n.get("name", ""))):
            yield {"id": n["id"], "nombre": n["name"], "ruta": "/".join(map(str, r))}
        else:
            yield from _nodos_categoria(n.get("children") or [], r)


RANGOS_PRECIO = [(0, 10000), (10000, 20000), (20000, 40000), (40000, 80000),
                 (80000, 160000), (160000, 400000), (400000, 100000000)]
URL_SEARCH = "/api/catalog_system/pub/products/search"


def _total(r):
    m = re.search(r"/(\d+)\s*$", r.headers.get("resources", ""))
    return int(m.group(1)) if m else None


def _paginar_simple(base, fqs, pref):
    desde, total = 0, None
    while desde < MAX_OFFSET:
        r = _get(base + URL_SEARCH, fqs + [("_from", desde), ("_to", desde + PAGINA - 1)])
        total = _total(r) or total
        datos = r.json()
        if not datos:
            break
        yield f"{pref}{desde // PAGINA + 1:03d}", datos, total, pref
        if len(datos) < PAGINA:
            break
        desde += PAGINA
        time.sleep(PAUSA_S)


def _total_fq(base, fqs):
    r = _get(base + URL_SEARCH, fqs + [("_from", 0), ("_to", 0)])
    time.sleep(PAUSA_S)
    return _total(r) or 0


def _dividir(base, fq, a, b):
    """Parte el rango de precio [a, b] por la mitad hasta que cada trozo tenga <= MAX_OFFSET productos."""
    fqs = [("fq", fq), ("fq", f"P:[{a} TO {b}]")]
    tot = _total_fq(base, fqs)
    if tot == 0:
        return
    if tot > MAX_OFFSET and b - a > 1:
        m = (a + b) // 2
        yield from _dividir(base, fq, a, m)
        yield from _dividir(base, fq, m + 1, b)
    else:
        yield from _paginar_simple(base, fqs, f"p{a}_{b}_")


def _paginar(base, fq):
    """Devuelve (etiqueta_pagina, datos, total, prefijo). VTEX no pagina más allá de 2500 resultados:
    si la categoría los supera se divide por rangos de precio (fq=P:[a TO b]) de forma adaptativa."""
    if _total_fq(base, [("fq", fq)]) > MAX_OFFSET:
        yield from _dividir(base, fq, 0, 100_000_000)
    else:
        yield from _paginar_simple(base, [("fq", fq)], "")


def _registros(productos, comercio, base, categoria, ts):
    """Convierte productos VTEX en filas con las mismas columnas (COLUMNAS)."""
    filas = []
    for p in productos:
        for it in p.get("items") or [{}]:
            vendedores = it.get("sellers") or [{}]
            ofertas = [(v.get("sellerDefault"), v.get("commertialOffer") or {}) for v in vendedores]
            stock = [o for _, o in sorted(ofertas, key=lambda x: not x[0]) if (o.get("AvailableQuantity") or 0) > 0 and o.get("Price")]
            vend = stock[0] if stock else ofertas[0][1]
            disp = (vend.get("AvailableQuantity") or 0) > 0
            precio = vend.get("Price")
            lista = vend.get("ListPrice")
            precio = float(precio) if disp and precio else None   # nunca 0: NULL
            lista = float(lista) if lista else None
            nombre = it.get("nameComplete") or p.get("productName") or ""
            desc = re.sub(r"\s+", " ", p.get("description") or "").strip()
            link = p.get("link") or ""
            if not link.startswith("http"):
                link = base + "/" + (p.get("linkText") or "") + "/p"
            filas.append({
                "comercio": comercio, "sku": str(it.get("itemId") or p.get("productId")),
                "ean": (it.get("ean") or "").strip() or None, "product_id": str(p.get("productId")),
                "url": link, "nombre_original": nombre, "descripcion_original": desc,
                "texto_original": f"{nombre} | {desc}" if desc else nombre,
                "marca_original": p.get("brand"), "categoria_sitio": categoria,
                "precio_cop": precio,
                "precio_regular_cop": lista,
                "en_promocion": int(bool(precio and lista and precio < lista)),
                "disponible": int(disp), "fecha_consulta": ts,
            })
    return filas


def _scrape_vtex(comercio, modo="online", fecha=None, raw_dir=None, corrida_id=None):
    """Devuelve (filas, meta). meta: consulta, corrida_id, categorias, n_esperado, n_productos."""
    cfg = COMERCIOS[comercio]
    raw_dir = raw_dir or RAW_DIR
    base = cfg["base"]
    if modo == "online":
        ts = ahora_iso()
        carpeta = os.path.join(raw_dir, cfg["slug"], ts[:10])
        arbol = _get(base + "/api/catalog_system/pub/category/tree/3").json()
        _guardar_gz(os.path.join(carpeta, "arbol_categorias.json.gz"), arbol)
        cats, filas, esperado, vistos, incompletas = [], [], 0, set(), []
        for cat in _nodos_categoria(arbol):
            fq = f"C:/{cat['ruta']}/"
            tot_slices, obtenidos = {}, 0
            for pag, datos, total, pref in _paginar(base, fq):
                tot_slices[pref] = total or 0
                obtenidos += len(datos)
                _guardar_gz(os.path.join(carpeta, f"cat_{cat['id']}_pag_{pag}.json.gz"), datos)
                nuevos = [d for d in datos if d.get("productId") not in vistos]
                vistos.update(d.get("productId") for d in datos)
                filas += _registros(nuevos, comercio, base, cat["nombre"], ts)
            total = sum(tot_slices.values()) or None
            cat["total_sitio"], cat["obtenidos"] = total, obtenidos
            if total and obtenidos < total:
                incompletas.append(f"{cat['nombre']}: {obtenidos}/{total}")
            esperado += total or 0
            cats.append(cat)
        meta = {"consulta": ts, "corrida_id": corrida_id, "categorias": cats,
                "n_esperado": esperado or None, "n_productos": len(vistos), "incompletas": incompletas}
        _guardar_gz(os.path.join(carpeta, "meta.json.gz"), meta)
        return filas, meta
    # --- modo offline: reprocesa raw/ sin red ---
    carpetas = sorted(c for c in glob.glob(os.path.join(raw_dir, cfg["slug"], "*")) if os.path.isdir(c))   # solo carpetas de fecha (no robots.txt)
    if fecha:
        carpetas = [c for c in carpetas if os.path.basename(c) == fecha]
    if not carpetas:
        raise FileNotFoundError(f"No hay copias crudas de {comercio} en {raw_dir}")
    carpeta = carpetas[-1]
    meta = _leer_gz(os.path.join(carpeta, "meta.json.gz"))
    filas, vistos = [], set()
    cats = {str(c["id"]): c["nombre"] for c in meta["categorias"]}
    for ruta in sorted(glob.glob(os.path.join(carpeta, "cat_*_pag_*.json.gz"))):
        cid = os.path.basename(ruta).split("_")[1]
        datos = _leer_gz(ruta)
        nuevos = [d for d in datos if d.get("productId") not in vistos]
        vistos.update(d.get("productId") for d in datos)
        filas += _registros(nuevos, comercio, base, cats.get(cid), meta["consulta"])
    return filas, meta


def scrape_la_rebaja(modo="online", fecha=None, raw_dir=None, corrida_id=None):
    return _scrape_vtex("La Rebaja", modo, fecha, raw_dir, corrida_id)

def scrape_locatel(modo="online", fecha=None, raw_dir=None, corrida_id=None):
    return _scrape_vtex("Locatel", modo, fecha, raw_dir, corrida_id)


def scrape_jumbo(modo="online", fecha=None, raw_dir=None, corrida_id=None):
    return _scrape_vtex("Jumbo", modo, fecha, raw_dir, corrida_id)


def scrape_carulla(modo="online", fecha=None, raw_dir=None, corrida_id=None):
    return _scrape_vtex("Carulla", modo, fecha, raw_dir, corrida_id)


def scrape_olimpica(modo="online", fecha=None, raw_dir=None, corrida_id=None):
    return _scrape_vtex("Olímpica", modo, fecha, raw_dir, corrida_id)

def scrape_exito(modo="online", fecha=None, raw_dir=None, corrida_id=None):
    return _scrape_vtex("Éxito", modo, fecha, raw_dir, corrida_id)



# Comercios candidatos (todos VTEX). Se consultan todos y el análisis usa los 3 con mayor número de
# productos comunes con precio en una misma corrida (ver asignar_correspondencia). Si un sitio
# falla o bloquea, su ejecución queda "fallida" y se descarta sin detener el resto.
# Grupo empresarial: Éxito y Carulla comparten catálogo, SKU y precios (Grupo Éxito); no son comercios independientes.
GRUPO = {"Éxito": "Grupo Éxito", "Carulla": "Grupo Éxito", "Jumbo": "Cencosud"}
SCRAPERS = {"La Rebaja": scrape_la_rebaja, "Locatel": scrape_locatel, "Éxito": scrape_exito,
            "Olímpica": scrape_olimpica, "Jumbo": scrape_jumbo, "Carulla": scrape_carulla}


def verificar_robots(modo="online", raw_dir=None):
    """Descarga (o lee de raw/) el robots.txt de cada comercio y verifica si las rutas usadas están permitidas."""
    from urllib.robotparser import RobotFileParser
    raw_dir, filas = raw_dir or RAW_DIR, []
    for nombre, cfg in COMERCIOS.items():
        if nombre not in SCRAPERS:
            continue
        ruta = os.path.join(raw_dir, cfg["slug"], "robots.txt")
        try:
            if modo == "online":
                os.makedirs(os.path.dirname(ruta), exist_ok=True)
                with open(ruta, "w", encoding="utf-8") as f:
                    f.write(_get(cfg["base"] + "/robots.txt").text)
            with open(ruta, encoding="utf-8") as f:
                rp = RobotFileParser(); rp.parse(f.read().splitlines())
            ok = [rp.can_fetch("*", cfg["base"] + p) for p in ("/api/catalog_system/pub/category/tree/3", URL_SEARCH + "?fq=C:/1/")]
            filas.append({"comercio": nombre, "arbol_categorias_permitido": ok[0], "busqueda_productos_permitida": ok[1]})
        except Exception as e:  # noqa: BLE001
            filas.append({"comercio": nombre, "arbol_categorias_permitido": None, "busqueda_productos_permitida": None, "nota": str(e)[:80]})
    return filas


# ----------------------------------------------------------------------------
# Limpieza y estandarización (los textos originales no se modifican)
# ----------------------------------------------------------------------------
_U = r"(ml|cc|lts?|l|litros?|kg|kilos?|gr?s?|gramos?|mg|und?s?|unidades|unid|uds?)"
_FACTOR = {"ml": ("mL", 1), "cc": ("mL", 1), "l": ("mL", 1000), "lt": ("mL", 1000), "lts": ("mL", 1000),
           "litro": ("mL", 1000), "litros": ("mL", 1000), "kg": ("g", 1000), "kilo": ("g", 1000),
           "kilos": ("g", 1000), "g": ("g", 1), "gr": ("g", 1), "grs": ("g", 1), "gramo": ("g", 1),
           "gramos": ("g", 1), "mg": ("mg", 1)}


def extraer_contenido(texto):
    """Devuelve (contenido_total, unidad_base, unidades_por_paquete) o (None, None, 1)."""
    t = norm(texto).replace(",", ".")
    mult = 1
    m = re.search(rf"\b(\d{{1,3}})\s*x\s*(\d+(?:\.\d+)?)\s*{_U}\b", t)       # 3 x 100 ml
    if m:
        mult, val, u = int(m.group(1)), float(m.group(2)), m.group(3)
    else:
        m = re.search(rf"(\d+(?:\.\d+)?)\s*{_U}\b", t)
        if not m:
            return None, None, 1
        val, u = float(m.group(1)), m.group(2)
        pk = re.search(rf"\b(?:x|pack|paquete de|set de)\s*(\d{{1,3}})\b(?!\s*\.?\d*\s*{_U})", t)
        if pk and u not in ("und", "unds", "unidades", "unid", "u", "ud", "uds"):
            mult = int(pk.group(1))
    if u in _FACTOR:
        unidad, f = _FACTOR[u]
        return val * f * mult, unidad, mult
    return val * mult, "unidad", mult


SUBCATEGORIAS = [
    (r"repuesto|cartucho|recambio|cuchilla", "Cuchillas y repuestos"),
    (r"maquina|maquinilla|afeitadora|rasuradora|recortadora|trimmer", "Máquinas de afeitar"),
    (r"rastrillo|desechable|shave\b.*(1|2|3)\b", "Rastrillos desechables"),
    (r"espuma", "Espumas de afeitar"),
    (r"gel", "Geles de afeitar"),
    (r"cera|depilatori|roll ?on|veet|nair", "Depilación"),
    (r"after ?shave|despues del afeit|post ?afeit|balsamo|locion", "Después del afeitado"),
]


def subcategoria(texto):
    t = norm(texto)
    for rx, nombre in SUBCATEGORIAS:
        if re.search(rx, t):
            return nombre
    return "Otros afeitado/depilación"


STOP = set("de la el en con para y por sin del los las un una x pack set paquete".split())


def nombre_base(nombre, marca):
    """Nombre sin marca, contenido ni palabras vacías: ordenado, para emparejar entre sitios."""
    t = norm(nombre).replace(",", ".")
    t = re.sub(rf"\d+(?:\.\d+)?\s*{_U}\b", " ", t)
    mk = set(norm(marca or "").split())
    toks = sorted({w for w in t.split() if w not in STOP and w not in mk and not w.isdigit() and len(w) > 1})
    return " ".join(toks)


# ----------------------------------------------------------------------------
# SQLite
# ----------------------------------------------------------------------------
DDL = """
CREATE TABLE IF NOT EXISTS comercios (
  id INTEGER PRIMARY KEY, nombre TEXT UNIQUE NOT NULL, url_base TEXT);
CREATE TABLE IF NOT EXISTS ejecuciones (
  id INTEGER PRIMARY KEY AUTOINCREMENT, corrida_id TEXT NOT NULL, inicio TEXT NOT NULL,
  comercio TEXT NOT NULL, modo TEXT NOT NULL, n_registros INTEGER, n_productos INTEGER,
  n_esperado_sitio INTEGER, estado TEXT NOT NULL CHECK (estado IN ('completa','parcial','fallida')),
  detalle TEXT, UNIQUE (corrida_id, comercio));
CREATE TABLE IF NOT EXISTS categorias_sitio (
  corrida_id TEXT, comercio TEXT, categoria_id TEXT, nombre TEXT, ruta TEXT, total_sitio INTEGER,
  PRIMARY KEY (corrida_id, comercio, categoria_id));
CREATE TABLE IF NOT EXISTS productos (          -- identificador común entre comercios
  id INTEGER PRIMARY KEY AUTOINCREMENT, clave_comun TEXT UNIQUE NOT NULL, marca TEXT,
  nombre_estandar TEXT, subcategoria TEXT, contenido_total REAL, unidad_base TEXT,
  n_comercios INTEGER, comparable INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS anuncios (           -- producto tal como aparece en un comercio
  id INTEGER PRIMARY KEY AUTOINCREMENT, comercio_id INTEGER NOT NULL REFERENCES comercios(id),
  sku TEXT NOT NULL, ean TEXT, product_id TEXT, url TEXT NOT NULL,
  nombre_original TEXT, descripcion_original TEXT, texto_original TEXT NOT NULL,
  marca_original TEXT, categoria_sitio TEXT,
  marca_estandar TEXT, nombre_estandar TEXT, nombre_base TEXT, subcategoria TEXT,
  contenido_total REAL, unidad_base TEXT, unidades_paquete INTEGER,
  excluido INTEGER NOT NULL DEFAULT 0, motivo_exclusion TEXT,
  producto_id INTEGER REFERENCES productos(id), UNIQUE (comercio_id, sku));
CREATE TABLE IF NOT EXISTS observaciones (
  id INTEGER PRIMARY KEY AUTOINCREMENT, anuncio_id INTEGER NOT NULL REFERENCES anuncios(id),
  ejecucion_id INTEGER NOT NULL REFERENCES ejecuciones(id),
  precio_cop REAL, precio_regular_cop REAL, en_promocion INTEGER NOT NULL, disponible INTEGER NOT NULL,
  fecha_consulta TEXT NOT NULL, UNIQUE (anuncio_id, fecha_consulta));
CREATE TABLE IF NOT EXISTS seleccion (        -- comercios usados en el análisis
  comercio_id INTEGER PRIMARY KEY REFERENCES comercios(id));
DROP VIEW IF EXISTS v_obs;
CREATE VIEW v_obs AS
SELECT e.corrida_id, c.nombre AS comercio, o.fecha_consulta, o.precio_cop, o.precio_regular_cop,
       o.en_promocion, o.disponible, a.id AS anuncio_id, a.sku, a.url, a.texto_original,
       a.marca_estandar, a.nombre_estandar, a.nombre_base, a.subcategoria, a.contenido_total,
       a.unidad_base, a.excluido, a.motivo_exclusion, p.id AS producto_id, p.clave_comun,
       CASE WHEN p.comparable = 1 AND a.comercio_id IN (SELECT comercio_id FROM seleccion) THEN 1 ELSE 0 END AS comparable
FROM observaciones o JOIN ejecuciones e ON e.id = o.ejecucion_id
JOIN anuncios a ON a.id = o.anuncio_id JOIN comercios c ON c.id = a.comercio_id
LEFT JOIN productos p ON p.id = a.producto_id;
"""


def conectar(db_path=None):
    con = sqlite3.connect(db_path or DB_PATH)
    con.row_factory = sqlite3.Row
    if con.execute("PRAGMA user_version").fetchone()[0] not in (0, SCHEMA_VERSION):
        raise RuntimeError("Base con esquema antiguo: elimine comparador_precios.sqlite y reejecute.")
    if not con.execute("SELECT 1 FROM sqlite_master WHERE name='ejecuciones'").fetchone() and \
            con.execute("SELECT 1 FROM sqlite_master WHERE name='observaciones'").fetchone():
        raise RuntimeError("Base con esquema antiguo: elimine comparador_precios.sqlite y reejecute.")
    con.executescript(DDL)
    con.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
    return con


def _insertar(con, fila, ejec_id):
    cid = con.execute("SELECT id FROM comercios WHERE nombre=?", (fila["comercio"],)).fetchone()["id"]
    marca = (fila["marca_original"] or "").strip()
    marca_std = marca.title() if marca else None
    cont, unidad, mult = extraer_contenido(fila["nombre_original"])
    if cont is None:
        cont, unidad, mult = extraer_contenido(fila["descripcion_original"][:300])
    texto = fila["nombre_original"]
    exc = RE_EXCLUIR.search(norm(texto))
    con.execute("""INSERT INTO anuncios (comercio_id, sku, ean, product_id, url, nombre_original,
        descripcion_original, texto_original, marca_original, categoria_sitio, marca_estandar,
        nombre_estandar, nombre_base, subcategoria, contenido_total, unidad_base, unidades_paquete,
        excluido, motivo_exclusion) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(comercio_id, sku) DO UPDATE SET url=excluded.url, texto_original=excluded.texto_original,
        nombre_original=excluded.nombre_original, descripcion_original=excluded.descripcion_original,
        marca_original=excluded.marca_original, categoria_sitio=excluded.categoria_sitio,
        marca_estandar=excluded.marca_estandar, nombre_estandar=excluded.nombre_estandar,
        nombre_base=excluded.nombre_base, subcategoria=excluded.subcategoria,
        contenido_total=excluded.contenido_total, unidad_base=excluded.unidad_base,
        unidades_paquete=excluded.unidades_paquete, excluido=excluded.excluido,
        motivo_exclusion=excluded.motivo_exclusion, ean=excluded.ean""",
        (cid, fila["sku"], fila["ean"], fila["product_id"], fila["url"], fila["nombre_original"],
         fila["descripcion_original"], fila["texto_original"], fila["marca_original"],
         fila["categoria_sitio"], marca_std, re.sub(r"\bsku\s*\d+\b", "", norm(texto)).strip(), nombre_base(texto, marca), subcategoria(texto),
         cont, unidad, mult, int(bool(exc)),
         f"Fuera de la categoría ({exc.group(0)})" if exc else None))
    aid = con.execute("SELECT id FROM anuncios WHERE comercio_id=? AND sku=?", (cid, fila["sku"])).fetchone()["id"]
    cur = con.execute("""INSERT OR IGNORE INTO observaciones (anuncio_id, ejecucion_id, precio_cop,
        precio_regular_cop, en_promocion, disponible, fecha_consulta) VALUES (?,?,?,?,?,?,?)""",
                      (aid, ejec_id, fila["precio_cop"], fila["precio_regular_cop"], fila["en_promocion"],
                       fila["disponible"], fila["fecha_consulta"]))
    return cur.rowcount


# ----------------------------------------------------------------------------
# Correspondencia de productos entre comercios
# ----------------------------------------------------------------------------
def asignar_correspondencia(con, corrida_id, csv_path=None):
    """
    Regla documentada: misma presentación = mismo EAN; si falta el EAN, misma marca +
    mismo nombre_base + mismo contenido total y unidad. Entre los comercios de la corrida se
    elige la terna con más productos presentes, con precio, en los tres (tabla seleccion);
    comparable=1 para esos productos.
    """
    filas = con.execute("SELECT * FROM anuncios WHERE excluido=0").fetchall()

    def tkey(r):
        return f"{norm(r['marca_estandar'])}|{r['nombre_base']}|{r['contenido_total']}|{r['unidad_base']}"
    ean_por_txt = {}
    for r in filas:
        if r["ean"] and len(r["ean"]) >= 8 and r["contenido_total"]:
            ean_por_txt.setdefault(tkey(r), r["ean"])
    grupos = {}
    for r in filas:
        if r["ean"] and len(r["ean"]) >= 8:
            k = "EAN:" + r["ean"]
        elif tkey(r) in ean_por_txt:
            k = "EAN:" + ean_por_txt[tkey(r)]
        elif r["contenido_total"] and r["marca_estandar"]:
            k = "TXT:" + tkey(r)
        else:
            continue
        grupos.setdefault(k, []).append(r)
    con.execute("UPDATE anuncios SET producto_id=NULL")
    con.execute("DELETE FROM productos")
    con_precio = {r["anuncio_id"] for r in con.execute(
        "SELECT anuncio_id FROM v_obs WHERE corrida_id=? AND precio_cop IS NOT NULL", (corrida_id,))}
    salida = []
    ids_run = [r[0] for r in con.execute(
        """SELECT DISTINCT c.id FROM ejecuciones e JOIN comercios c ON c.nombre=e.comercio
           WHERE e.corrida_id=? AND e.estado!='fallida' ORDER BY c.id""", (corrida_id,))]
    con_precio_g = {k: {r["comercio_id"] for r in rs if r["id"] in con_precio} for k, rs in grupos.items()}
    nom = {r[0]: r[1] for r in con.execute("SELECT id, nombre FROM comercios")}
    indep = lambda cb: len({GRUPO.get(nom[i], nom[i]) for i in cb}) == len(cb)   # un comercio por grupo
    mejor = max((cb for cb in combinations(ids_run, min(3, len(ids_run))) if indep(cb)) or [()],
                key=lambda cb: sum(set(cb) <= s for s in con_precio_g.values()), default=())
    con.execute("DELETE FROM seleccion")
    con.executemany("INSERT INTO seleccion VALUES (?)", [(i,) for i in mejor])
    for k, rs in grupos.items():
        coms = {r["comercio_id"] for r in rs}
        comp = int(bool(mejor) and set(mejor) <= con_precio_g[k])
        donante = min((r for r in rs if r["contenido_total"]), key=lambda r: len(r["nombre_estandar"]), default=None)
        if donante and k.startswith("EAN:"):   # mismo EAN = mismo contenido (regla documentada)
            con.executemany("UPDATE anuncios SET contenido_total=?, unidad_base=? WHERE id=?",
                            [(donante["contenido_total"], donante["unidad_base"], r["id"])
                             for r in rs if not r["contenido_total"]])
        r0 = min(rs, key=lambda r: len(r["nombre_estandar"]))
        if donante:
            r0 = dict(r0); r0["contenido_total"], r0["unidad_base"] = donante["contenido_total"], donante["unidad_base"]
        pid = con.execute("""INSERT INTO productos (clave_comun, marca, nombre_estandar, subcategoria,
            contenido_total, unidad_base, n_comercios, comparable) VALUES (?,?,?,?,?,?,?,?)""",
                          (k, r0["marca_estandar"], r0["nombre_estandar"], r0["subcategoria"],
                           r0["contenido_total"], r0["unidad_base"], len(coms), comp)).lastrowid
        con.executemany("UPDATE anuncios SET producto_id=? WHERE id=?", [(pid, r["id"]) for r in rs])
        if comp:
            salida += [(pid, k, r["comercio_id"], r["sku"], r["texto_original"][:90]) for r in rs if r["comercio_id"] in mejor]
    con.commit()
    if csv_path is not None:
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["producto_id", "clave_comun", "comercio_id", "sku", "nombre"])
            w.writerows(sorted(salida))
    return len([1 for k, rs in grupos.items()])


# ----------------------------------------------------------------------------
# Orquestador
# ----------------------------------------------------------------------------
def ejecutar_recoleccion_completa(modo="online", fecha=None, db_path=None, raw_dir=None, comercios=None):
    """Ejecuta los scrapers, registra cada ejecución e inserta sin sobrescribir observaciones."""
    con = conectar(db_path)
    corrida = "run-" + datetime.now(TZ).strftime("%Y%m%dT%H%M%S")
    resumen = {}
    for nombre, fn in SCRAPERS.items():
        if comercios and nombre not in comercios:
            continue
        con.execute("INSERT OR IGNORE INTO comercios (nombre, url_base) VALUES (?,?)",
                    (nombre, COMERCIOS[nombre]["base"]))
        inicio = ahora_iso()
        try:
            filas, meta = fn(modo, fecha, raw_dir, corrida)
            cid = meta.get("corrida_id") or (corrida if modo == "online" else "raw-" + meta["consulta"])
            esperado, nprod = meta.get("n_esperado"), meta.get("n_productos")
            estado = "completa" if filas and not meta.get("incompletas") else "parcial"
            detalle = None if estado == "completa" else f"categorías incompletas: {meta.get('incompletas')}"
        except Exception as e:  # noqa: BLE001
            filas, cid, estado, detalle, esperado, nprod, meta = [], corrida, "fallida", str(e)[:300], None, 0, {}
        con.execute("""INSERT INTO ejecuciones (corrida_id, inicio, comercio, modo, n_registros, n_productos,
            n_esperado_sitio, estado, detalle) VALUES (?,?,?,?,?,?,?,?,?)
            ON CONFLICT(corrida_id, comercio) DO UPDATE SET n_registros=excluded.n_registros,
            n_productos=excluded.n_productos, n_esperado_sitio=excluded.n_esperado_sitio,
            estado=excluded.estado, detalle=excluded.detalle""",
                    (cid, meta.get("consulta", inicio), nombre, modo, len(filas), nprod, esperado, estado, detalle))
        eid = con.execute("SELECT id FROM ejecuciones WHERE corrida_id=? AND comercio=?", (cid, nombre)).fetchone()["id"]
        for c in meta.get("categorias", []):
            con.execute("INSERT OR REPLACE INTO categorias_sitio VALUES (?,?,?,?,?,?)",
                        (cid, nombre, str(c["id"]), c["nombre"], c["ruta"], c.get("total_sitio")))
        ins = sum(_insertar(con, f, eid) for f in filas)
        con.commit()
        resumen[nombre] = {"estado": estado, "registros": len(filas), "nuevos": ins, "detalle": detalle}
        print(f"[{nombre}] {estado}: {len(filas)} registros, {ins} observaciones nuevas {detalle or ''}")
    if modo == "online":
        try:
            verificar_robots("online", raw_dir)   # guarda robots.txt en raw/<comercio>/
        except Exception as e:  # noqa: BLE001
            print("robots.txt no verificado:", e)
    ult = con.execute("SELECT corrida_id FROM ejecuciones WHERE estado!='fallida' ORDER BY id DESC LIMIT 1").fetchone()
    if ult:
        n = asignar_correspondencia(con, ult[0], os.path.join(os.path.dirname(db_path or DB_PATH), "comparables.csv"))
        sel = [r[0] for r in con.execute("SELECT c.nombre FROM seleccion s JOIN comercios c ON c.id=s.comercio_id")]
        nc = con.execute("SELECT COUNT(*) FROM productos WHERE comparable=1").fetchone()[0]
        print(f"Comercios en el análisis: {sel}. Productos comparables: {nc} ({nc * len(sel)} observaciones).")
        print("CUMPLE el mínimo (>=8 productos x 3 comercios)." if nc >= 8 and len(sel) == 3 else
              "ATENCIÓN: NO cumple el mínimo de 8 comparables en 3 comercios.")
    con.close()
    return resumen


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--modo", choices=["online", "offline"], default="online")
    ap.add_argument("--fecha", help="AAAA-MM-DD de la copia cruda (solo offline)")
    ap.add_argument("--comercios", nargs="*", help="subconjunto de comercios (por defecto todos los candidatos)")
    a = ap.parse_args()
    ejecutar_recoleccion_completa(a.modo, a.fecha, comercios=a.comercios)