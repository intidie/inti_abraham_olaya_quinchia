"""App Streamlit (opcional) - lee comparador_precios.sqlite con el esquema v2 (vista v_obs)."""
import os
import sqlite3

import pandas as pd
import plotly.express as px
import streamlit as st

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "comparador_precios.sqlite")
st.set_page_config(page_title="Comparador - Afeitado y depilación", page_icon="🛒", layout="wide")
st.title("🛒 Comparador de precios: Afeitado y depilación")
st.caption("Taller 1 · Minería de Datos · Universidad Nacional de Colombia. Los precios son de la fecha de captura.")


@st.cache_data(ttl=60)
def q(sql, params=()):
    if not os.path.exists(DB):
        return pd.DataFrame()
    con = sqlite3.connect(DB)
    try:
        return pd.read_sql_query(sql, con, params=params)
    finally:
        con.close()


ejec = q("SELECT * FROM ejecuciones ORDER BY id DESC")
if ejec.empty:
    st.warning("No hay datos. Ejecute `python scraper.py` para generar la base.")
    st.stop()

corrida = st.sidebar.selectbox("Corrida de recolección", ejec["corrida_id"].unique())
solo_comp = st.sidebar.checkbox("Solo productos comparables (en todos los comercios)", True)
df = q("SELECT * FROM v_obs WHERE corrida_id=? AND precio_cop IS NOT NULL AND excluido=0", (corrida,))
if solo_comp:
    df = df[df["comparable"] == 1]
texto = st.sidebar.text_input("Buscar por nombre")
if texto:
    df = df[df["nombre_estandar"].str.contains(texto, case=False, na=False)]

t1, t2, t3 = st.tabs(["Comparador", "Ahorro y precio por unidad", "Ejecuciones"])
with t1:
    if df.empty:
        st.info("Sin datos para los filtros elegidos.")
    else:
        st.plotly_chart(px.bar(df, x="nombre_estandar", y="precio_cop", color="comercio", barmode="group",
                               labels={"precio_cop": "Precio (COP)", "nombre_estandar": "Producto"}),
                        use_container_width=True)
        st.dataframe(df[["comercio", "nombre_estandar", "marca_estandar", "precio_cop", "en_promocion", "url"]],
                     column_config={"url": st.column_config.LinkColumn("Enlace")}, hide_index=True)
with t2:
    c = df[df["comparable"] == 1]
    if not c.empty:
        a = c.groupby("producto_id").agg(producto=("nombre_estandar", "first"), pmin=("precio_cop", "min"),
                                         pmax=("precio_cop", "max"))
        a["ahorro_cop"] = a["pmax"] - a["pmin"]
        a["ahorro_pct"] = (100 * a["ahorro_cop"] / a["pmax"]).round(1)
        st.dataframe(a.sort_values("ahorro_pct", ascending=False))
    u = df[df["contenido_total"] > 0].assign(precio_unidad=lambda d: d["precio_cop"] / d["contenido_total"])
    if not u.empty:
        unidad = st.selectbox("Unidad", sorted(u["unidad_base"].dropna().unique()))
        st.dataframe(u[u["unidad_base"] == unidad].sort_values("precio_unidad")[
            ["comercio", "nombre_estandar", "contenido_total", "precio_cop", "precio_unidad"]], hide_index=True)
with t3:
    st.dataframe(ejec, hide_index=True)
