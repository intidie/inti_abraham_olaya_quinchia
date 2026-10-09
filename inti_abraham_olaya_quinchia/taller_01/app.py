import os
import re
import sqlite3
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

# Importar módulo de scraper local si existe
try:
    import scraper
except ImportError:
    scraper = None

# ==========================================
# CONFIGURACIÓN DE LA PÁGINA STREAMLIT
# ==========================================
st.set_page_config(
    page_title="Comparador de Precios - Afeitado y Depilación",
    page_icon="🛒",
    layout="wide",
    initial_sidebar_state="expanded"
)

DB_PATH = "comparador_precios.sqlite"

# Estilos CSS personalizados
st.markdown("""
    <style>
    .main-title {
        font-size: 2.2rem;
        color: #1E3A8A;
        font-weight: bold;
        margin-bottom: 0px;
    }
    .sub-title {
        font-size: 1.1rem;
        color: #4B5563;
        margin-bottom: 20px;
    }
    .metric-card {
        background-color: #F3F4F6;
        padding: 15px;
        border-radius: 10px;
        border-left: 5px solid #2563EB;
        margin-bottom: 15px;
    }
    </style>
""", unsafe_allow_html=True)

# ==========================================
# FUNCIONES DE BASE DE DATOS Y AYUDANTES
# ==========================================

@st.cache_data(ttl=60)
def cargar_datos_completos(db_file=DB_PATH):
    if not os.path.exists(db_file):
        return pd.DataFrame()
    
    conn = sqlite3.connect(db_file)
    query = """
    SELECT 
        o.id as observacion_id,
        a.comercio,
        p.id as producto_id,
        p.nombre_normalizado as producto,
        p.marca,
        p.subcategoria,
        o.precio_cop,
        o.en_promocion,
        o.precio_regular_cop,
        a.url,
        a.texto_original,
        o.fecha_consulta,
        e.id as ejecucion_id,
        e.tipo as tipo_ejecucion
    FROM observaciones o
    JOIN anuncios a ON o.anuncio_id = a.id
    JOIN productos p ON a.producto_id = p.id
    JOIN ejecuciones e ON o.ejecucion_id = e.id
    ORDER BY p.nombre_normalizado, a.comercio
    """
    df = pd.read_sql_query(query, conn)
    conn.close()
    
    # Extraer unidad de medida y cantidad usando Expresiones Regulares
    def extraer_unidad(texto):
        texto = str(texto)
        match_ml = re.search(r'(\d+)\s*ml', texto, re.IGNORECASE)
        match_g = re.search(r'(\d+)\s*g', texto, re.IGNORECASE)
        match_und = re.search(r'(\d+)\s*(unidades|unidad|uds|unid)', texto, re.IGNORECASE)
        
        if match_ml:
            cant = float(match_ml.group(1))
            return cant, "mL", "COP/mL"
        elif match_g:
            cant = float(match_g.group(1))
            return cant, "g", "COP/g"
        elif match_und:
            cant = float(match_und.group(1))
            return cant, "Unidades", "COP/unidad"
        else:
            return 1.0, "Unidad base", "COP/unidad"

    unidades_info = df['texto_original'].apply(extraer_unidad)
    df['cantidad_contenido'] = [x[0] for x in unidades_info]
    df['unidad_medida'] = [x[1] for x in unidades_info]
    df['tipo_precio_unitario'] = [x[2] for x in unidades_info]
    df['precio_por_unidad'] = (df['precio_cop'] / df['cantidad_contenido']).round(2)
    
    return df

def ejecutar_consulta_sql(query, db_file=DB_PATH):
    if not os.path.exists(db_file):
        return pd.DataFrame()
    conn = sqlite3.connect(db_file)
    df = pd.read_sql_query(query, conn)
    conn.close()
    return df

# ==========================================
# ENCABEZADO Y BARRA LATERAL
# ==========================================

st.markdown('<p class="main-title">🛒 Comparador de Precios: Afeitado y Depilación</p>', unsafe_allow_html=True)
st.markdown('<p class="sub-title">Taller 1 - Minería de Datos | Universidad Nacional de Colombia</p>', unsafe_allow_html=True)

st.sidebar.title("🛒 Opciones & Filtros")

# Cargar datos iniciales
df_raw = cargar_datos_completos()

if df_raw.empty:
    st.warning("⚠️ No se encontró la base de datos `comparador_precios.sqlite`. Ejecuta el scraper desde el panel lateral para generarla.")

# Panel de control de Scraper en la barra lateral
st.sidebar.subheader("⚡ Control de Scraper")
modo_offline = st.sidebar.checkbox("Modo Offline (Usar archivos en raw/)", value=True)

if st.sidebar.button("🚀 Ejecutar Scraper Ahora"):
    if scraper is not None:
        with st.spinner("Ejecutando recolección de datos..."):
            try:
                productos_capturados = scraper.ejecutar_recoleccion(modo_offline=modo_offline)
                st.sidebar.success(f"✅ Scraping finalizado: {len(productos_capturados)} observaciones.")
                st.cache_data.clear()
                st.rerun()
            except Exception as e:
                st.sidebar.error(f"❌ Error al ejecutar scraper: {e}")
    else:
        st.sidebar.error("Módulo `scraper.py` no disponible.")

st.sidebar.markdown("---")
st.sidebar.subheader("🎯 Filtros de Productos")

if not df_raw.empty:
    comercios_sel = st.sidebar.multiselect(
        "Comercios:",
        options=df_raw['comercio'].unique().tolist(),
        default=df_raw['comercio'].unique().tolist()
    )
    
    marcas_sel = st.sidebar.multiselect(
        "Marcas:",
        options=df_raw['marca'].unique().tolist(),
        default=df_raw['marca'].unique().tolist()
    )
    
    solo_promos = st.sidebar.checkbox("Mostrar solo productos en promoción", value=False)
    busqueda_txt = st.sidebar.text_input("🔍 Buscar por nombre o texto:", "")

    # Aplicar Filtros
    df_filtered = df_raw[
        (df_raw['comercio'].isin(comercios_sel)) &
        (df_raw['marca'].isin(marcas_sel))
    ]
    
    if solo_promos:
        df_filtered = df_filtered[df_filtered['en_promocion'] == 1]
        
    if busqueda_txt:
        df_filtered = df_filtered[
            df_filtered['producto'].str.contains(busqueda_txt, case=False, na=False) |
            df_filtered['texto_original'].str.contains(busqueda_txt, case=False, na=False)
        ]
else:
    df_filtered = pd.DataFrame()

# ==========================================
# PESTAÑAS PRINCIPALES DE LA APLICACIÓN
# ==========================================

tab1, tab2, tab3, tab4 = st.tabs([
    "📊 Comparador Interactivo", 
    "🔍 Consultas SQL del Taller", 
    "⚡ Historial de Ejecuciones", 
    "ℹ️ Información del Taller"
])

# ------------------------------------------
# TAB 1: COMPARADOR INTERACTIVO
# ------------------------------------------
with tab1:
    if not df_filtered.empty:
        # Métricas Clave (KPIs)
        col1, col2, col3, col4 = st.columns(4)
        
        with col1:
            st.metric("Total Observaciones", len(df_filtered))
        with col2:
            prom_precio = f"${df_filtered['precio_cop'].mean():,.0f} COP"
            st.metric("Precio Promedio", prom_precio)
        with col3:
            comercio_barato = df_filtered.groupby('comercio')['precio_cop'].mean().idxmin()
            st.metric("Comercio + Económico (Prom)", comercio_barato)
        with col4:
            pct_promos = (df_filtered['en_promocion'].sum() / len(df_filtered)) * 100
            st.metric("Productos en Promoción", f"{pct_promos:.1f}%")

        st.markdown("---")
        
        # Gráficos interactivos con Plotly
        c_left, c_right = st.columns(2)
        
        with c_left:
            st.subheader("💰 Precio COP por Producto y Comercio")
            fig_bar = px.bar(
                df_filtered,
                x="producto",
                y="precio_cop",
                color="comercio",
                barmode="group",
                text_auto=",.0f",
                title="Comparación Directa de Precios (COP)",
                labels={"precio_cop": "Precio ($ COP)", "producto": "Producto Normalizado"},
                color_discrete_sequence=px.colors.qualitative.Set2
            )
            fig_bar.update_layout(xaxis_tickangle=-45, legend_title_text="Comercio")
            st.plotly_chart(fig_bar, use_container_width=True)
            
        with c_right:
            st.subheader("📏 Precio por Unidad de Medida (COP/mL, COP/g, COP/und)")
            fig_unit = px.bar(
                df_filtered,
                x="producto",
                y="precio_por_unidad",
                color="comercio",
                barmode="group",
                text_auto=",.1f",
                hover_data=["unidad_medida", "tipo_precio_unitario"],
                title="Costo Estandarizado por Contenido",
                labels={"precio_por_unidad": "Precio por Unidad", "producto": "Producto"},
                color_discrete_sequence=px.colors.qualitative.Pastel
            )
            fig_unit.update_layout(xaxis_tickangle=-45)
            st.plotly_chart(fig_unit, use_container_width=True)

        st.subheader("📋 Tabla Detallada de Comparación")
        
        cols_mostrar = [
            'comercio', 'producto', 'marca', 'precio_cop', 
            'en_promocion', 'cantidad_contenido', 'unidad_medida', 
            'precio_por_unidad', 'url'
        ]
        
        df_display = df_filtered[cols_mostrar].copy()
        df_display['en_promocion'] = df_display['en_promocion'].map({1: "✅ Sí", 0: "❌ No"})
        df_display['precio_cop'] = df_display['precio_cop'].apply(lambda x: f"${x:,.0f} COP")
        
        st.dataframe(
            df_display,
            column_config={
                "url": st.column_config.LinkColumn("Enlace al Comercio")
            },
            use_container_width=True,
            hide_index=True
        )
    else:
        st.info("No hay datos que coincidan con los filtros seleccionados.")

# ------------------------------------------
# TAB 2: CONSULTAS SQL DEL TALLER
# ------------------------------------------
with tab2:
    st.header("🔍 Consultas SQL Requeridas por el Taller")
    st.write("Selecciona cualquiera de las 8 consultas analíticas exigidas en la consigna para ver su código SQL, resultados e interpretación.")
    
    opcion_sql = st.selectbox(
        "Selecciona la consulta a ejecutar:",
        [
            "1. Conteo total de observaciones por comercio",
            "2. Precio promedio y rango de precios por comercio",
            "3. Productos con mayor diferencia de precio entre comercios",
            "4. Ahorro potencial máximo (COP y %) por producto",
            "5. Porcentaje de productos en promoción por comercio",
            "6. Comparación de precio por unidad de contenido (COP/mL, COP/g)",
            "7. Ranking de productos más económicos en cada comercio",
            "8. Resumen global de ejecuciones del scraper"
        ]
    )
    
    queries = {
        "1. Conteo total de observaciones por comercio": (
            """
            SELECT 
                a.comercio,
                COUNT(o.id) AS total_observaciones,
                COUNT(DISTINCT a.producto_id) AS productos_unicos
            FROM observaciones o
            JOIN anuncios a ON o.anuncio_id = a.id
            GROUP BY a.comercio
            ORDER BY total_observaciones DESC;
            """,
            "Muestra el número total de registros recopilados en la última corrida por cada uno de los 3 comercios."
        ),
        "2. Precio promedio y rango de precios por comercio": (
            """
            SELECT 
                a.comercio,
                ROUND(AVG(o.precio_cop), 2) AS precio_promedio_cop,
                MIN(o.precio_cop) AS precio_minimo_cop,
                MAX(o.precio_cop) AS precio_maximo_cop
            FROM observaciones o
            JOIN anuncios a ON o.anuncio_id = a.id
            GROUP BY a.comercio;
            """,
            "Permite identificar la dispersión de precios y el nivel promedio de costo general de cada tienda."
        ),
        "3. Productos con mayor diferencia de precio entre comercios": (
            """
            SELECT 
                p.nombre_normalizado AS producto,
                MIN(o.precio_cop) AS precio_minimo,
                MAX(o.precio_cop) AS precio_maximo,
                (MAX(o.precio_cop) - MIN(o.precio_cop)) AS brecha_cop,
                ROUND(((MAX(o.precio_cop) - MIN(o.precio_cop)) / MIN(o.precio_cop)) * 100, 2) AS brecha_porcentaje
            FROM observaciones o
            JOIN anuncios a ON o.anuncio_id = a.id
            JOIN productos p ON a.producto_id = p.id
            GROUP BY p.id
            ORDER BY brecha_cop DESC;
            """,
            "Mide la variación absoluta y relativa de precios para un mismo artículo exacto entre comercios."
        ),
        "4. Ahorro potencial máximo (COP y %) por producto": (
            """
            SELECT 
                p.nombre_normalizado AS producto,
                MIN(o.precio_cop) AS precio_mas_bajo,
                MAX(o.precio_cop) AS precio_mas_alto,
                (MAX(o.precio_cop) - MIN(o.precio_cop)) AS ahorro_maximo_cop
            FROM observaciones o
            JOIN anuncios a ON o.anuncio_id = a.id
            JOIN productos p ON a.producto_id = p.id
            GROUP BY p.id
            ORDER BY ahorro_maximo_cop DESC;
            """,
            "Identifica cuánto dinero en pesos colombianos puede ahorrar un consumidor si compra en el comercio más barato."
        ),
        "5. Porcentaje de productos en promoción por comercio": (
            """
            SELECT 
                a.comercio,
                COUNT(o.id) AS total_productos,
                SUM(o.en_promocion) AS en_promocion,
                ROUND((CAST(SUM(o.en_promocion) AS FLOAT) / COUNT(o.id)) * 100, 2) AS porcentaje_promocion
            FROM observaciones o
            JOIN anuncios a ON o.anuncio_id = a.id
            GROUP BY a.comercio;
            """,
            "Evalúa qué tienda utiliza más estrategias de descuento comercial en la categoría."
        ),
        "6. Comparación de precio por unidad de contenido (COP/mL, COP/g)": (
            """
            SELECT 
                a.comercio,
                p.nombre_normalizado AS producto,
                o.precio_cop,
                a.texto_original
            FROM observaciones o
            JOIN anuncios a ON o.anuncio_id = a.id
            JOIN productos p ON a.producto_id = p.id
            ORDER BY p.nombre_normalizado, o.precio_cop ASC;
            """,
            "Muestra los datos crudos para evaluar la eficiencia de costo por volumen o peso."
        ),
        "7. Ranking de productos más económicos en cada comercio": (
            """
            SELECT 
                a.comercio,
                p.nombre_normalizado AS producto,
                o.precio_cop,
                RANK() OVER (PARTITION BY a.comercio ORDER BY o.precio_cop ASC) AS ranking_precio
            FROM observaciones o
            JOIN anuncios a ON o.anuncio_id = a.id
            JOIN productos p ON a.producto_id = p.id;
            """,
            "Utiliza funciones de ventana (RANK) para posicionar los productos del más económico al más costoso por tienda."
        ),
        "8. Resumen global de ejecuciones del scraper": (
            """
            SELECT 
                id AS ejecucion_id,
                fecha_hora,
                tipo,
                estado
            FROM ejecuciones
            ORDER BY id DESC;
            """,
            "Verifica la trazabilidad e historial de las corridas del extractor."
        )
    }
    
    query_str, explicacion = queries[opcion_sql]
    
    st.subheader("💻 Código SQL Executed")
    st.code(query_str, language="sql")
    
    df_res = ejecutar_consulta_sql(query_str)
    
    st.subheader("📊 Resultado de la Consulta")
    st.dataframe(df_res, use_container_width=True)
    
    st.subheader("💡 Interpretación Analítica")
    st.info(explicacion)

# ------------------------------------------
# TAB 3: HISTORIAL DE EJECUCIONES
# ------------------------------------------
with tab3:
    st.header("⚡ Auditoría de Corridas de Scraper")
    
    df_ejec = ejecutar_consulta_sql("SELECT * FROM ejecuciones ORDER BY id DESC")
    if not df_ejec.empty:
        st.dataframe(df_ejec, use_container_width=True)
    else:
        st.write("No hay ejecuciones registradas en la tabla `ejecuciones`.")

# ------------------------------------------
# TAB 4: INFORMACIÓN DEL TALLER
# ------------------------------------------
with tab4:
    st.header("ℹ️ Ficha Técnica del Taller")
    st.markdown("""
    * **Asignatura:** Minería de Datos (Código 2016325)
    * **Institución:** Universidad Nacional de Colombia
    * **Categoría Asignada:** 10. Afeitado y Depilación
    * **Comercios Evaluados:** Farmatodo, Cruz Verde, Éxito
    * **Formato de Fecha:** ISO 8601 con zona horaria de Colombia (`America/Bogota`, UTC-5)
    * **Arquitectura de Software:** Python, Streamlit, SQLite, BeautifulSoup / REST APIs, Plotly.
    """)
