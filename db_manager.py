"""
db_manager.py
Gestiona la base de datos SQLite local para almacenar simulaciones PVsyst.

Diseño relacional:
  ┌─────────────────────────┐       ┌──────────────────────────────┐
  │      simulations        │  1─n  │      monthly_production      │
  ├─────────────────────────┤       ├──────────────────────────────┤
  │ id (PK)                 │──────>│ id (PK)                      │
  │ filename                │       │ simulation_id (FK)           │
  │ imported_at             │       │ month                        │
  │ peak_power_kwp          │       │ e_grid_mwh                   │
  │ ── P50 ──               │       └──────────────────────────────┘
  │ p50_annual_energy_mwh   │
  │ p50_yearly_yield        │
  │ p50_daily_yield         │
  │ ── P75 ──               │
  │ p75_annual_energy_mwh   │
  │ p75_yearly_yield        │
  │ p75_daily_yield         │
  │ ── P90 ──               │
  │ p90_annual_energy_mwh   │
  │ p90_yearly_yield        │
  │ p90_daily_yield         │
  │ ── P99 ──               │
  │ p99_annual_energy_mwh   │
  │ p99_yearly_yield        │
  │ p99_daily_yield         │
  └─────────────────────────┘

Preparado para migración futura a PostgreSQL: solo cambia DB_URL
y reemplaza sqlite3 por psycopg2 / SQLAlchemy.
"""

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


# ─── Configuración ────────────────────────────────────────────────────────────

DB_PATH = Path("simulaciones.db")   # Cambiar por URL de PostgreSQL en producción


# ─── Context manager de conexión ──────────────────────────────────────────────

@contextmanager
def get_connection():
    """Abre y cierra la conexión garantizando commit o rollback automático."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row          # Acceso a columnas por nombre
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ─── Inicialización del esquema ────────────────────────────────────────────────

DDL_SIMULATIONS = """
CREATE TABLE IF NOT EXISTS simulations (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    filename                TEXT    NOT NULL,
    imported_at             TEXT    NOT NULL,          -- ISO-8601 UTC
    peak_power_kwp          REAL,

    -- P50
    p50_annual_energy_mwh   REAL,
    p50_yearly_yield        REAL,                      -- kWh/kWp/year
    p50_daily_yield         REAL,                      -- kWh/kWp/day

    -- P75 (opcional, puede ser NULL)
    p75_annual_energy_mwh   REAL,
    p75_yearly_yield        REAL,
    p75_daily_yield         REAL,

    -- P90
    p90_annual_energy_mwh   REAL,
    p90_yearly_yield        REAL,
    p90_daily_yield         REAL,

    -- P99
    p99_annual_energy_mwh   REAL,
    p99_yearly_yield        REAL,
    p99_daily_yield         REAL,

    UNIQUE(filename)                                   -- evita duplicados
);
"""

DDL_MONTHLY = """
CREATE TABLE IF NOT EXISTS monthly_production (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    simulation_id   INTEGER NOT NULL
                    REFERENCES simulations(id) ON DELETE CASCADE,
    month           TEXT    NOT NULL,                  -- 'january', 'february', …
    e_grid_mwh      REAL,

    UNIQUE(simulation_id, month)
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
    passed          INTEGER NOT NULL                   -- 1 = OK, 0 = FAIL
);
"""


def init_db() -> None:
    """Crea las tablas si no existen. Seguro de ejecutar múltiples veces."""
    with get_connection() as conn:
        conn.execute(DDL_SIMULATIONS)
        conn.execute(DDL_MONTHLY)
        conn.execute(DDL_VALIDATION)
    print(f"[DB] Base de datos inicializada en: {DB_PATH.resolve()}")


# ─── Inserción ────────────────────────────────────────────────────────────────

def insert_simulation(metrics: dict) -> int:
    """
    Inserta una simulación en la base de datos.

    Args:
        metrics: diccionario devuelto por extractor_pvsyst.extract_metrics()

    Returns:
        ID de la fila insertada (simulation_id).

    Raises:
        sqlite3.IntegrityError si el archivo ya fue importado.
    """
    now = datetime.now(timezone.utc).isoformat()

    def _val(prob: str, key: str):
        return metrics.get(prob, {}).get(key)

    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO simulations (
                filename, imported_at, peak_power_kwp,
                p50_annual_energy_mwh, p50_yearly_yield, p50_daily_yield,
                p75_annual_energy_mwh, p75_yearly_yield, p75_daily_yield,
                p90_annual_energy_mwh, p90_yearly_yield, p90_daily_yield,
                p99_annual_energy_mwh, p99_yearly_yield, p99_daily_yield
            ) VALUES (
                :filename, :imported_at, :peak_power_kwp,
                :p50_annual, :p50_yearly, :p50_daily,
                :p75_annual, :p75_yearly, :p75_daily,
                :p90_annual, :p90_yearly, :p90_daily,
                :p99_annual, :p99_yearly, :p99_daily
            )
            """,
            {
                "filename":       metrics["filename"],
                "imported_at":    now,
                "peak_power_kwp": metrics.get("peak_power_kwp"),
                "p50_annual":     _val("p50", "annual_energy_mwh"),
                "p50_yearly":     _val("p50", "yearly_yield_kwh_kwp"),
                "p50_daily":      _val("p50", "daily_yield_kwh_kwp"),
                "p75_annual":     _val("p75", "annual_energy_mwh"),
                "p75_yearly":     _val("p75", "yearly_yield_kwh_kwp"),
                "p75_daily":      _val("p75", "daily_yield_kwh_kwp"),
                "p90_annual":     _val("p90", "annual_energy_mwh"),
                "p90_yearly":     _val("p90", "yearly_yield_kwh_kwp"),
                "p90_daily":      _val("p90", "daily_yield_kwh_kwp"),
                "p99_annual":     _val("p99", "annual_energy_mwh"),
                "p99_yearly":     _val("p99", "yearly_yield_kwh_kwp"),
                "p99_daily":      _val("p99", "daily_yield_kwh_kwp"),
            },
        )
        sim_id = cursor.lastrowid

        # Insertar producción mensual
        monthly = metrics.get("e_grid_monthly_mwh", {})
        rows = [
            (sim_id, month, value)
            for month, value in monthly.items()
            if value is not None
        ]
        if rows:
            conn.executemany(
                "INSERT INTO monthly_production (simulation_id, month, e_grid_mwh) "
                "VALUES (?, ?, ?)",
                rows,
            )

    print(f"[DB] Insertada simulación '{metrics['filename']}' con ID={sim_id}")
    return sim_id


# ─── Consultas de utilidad ────────────────────────────────────────────────────

def get_all_simulations() -> list[dict]:
    """Devuelve todas las simulaciones como lista de diccionarios."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM simulations ORDER BY imported_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def get_monthly(simulation_id: int) -> list[dict]:
    """Devuelve la producción mensual de una simulación."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT month, e_grid_mwh FROM monthly_production "
            "WHERE simulation_id = ? ORDER BY rowid",
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
            """
            INSERT INTO validation_log
                (simulation_id, validated_at, check_name, expected, actual,
                 tolerance_pct, passed)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (simulation_id, now, check_name, expected, actual,
             tolerance_pct, int(passed)),
        )


# ─── CLI rápido ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    init_db()

    sims = get_all_simulations()
    if sims:
        print(f"\n[DB] {len(sims)} simulación(es) en la base de datos:")
        for s in sims:
            print(f"  ID={s['id']}  {s['filename']}  "
                  f"P50={s['p50_annual_energy_mwh']} MWh  "
                  f"importado={s['imported_at']}")
    else:
        print("[DB] La base de datos está vacía. Ejecuta main.py para importar PDFs.")
