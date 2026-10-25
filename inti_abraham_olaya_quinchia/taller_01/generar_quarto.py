"""
generar_quarto.py - Renderiza taller_1.qmd a HTML (y PDF si Quarto/LaTeX lo permiten).

Uso (desde la carpeta taller_01, con el .venv activo):
    python generar_quarto.py              # HTML + PDF
    python generar_quarto.py --solo-html  # solo HTML
    python generar_quarto.py --online     # hace scraping en vivo antes de renderizar
"""
import argparse
import os
import shutil
import subprocess
import sys

QMD = "taller_1.qmd"


def ejecutar(cmd, env=None):
    print("\n>>", " ".join(cmd))
    return subprocess.run(cmd, env=env).returncode == 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--solo-html", action="store_true", help="no generar PDF")
    ap.add_argument("--online", action="store_true", help="scraping en vivo al renderizar")
    a = ap.parse_args()

    # Ir a la carpeta donde está este script (el .qmd importa scraper.py desde ahí)
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    for f in (QMD, "scraper.py"):
        if not os.path.exists(f):
            sys.exit(f"ERROR: no encuentro {f} en {os.getcwd()}")

    if shutil.which("quarto") is None:
        sys.exit("ERROR: Quarto no está instalado. Descárgalo de https://quarto.org/docs/get-started "
                 "y reinicia la terminal.")

    env = os.environ.copy()
    env["TALLER_MODO"] = "online" if a.online else "offline"
    env["PYTHONIOENCODING"] = "utf-8"

    ok_html = ejecutar(["quarto", "render", QMD, "--to", "html"], env)
    if not ok_html:
        sys.exit("\nFallo al generar el HTML. Revisa el error de arriba (suele ser un paquete de Python "
                 "faltante: pip install -r requirements.txt).")
    print("\nHTML generado: taller_1.html")

    if a.solo_html:
        return

    ok_pdf = ejecutar(["quarto", "render", QMD, "--to", "pdf"], env)
    if ok_pdf:
        print("\nPDF generado: taller_1.pdf")
    else:
        print("\nNo se pudo generar el PDF directo con Quarto. Causa habitual: falta LaTeX.")
        print("  Opción 1: quarto install tinytex   (y vuelve a ejecutar este script)")
        print("  Opción 2: abre taller_1.html en el navegador y usa Imprimir > Guardar como PDF.")
        print("  Opción 3: python generar_pdf.py    (PDF sin Quarto)")


if __name__ == "__main__":
    main()