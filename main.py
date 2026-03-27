"""
main.py
Importa PDFs de PVsyst a la base de datos y registra la radiacion Solargis.

Uso:
    python main.py                              # procesa la carpeta 'pdfs/'
    python main.py ruta/carpeta                 # procesa otra carpeta
    python main.py --solargis CODIGO VALOR      # actualiza Solargis de un proyecto
"""

import sqlite3
import sys
from pathlib import Path

from db_manager import DB_PATH, get_all_simulations, init_db, insert_simulation, update_solargis
from extractor_pvsyst import extract_metrics, NotPVsystError

PDFS_DEFAULT = Path("pdfs")


# --- Migracion de esquema ------------------------------------------------------

def _fix_schema_if_needed() -> None:
    """Elimina la DB si tiene el esquema antiguo y esta vacia, para que init_db()
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


# --- Subcomando: actualizar Solargis -------------------------------------------

def cmd_solargis(codigo: str, valor_str: str) -> None:
    """Actualiza la radiacion Solargis para el proyecto indicado."""
    init_db()
    try:
        valor = float(valor_str.replace(",", "."))
    except ValueError:
        print(f"[!] Valor invalido: '{valor_str}'. Debe ser un numero (ej: 4.48).")
        sys.exit(1)

    sims = get_all_simulations()
    coincidencias = [
        s for s in sims
        if (s.get("project_code") or "").upper() == codigo.upper()
        or s["filename"].upper().startswith(codigo.upper())
    ]

    if not coincidencias:
        print(f"[!] No se encontro ningun proyecto con codigo '{codigo}'.")
        print("    Proyectos en la base de datos:")
        for s in sims:
            print(f"      - {s.get('project_code') or s['filename']}")
        sys.exit(1)

    for s in coincidencias:
        update_solargis(s["id"], valor)
        codigo_real = s.get("project_code") or s["filename"]
        print(f"    Solargis actualizado para {codigo_real}: {valor} kWh/m2/dia")

    print("\n    Ejecuta 'python exportar.py' para regenerar el Excel.\n")


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
        print(f"[i] Todos los PDFs ya estan importados ({len(pdfs)} en total).")
    else:
        print(f"Importando {len(nuevos)} PDF(s) nuevo(s)...\n")
        for pdf in nuevos:
            print(f"  -> {pdf.name}")
            try:
                metrics = extract_metrics(str(pdf))
                sim_id = insert_simulation(metrics, source_pdf_path=str(pdf))
                print(f"     Guardado con ID={sim_id}")
            except NotPVsystError:
                print(f"     [!] No parece ser un reporte de PVsyst, se omite.")
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
        print("Ingresa la Radiacion Solargis para cada proyecto.")
        print("(Presiona Enter para omitir y completarlo en otro momento)\n")
        for s in sin_solargis:
            codigo = s.get("project_code") or s["filename"]
            raw = input(f"  {codigo}  ->  Radiacion Solargis [kWh/m2/dia]: ").strip()
            if raw:
                try:
                    update_solargis(s["id"], float(raw.replace(",", ".")))
                except ValueError:
                    print("     [!] Valor invalido, se omite.")

    print(f"\n{'-' * 55}")
    print("OK Importacion completa.")
    print("   Ejecuta 'python exportar.py' para generar el Excel.\n")


# --- Punto de entrada ----------------------------------------------------------

if __name__ == "__main__":
    args = sys.argv[1:]

    # Subcomando: python main.py --solargis CODIGO VALOR
    if args and args[0] == "--solargis":
        if len(args) != 3:
            print("Uso: python main.py --solargis CODIGO_PROYECTO VALOR")
            print("Ej:  python main.py --solargis COLATLT119 4.48")
            sys.exit(1)
        cmd_solargis(args[1], args[2])

    # Modo normal: importar PDFs de una carpeta
    else:
        folder = Path(args[0]) if args else PDFS_DEFAULT
        if not folder.exists():
            folder.mkdir(parents=True)
            print(f"[i] Carpeta '{folder}/' creada.")
            print(f"    Coloca tus PDFs ahi y vuelve a ejecutar 'python main.py'.\n")
        else:
            process_folder(folder)
