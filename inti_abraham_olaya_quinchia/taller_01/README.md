# README.md - Taller 1 de Minería de Datos
# Comparador de Precios en Colombia: Categoría "Afeitado y Depilación"

**Asignatura:** Minería de Datos (Código 2016325)  
**Institución:** Universidad Nacional de Colombia  
**Modalidad:** Individual (25% de la nota final)  
**Categoría Elegida:** 10 - Afeitado y depilación  

---

## Descripción del Proyecto

Este proyecto implementa un comparador de precios automatizado para productos de la categoría **Afeitado y depilación** en tres comercios electrónicos líderes en Colombia: **Farmatodo**, **Cruz Verde** y **Éxito**.

El sistema realiza web scraping de las ofertas disponibles, almacena la información estructurada en una base de datos relacional **SQLite** (`comparador_precios.sqlite`), calcula el precio estandarizado por unidad de medida (COP/mL, COP/g, COP/unidad) y evalúa las oportunidades de ahorro para el consumidor final mediante un informe analítico reproducible desarrollado en **Quarto** (`taller_1.qmd`).

---

## Estructura del Repositorio

```text
taller-1-mineria-datos/
├── .gitignore                      # Filtro de archivos excluidos de Git
├── README.md                       # Documento de instrucciones y guía de uso
├── requirements.txt                # Dependencias de Python con versiones fijas
├── scraper.py                      # Módulo independiente de web scraping
├── comparador_precios.sqlite       # Base de datos relacional SQLite
├── taller_1.qmd                    # Documento ejecutable en Quarto (Reporte Final)
└── raw/                            # Almacenamiento local de respuestas crudas (HTML/JSON)
    ├── farmatodo_afeitado.json
    ├── cruzverde_afeitado.html
    └── exito_afeitado.json
```

---

## Instrucciones de Ejecución en GitHub Codespaces

### 1. Instalación de Dependencias
Abre la terminal en GitHub Codespaces y ejecuta:
```bash
pip install -r requirements.txt
```

### 2. Ejecución del Scraper
El módulo `scraper.py` permite la ejecución en dos modos:

* **Modo Offline (Predeterminado - Recomendado para evaluación):**  
  Lee los archivos crudos almacenados en la carpeta `raw/` sin realizar peticiones a la red.
  ```bash
  python scraper.py
  ```

* **Modo Online:**  
  Realiza peticiones HTTP en vivo a las tiendas web y actualiza los archivos en `raw/`.
  ```python
  from scraper import ejecutar_recoleccion
  ejecutar_recoleccion(modo_offline=False)
  ```

### 3. Inspección de la Base de Datos SQLite
Puedes consultar las tablas de la base de datos usando la extensión **SQLite Viewer** en VS Code o desde la terminal de Python/SQLite:
```bash
sqlite3 comparador_precios.sqlite ".tables"
```

### 4. Compilación del Informe Reproducible (`taller_1.qmd`)
Para renderizar el informe analítico en formato HTML:
```bash
quarto render taller_1.qmd --to html
```

---

## Estructura de la Base de Datos (`comparador_precios.sqlite`)

La base de datos relacional consta de 4 tablas interconectadas:
1. `ejecuciones`: Almacena el historial de corridas del scraper, identificando fecha/hora en formato **ISO 8601** (zona horaria de Colombia `UTC-5`) y el modo de ejecución.
2. `productos`: Catálogo maestro normalizado de marcas y subcategorías.
3. `anuncios`: Mapeo de URLs, SKUs y el **texto original del anuncio sin modificaciones**.
4. `observaciones`: Histórico de precios en COP, flags de promoción, precios regulares y marcas de tiempo.

---

## Autor
* **Estudiante:** INTI ABRAHAM OLAYA QUINCHIA Taller 1 - Minería de Datos
* **Universidad:** Universidad Nacional de Colombia
