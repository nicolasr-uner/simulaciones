"""
main.py
Importa PDFs de PVsyst a la base de datos y registra la radiación Solargis.

Uso:
    python main.py               # procesa la carpeta 'pdfs/'
    python main.py ruta/carpeta  # procesa otra carpeta
"""

import sqlite3
import sys
from pathlib import Path

from db_manager import DB_PATH, get_all_simulations, init_db, insert_simulation, update_solargis
from extractor_pvsyst import extract_metrics

PDFS_DEFAULT = Path("pdfs")


# --- Migración de esquema ------------------------------------------------------

def _fix_schema_if_needed() -> None:
    """Elimina la DB si tiene el esquema antiguo y está vacía, para que init_db()
    la recree con el esquema correcto."""
    if not DB_PATH.exists():
        return
    conn = sqlite3.connect(DB_PATH)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(simulations)")}
    n_rows = conn.execute("SELECT COUNT(*) FROM simulations").fetchone()[0]
    conn.close()
    if "project_code" not in cols and n_rows == 0:
        DB_PATH.unlink()
        print("[DB] Esquema desactualizado detectado -> base de datos reiniciada.\n")


# --- Pipeline principal --------------------------------------------------------

def process_folder(folder: Path) -> None:
    _fix_schema_if_needed()
    init_db()

    pdfs = sorted(folder.glob("*.pdf"))
    if not pdfs:
        print(f"[!] No se encontraron PDFs en '{folder}'.")
        return

    already = {s["filename"] for s in get_all_simulations()}
    nuevos = [p for p in pdfs if p.name not in already]

    # -- Importar PDFs nuevos ---------------------------------------------------
    if not nuevos:
        print(f"[i] Todos los PDFs ya están importados ({len(pdfs)} en total).")
    else:
        print(f"Importando {len(nuevos)} PDF(s) nuevo(s)...\n")
        for pdf in nuevos:
            print(f"  -> {pdf.name}")
            try:
                metrics = extract_metrics(str(pdf))
                sim_id = insert_simulation(metrics, source_pdf_path=str(pdf))
                print(f"     Guardado con ID={sim_id}")
            except sqlite3.IntegrityError:
                print("     [!] Ya existe en la base de datos, se omite.")
            except Exception as exc:
                print(f"     [ERROR] {exc}")

    # -- Solicitar Solargis para simulaciones que no lo tienen ------------------
    sin_solargis = [
        s for s in get_all_simulations()
        if s.get("solargis_daily_irradiation") is None
    ]

    if sin_solargis:
        print(f"\n{'-' * 55}")
        print("Ingresá la Radiación Solargis para cada proyecto.")
        print("(Presioná Enter para omitir y completarlo en otro momento)\n")
        for s in sin_solargis:
            codigo = s.get("project_code") or s["filename"]
            raw = input(f"  {codigo}  ->  Radiacion Solargis [kWh/m2/dia]: ").strip()
            if raw:
                try:
                    update_solargis(s["id"], float(raw.replace(",", ".")))
                except ValueError:
                    print("     [!] Valor inválido, se omite.")

    print(f"\n{'-' * 55}")
    print("OK Importacion completa.")
    print("   Ejecuta 'python exportar.py' para generar el Excel.\n")


# --- Punto de entrada ----------------------------------------------------------

if __name__ == "__main__":
    folder = Path(sys.argv[1]) if len(sys.argv) > 1 else PDFS_DEFAULT
    if not folder.exists():
        folder.mkdir(parents=True)
        print(f"[i] Carpeta '{folder}/' creada.")
        print(f"    Colocá tus PDFs ahí y volvé a ejecutar 'python main.py'.\n")
    else:
        process_folder(folder)
