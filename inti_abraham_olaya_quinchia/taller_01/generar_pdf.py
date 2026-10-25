"""Genera taller_1.pdf ejecutando las celdas Python de taller_1.qmd (alternativa sin Quarto). Uso: python generar_pdf.py"""
import re, ast, io, sys, os, contextlib
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt, pandas as pd
sys.path.insert(0, os.getcwd())
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image, Preformatted, PageBreak
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
F, FB = "Helvetica", "Helvetica-Bold"
for p, pb in [("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")]:
    if os.path.exists(p):
        pdfmetrics.registerFont(TTFont("DV", p)); pdfmetrics.registerFont(TTFont("DVB", pb)); F, FB = "DV", "DVB"
ss = getSampleStyleSheet()
body = ParagraphStyle("b", parent=ss["BodyText"], fontName=F, fontSize=9, leading=12)
h1 = ParagraphStyle("h1", parent=ss["Heading1"], fontName=FB, fontSize=15, spaceBefore=10)
h2 = ParagraphStyle("h2", parent=ss["Heading2"], fontName=FB, fontSize=11)
mono = ParagraphStyle("m", fontName="Courier", fontSize=7.5, leading=9)
def esc(t): return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
def md(t):
    t = esc(t); t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t); t = re.sub(r"\*(.+?)\*", r"<i>\1</i>", t)
    return re.sub(r"`(.+?)`", r"<font face='Courier'>\1</font>", t)

src = open("taller_1.qmd", encoding="utf-8").read()
src = re.sub(r"^---\n.*?\n---\n", "", src, count=1, flags=re.S)
partes = re.split(r"```\{python\}\n(.*?)```", src, flags=re.S)
story = []
import sqlite3
_c = sqlite3.connect(os.path.join(os.path.dirname(os.path.abspath(__file__)), "comparador_precios.sqlite"))
_r = _c.execute("SELECT corrida_id FROM ejecuciones ORDER BY id DESC LIMIT 1").fetchone()[0]
_n = _c.execute("SELECT COUNT(DISTINCT producto_id), COUNT(*) FROM v_obs WHERE comparable=1 AND precio_cop IS NOT NULL AND corrida_id=?", (_r,)).fetchone()
NOTA = None if _n[0] >= 8 and _n[1] >= 24 else ("<b>ATENCION:</b> solo hay %d productos comparables (%d observaciones); el taller exige >=8 productos y >=24 observaciones en 3 comercios." % _n)
story += [Paragraph("Taller 1 - Comparador de precios: Afeitado y depilacion", ParagraphStyle("t", fontName=FB, fontSize=17, leading=21)),
          Paragraph("Mineria de Datos (2016325) - Universidad Nacional de Colombia - Inti Abraham Olaya Quinchia (1000240392)", body), Spacer(1, 6)]
if NOTA:
    t = Table([[Paragraph(NOTA, body)]], colWidths=[17 * cm]); t.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 1, colors.red)]))
    story += [t, Spacer(1, 8)]
g = {"__name__": "pdf"}; figs = []
_show = lambda *a, **k: (lambda b: (plt.savefig(b, format="png", dpi=110, bbox_inches="tight"), figs.append(b.getvalue()), plt.close("all")))(io.BytesIO())
plt.show = _show

def texto(t):
    for ln in t.strip().split("\n"):
        ln = ln.rstrip()
        if not ln or ln.startswith("|") or ln.startswith("```"): continue
        if ln.startswith("# "): story.append(Paragraph(md(ln[2:]), h1))
        elif ln.startswith("## "): story.append(Paragraph(md(ln[3:]), h2))
        elif ln.lstrip().startswith("- "): story.append(Paragraph("&bull; " + md(ln.lstrip()[2:]), body))
        elif ln.startswith("  "): story.append(Paragraph(md(ln.strip()), body))
        else: story.append(Paragraph(md(ln), body))

def tabla(df):
    df = df.head(30)
    data = [[str(c)[:18] for c in df.columns]] + [[("" if pd.isna(v) else (f"{v:,.2f}".rstrip("0").rstrip(".") if isinstance(v, float) else str(v)))[:26] for v in r] for r in df.itertuples(index=False)]
    tb = Table(data, repeatRows=1); tb.setStyle(TableStyle([("FONTNAME", (0, 0), (-1, -1), F), ("FONTSIZE", (0, 0), (-1, -1), 6.5),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e5e7eb")), ("GRID", (0, 0), (-1, -1), .25, colors.grey)])); return tb

for i, p in enumerate(partes):
    if i % 2 == 0: texto(p); continue
    oculto = "include: false" in p
    cod = "\n".join(l for l in p.split("\n") if not l.startswith("#|"))
    if "ejecutar_recoleccion_completa" in cod: story.append(Paragraph("<i>(Celda de scraping omitida en este PDF: se uso la base adjunta.)</i>", body)); continue
    tree = ast.parse(cod); last = None
    if tree.body and isinstance(tree.body[-1], ast.Expr):
        last = ast.Expression(tree.body.pop().value)
    buf = io.StringIO(); nf = len(figs); val = None
    with contextlib.redirect_stdout(buf):
        exec(compile(tree, "c", "exec"), g)
        if last is not None: val = eval(compile(last, "c", "eval"), g)
    if oculto: continue
    if buf.getvalue().strip(): story.append(Preformatted(buf.getvalue(), mono))
    if isinstance(val, pd.DataFrame): story += [tabla(val), Spacer(1, 6)] if len(val) else [Paragraph("<i>(sin filas)</i>", body)]
    for b in figs[nf:]:
        im = Image(io.BytesIO(b)); r = min(17 * cm / im.imageWidth, 12 * cm / im.imageHeight); im.drawWidth, im.drawHeight = im.imageWidth * r, im.imageHeight * r; story.append(im)
def san(x): return x
SimpleDocTemplate("taller_1.pdf", pagesize=A4, leftMargin=2*cm, rightMargin=2*cm, topMargin=1.8*cm, bottomMargin=1.8*cm).build(story)
print("ok", len(figs), "figuras")
