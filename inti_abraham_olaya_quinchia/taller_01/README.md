# Taller 1 — Comparador de precios: Afeitado y depilación

**Asignatura:** Minería de Datos (2016325) · Universidad Nacional de Colombia
**Estudiante:** Inti Abraham Olaya Quinchia (1000240392)
**Lenguaje elegido:** Python (extracción, limpieza, SQLite, consultas y gráficos)

## Comercios y técnica de extracción

| Comercio | Técnica |
|---|---|
| Candidatos: La Rebaja, Locatel, Éxito, Olímpica, Jumbo, Carulla | `requests` a los servicios JSON de VTEX (árbol de categorías + `products/search`) |

Se consultan los seis candidatos (todos VTEX) y el análisis usa automáticamente los **tres** con más productos comunes con precio en la misma corrida (tabla `seleccion`); un sitio que falle queda como ejecución `fallida` sin detener el resto. Sin navegador automatizado. La paginación usa `_from/_to` (50 por página) y el total del sitio se lee del
encabezado `resources` para verificar la completitud. Pausa de 1 s entre peticiones y *User-Agent* identificado.

## Estructura

```text
taller_01/
├── taller_1.qmd                 # informe reproducible (consultas, gráficos, conclusiones)
├── scraper.py                   # módulo de extracción + limpieza + SQLite + correspondencia
├── comparador_precios.sqlite    # base generada por el scraper
├── comparables.csv              # correspondencia de comparables (generada por código)
├── raw/<comercio>/<fecha>/      # copias crudas (.json.gz) + meta.json.gz
├── generar_pdf.py               # genera taller_1.pdf sin Quarto
├── prueba_colab.ipynb           # cuaderno para validar la ejecución en Google Colab
├── requirements.txt             # dependencias con versiones fijas
└── app.py                       # app Streamlit auxiliar (opcional)
```

## Ejecución

```bash
pip install -r requirements.txt
```

**En línea** (scraping real; guarda `raw/` y añade observaciones sin sobrescribir):

```bash
python scraper.py                  # consulta los 6 candidatos; o: python -c "import scraper; scraper.ejecutar_recoleccion_completa('online')"
```

**Sin conexión** (reprocesa `raw/`; opcional `--fecha AAAA-MM-DD`):

```bash
python scraper.py --modo offline
```

**Informe** (requiere [Quarto](https://quarto.org)):

```bash
quarto render taller_1.qmd --to html      # TALLER_MODO=online para scraping en vivo al renderizar
python generar_pdf.py                     # PDF sin Quarto (recomendado), o: quarto render taller_1.qmd --to pdf
```

> Si cambió el esquema respecto a una versión anterior, elimine `comparador_precios.sqlite` y vuelva a ejecutar.

## Base de datos

`comercios`, `ejecuciones` (corrida, inicio, comercio, registros, esperado del sitio, estado),
`categorias_sitio`, `productos` (id común y `comparable`), `anuncios` (texto original intacto y campos
estandarizados), `observaciones` (precio, promoción, fecha ISO 8601 -05:00, `UNIQUE(anuncio_id, fecha_consulta)`)
y la vista `v_obs`. Valores ausentes quedan `NULL`; los agotados no tienen precio.

## Correspondencia de productos

Misma presentación = mismo EAN; si falta, misma marca + nombre base + contenido + unidad. `comparable = 1`
solo si hay precio en todos los comercios en la misma corrida. Revisar `comparables.csv` a mano.

## Probar fuera del computador personal

Google Colab: `!git clone ...`, `%cd`, `!pip install -r requirements.txt`, `!python scraper.py` y reportar el resultado.
