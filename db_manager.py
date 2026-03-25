"""
db_manager.py
Gestiona la base de datos SQLite local para el pipeline de simulaciones PVsyst.

Diseño relacional:
  simulations         → una fila por PDF/simulación (métricas anuales P50/P75/P90/P99)
  monthly_production  → 12 filas por simulación (distribución mensual + proporción estacional)
  validation_log      → registro de chequeos de corroboración

Preparado para migración a PostgreSQL: solo cambia DB_PATH por una URL y
reemplaza sqlite3 por psycopg2 / SQLAlchemy.
"""

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


# ─── Configuración ─────────────────────────────────────────────────────────────

DB_PATH = Path("simulaciones.db")

# Si P75 no está en el PDF, se estima como P50 * P75_FACTOR
P75_FACTOR = 0.95


# ─── Context manager de conexión ───────────────────────────────────────────────

@contextmanager
def get_connection():
    """Abre y cierra la conexión garantizando commit o rollback automático."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ─── DDL ───────────────────────────────────────────────────────────────────────

DDL_SIMULATIONS = """
CREATE TABLE IF NOT EXISTS simulations (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    filename                    TEXT    NOT NULL,
    project_code                TEXT,
    imported_at                 TEXT    NOT NULL,           -- ISO-8601 UTC
    source_pdf_path             TEXT,
    peak_power_kwp              REAL,

    -- Datos Solargis (se cargan con update_solargis() si se dispone del dato)
    solargis_daily_irradiation  REAL,                      -- kWh/m²/día
    ratio_yield_irradiation     REAL,                      -- P90 diario / Solargis

    -- P50
    p50_annual_energy_mwh       REAL,
    p50_yearly_yield            REAL,                      -- kWh/kWp/año
    p50_daily_yield             REAL,                      -- kWh/kWp/día

    -- P75 (real si viene del PDF; estimado como P50*P75_FACTOR si no)
    p75_annual_energy_mwh       REAL,
    p75_yearly_yield            REAL,
    p75_daily_yield             REAL,
    p75_is_estimated            INTEGER NOT NULL DEFAULT 1, -- 1 = supuesto, 0 = real

    -- P90
    p90_annual_energy_mwh       REAL,
    p90_yearly_yield            REAL,
    p90_daily_yield             REAL,

    -- P99
    p99_annual_energy_mwh       REAL,
    p99_yearly_yield            REAL,
    p99_daily_yield             REAL,

    UNIQUE(filename)                                        -- evita duplicados
);
"""

DDL_MONTHLY = """
CREATE TABLE IF NOT EXISTS monthly_production (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    simulation_id       INTEGER NOT NULL
                        REFERENCES simulations(id) ON DELETE CASCADE,
    month_number        INTEGER NOT NULL,                  -- 1 = enero … 12 = diciembre
    month_name          TEXT    NOT NULL,                  -- 'enero', 'febrero', ...

    -- Proporción estacional (E_Grid P50 mensual / E_Grid P50 anual)
    monthly_proportion  REAL,

    -- Producción mensual en kWh por probabilidad
    p50_kwh             REAL,
    p75_kwh             REAL,
    p75_is_estimated    INTEGER NOT NULL DEFAULT 1,
    p90_kwh             REAL,
    p99_kwh             REAL,

    UNIQUE(simulation_id, month_number)
);
"""

DDL_VALIDATION = """
CREATE TABLE IF NOT EXISTS validation_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    simulation_id   INTEGER NOT NULL
                    REFERENCES simulations(id) ON DELETE CASCADE,
    validated_at    TEXT    NOT NULL,
    check_name      TEXT    NOT NULL,
    expected        REAL,
    actual          REAL,
    tolerance_pct   REAL,
    passed          INTEGER NOT NULL                       -- 1 = OK, 0 = FAIL
);
"""

MONTHS_MAP = [
    (1, "january", "enero"),
    (2, "february", "febrero"),
    (3, "march", "marzo"),
    (4, "april", "abril"),
    (5, "may", "mayo"),
    (6, "june", "junio"),
    (7, "july", "julio"),
    (8, "august", "agosto"),
    (9, "september", "septiembre"),
    (10, "october", "octubre"),
    (11, "november", "noviembre"),
    (12, "december", "diciembre"),
]


# ─── Inicialización ────────────────────────────────────────────────────────────

def init_db() -> None:
    """Crea las tablas si no existen. Seguro de ejecutar múltiples veces."""
    with get_connection() as conn:
        conn.execute(DDL_SIMULATIONS)
        conn.execute(DDL_MONTHLY)
        conn.execute(DDL_VALIDATION)
    print(f"[DB] Base de datos lista: {DB_PATH.resolve()}")


# ─── P75 helper ────────────────────────────────────────────────────────────────

def _resolve_p75(metrics: dict) -> tuple[dict, bool]:
    """
    Devuelve (p75_dict, is_estimated).
    Si P75 no viene en el PDF, lo estima como P50 * P75_FACTOR.
    """
    p75 = metrics.get("p75", {})
    if p75.get("annual_energy_mwh") is not None:
        return p75, False

    p50 = metrics.get("p50", {})
    p50_annual  = p50.get("annual_energy_mwh") or 0
    p50_yearly  = p50.get("yearly_yield_kwh_kwp") or 0
    p50_daily   = p50.get("daily_yield_kwh_kwp") or 0

    estimated = {
        "annual_energy_mwh":    round(p50_annual  * P75_FACTOR, 2) if p50_annual  else None,
        "yearly_yield_kwh_kwp": round(p50_yearly  * P75_FACTOR, 2) if p50_yearly  else None,
        "daily_yield_kwh_kwp":  round(p50_daily   * P75_FACTOR, 4) if p50_daily   else None,
    }
    return estimated, True


# ─── Inserción ─────────────────────────────────────────────────────────────────

def insert_simulation(metrics: dict, source_pdf_path: str = None) -> int:
    """
    Inserta una simulación completa en la base de datos.

    Args:
        metrics:         dict devuelto por extractor_pvsyst.extract_metrics()
        source_pdf_path: ruta original del PDF (para trazabilidad)

    Returns:
        ID de la fila creada en 'simulations'.

    Raises:
        sqlite3.IntegrityError si el archivo ya fue importado.
    """
    now = datetime.now(timezone.utc).isoformat()
    p75, p75_estimated = _resolve_p75(metrics)

    def _val(d: dict, key: str):
        return d.get(key)

    p50 = metrics.get("p50", {})
    p90 = metrics.get("p90", {})
    p99 = metrics.get("p99", {})

    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO simulations (
                filename, project_code, imported_at, source_pdf_path, peak_power_kwp,
                solargis_daily_irradiation, ratio_yield_irradiation,
                p50_annual_energy_mwh, p50_yearly_yield, p50_daily_yield,
                p75_annual_energy_mwh, p75_yearly_yield, p75_daily_yield, p75_is_estimated,
                p90_annual_energy_mwh, p90_yearly_yield, p90_daily_yield,
                p99_annual_energy_mwh, p99_yearly_yield, p99_daily_yield
            ) VALUES (
                :filename, :project_code, :imported_at, :source_pdf_path, :peak_power_kwp,
                NULL, NULL,
                :p50_annual, :p50_yearly, :p50_daily,
                :p75_annual, :p75_yearly, :p75_daily, :p75_estimated,
                :p90_annual, :p90_yearly, :p90_daily,
                :p99_annual, :p99_yearly, :p99_daily
            )
            """,
            {
                "filename":        metrics["filename"],
                "project_code":    _guess_project_code(metrics["filename"]),
                "imported_at":     now,
                "source_pdf_path": source_pdf_path,
                "peak_power_kwp":  metrics.get("peak_power_kwp"),
                "p50_annual":      _val(p50, "annual_energy_mwh"),
                "p50_yearly":      _val(p50, "yearly_yield_kwh_kwp"),
                "p50_daily":       _val(p50, "daily_yield_kwh_kwp"),
                "p75_annual":      _val(p75, "annual_energy_mwh"),
                "p75_yearly":      _val(p75, "yearly_yield_kwh_kwp"),
                "p75_daily":       _val(p75, "daily_yield_kwh_kwp"),
                "p75_estimated":   int(p75_estimated),
                "p90_annual":      _val(p90, "annual_energy_mwh"),
                "p90_yearly":      _val(p90, "yearly_yield_kwh_kwp"),
                "p90_daily":       _val(p90, "daily_yield_kwh_kwp"),
                "p99_annual":      _val(p99, "annual_energy_mwh"),
                "p99_yearly":      _val(p99, "yearly_yield_kwh_kwp"),
                "p99_daily":       _val(p99, "daily_yield_kwh_kwp"),
            },
        )
        sim_id = cursor.lastrowid
        _insert_monthly(conn, sim_id, metrics, p75, p75_estimated)

    print(f"[DB] Simulacion '{metrics['filename']}' guardada con ID={sim_id}"
          f" (P75 {'estimado' if p75_estimated else 'real'})")
    return sim_id


def _insert_monthly(conn, sim_id: int, metrics: dict,
                    p75: dict, p75_estimated: bool):
    """Inserta 12 filas en monthly_production con proporciones y P50/P75/P90/P99."""
    e_grid = metrics.get("e_grid_monthly_mwh", {})  # valores en MWh del PDF

    p50_annual = (metrics.get("p50", {}).get("annual_energy_mwh") or 0) * 1000  # → kWh
    p75_annual = (p75.get("annual_energy_mwh") or 0) * 1000
    p90_annual = (metrics.get("p90", {}).get("annual_energy_mwh") or 0) * 1000
    p99_annual = (metrics.get("p99", {}).get("annual_energy_mwh") or 0) * 1000

    rows = []
    for num, eng_name, esp_name in MONTHS_MAP:
        p50_kwh = (e_grid.get(eng_name) or 0) * 1000  # MWh → kWh
        proportion = (p50_kwh / p50_annual) if p50_annual and p50_kwh else None

        # Distribuir P75/P90/P99 con la misma proporción estacional que P50
        p75_kwh = round(p75_annual * proportion, 2) if proportion and p75_annual else None
        p90_kwh = round(p90_annual * proportion, 2) if proportion and p90_annual else None
        p99_kwh = round(p99_annual * proportion, 2) if proportion and p99_annual else None

        rows.append((
            sim_id, num, esp_name,
            round(proportion, 6) if proportion else None,
            p50_kwh or None,
            p75_kwh, int(p75_estimated),
            p90_kwh, p99_kwh,
        ))

    conn.executemany(
        """INSERT INTO monthly_production
           (simulation_id, month_number, month_name, monthly_proportion,
            p50_kwh, p75_kwh, p75_is_estimated, p90_kwh, p99_kwh)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )


# ─── Actualización Solargis ────────────────────────────────────────────────────

def update_solargis(sim_id: int, solargis_daily: float) -> None:
    """
    Carga la irradiación Solargis y recalcula el ratio P90_diario/Solargis
    para una simulación ya guardada.
    """
    with get_connection() as conn:
        row = conn.execute(
            "SELECT p90_daily_yield FROM simulations WHERE id = ?", (sim_id,)
        ).fetchone()
        if not row or not row["p90_daily_yield"]:
            print(f"[DB] Simulacion ID={sim_id} no tiene p90_daily_yield.")
            return
        ratio = round(row["p90_daily_yield"] / solargis_daily, 5)
        conn.execute(
            """UPDATE simulations
               SET solargis_daily_irradiation = ?, ratio_yield_irradiation = ?
               WHERE id = ?""",
            (solargis_daily, ratio, sim_id),
        )
    print(f"[DB] Solargis actualizado para ID={sim_id}: {solargis_daily} kWh/m2/dia, ratio={ratio}")


# ─── Consultas ─────────────────────────────────────────────────────────────────

def get_all_simulations() -> list[dict]:
    """Devuelve todas las simulaciones como lista de diccionarios."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM simulations ORDER BY imported_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def get_monthly(simulation_id: int) -> list[dict]:
    """Devuelve la producción mensual de una simulación (12 filas)."""
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT month_number, month_name, monthly_proportion,
                      p50_kwh, p75_kwh, p75_is_estimated, p90_kwh, p99_kwh
               FROM monthly_production
               WHERE simulation_id = ?
               ORDER BY month_number""",
            (simulation_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def log_validation(simulation_id: int, check_name: str,
                   expected: float, actual: float,
                   tolerance_pct: float, passed: bool) -> None:
    """Registra el resultado de una validación en validation_log."""
    now = datetime.now(timezone.utc).isoformat()
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO validation_log
               (simulation_id, validated_at, check_name, expected, actual,
                tolerance_pct, passed)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (simulation_id, now, check_name, expected, actual,
             tolerance_pct, int(passed)),
        )


# ─── Helpers internos ──────────────────────────────────────────────────────────

def _guess_project_code(filename: str) -> str:
    """Extrae el código de proyecto del nombre del archivo.
    Ej: 'COLATLT119-1760.pdf' → 'COLATLT119'
    """
    stem = Path(filename).stem
    return stem.split("-")[0] if "-" in stem else stem


# ─── CLI rápida ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    init_db()

    sims = get_all_simulations()
    if not sims:
        print("[DB] Base de datos vacia. Ejecuta main.py para importar PDFs.")
    else:
        print(f"\n[DB] {len(sims)} simulacion(es) registradas:\n")
        for s in sims:
            p75_tag = "estimado" if s["p75_is_estimated"] else "real"
            print(
                f"  [{s['id']:>3}] {s['project_code']:<15} "
                f"P50={s['p50_annual_energy_mwh']:>7.1f} MWh  "
                f"P90={s['p90_annual_energy_mwh']:>7.1f} MWh  "
                f"P99={s['p99_annual_energy_mwh']:>7.1f} MWh  "
                f"P75={s['p75_annual_energy_mwh']:>7.1f} MWh ({p75_tag})"
            )
