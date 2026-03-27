"""
extractor_pvsyst.py
Extrae métricas clave de reportes PDF generados por PVsyst.

Datos extraídos por probabilidad (P50, P75, P90, P99):
  - Producción específica diaria  (daily_yield_kwh_kwp)
  - Producción específica anual   (yearly_yield_kwh_kwp)
  - Producción de energía anual   (annual_energy_mwh)

Datos adicionales:
  - Potencia pico del sistema     (peak_power_kwp)
  - Producción mensual E_Grid     (e_grid_monthly_mwh)
"""

import re
import sys
import json
from pathlib import Path

import pdfplumber


class NotPVsystError(ValueError):
    """Se lanza cuando el PDF no parece ser un reporte válido de PVsyst."""


MONTHS = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]


def _parse_float(value: str) -> float:
    """Convierte string numérico a float (maneja coma como separador de miles)."""
    return float(value.replace(",", ""))


def _extract_full_text(pdf_path: str) -> str:
    """Lee todas las páginas del PDF y devuelve el texto completo concatenado."""
    full_text = ""
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            full_text += text + "\n"
    return full_text


def _extract_peak_power(text: str) -> float | None:
    """Extrae la potencia pico instalada en kWp."""
    # Formato portada: "System power: 1321 kWp"
    m = re.search(r"System\s+power:\s*([\d,.]+)\s*kWp", text)
    if m:
        return _parse_float(m.group(1))
    # Formato alternativo: "Pnom total 1321kWp"
    m = re.search(r"Pnom\s+total\s*([\d,.]+)kWp", text)
    if m:
        return _parse_float(m.group(1))
    return None


def _extract_probability_metrics(text: str, prob: str) -> dict:
    """
    Extrae métricas de producción para una probabilidad dada (50, 75, 90, 99).
    Busca en la sección 'Main results' (página 7) y en 'P50-P90 evaluation' (página 10).
    """
    result = {
        "annual_energy_mwh": None,
        "yearly_yield_kwh_kwp": None,
        "daily_yield_kwh_kwp": None,
    }

    # --- Energía anual ---
    # Formato preciso (página 7): "Produced Energy (P50) 2630.2MWh/year"
    m = re.search(
        rf"Produced\s+Energy\s+\(P{prob}\)\s*([\d,.]+)\s*MWh/year",
        text,
        re.IGNORECASE,
    )
    if m:
        result["annual_energy_mwh"] = _parse_float(m.group(1))

    # Formato redondeado (página 10): "P50 2630MWh"  (solo si no se encontró antes)
    if result["annual_energy_mwh"] is None:
        m = re.search(
            rf"(?<!\w)P{prob}\b\s+([\d,.]+)\s*MWh(?!/year)",
            text,
            re.IGNORECASE,
        )
        if m:
            result["annual_energy_mwh"] = _parse_float(m.group(1))

    # --- Producción específica anual ---
    # Formato (página 7): "Specific production (P50) 1991kWh/kWp/year"
    m = re.search(
        rf"Specific\s+production\s+\(P{prob}\)\s*([\d,.]+)\s*kWh/kWp/year",
        text,
        re.IGNORECASE,
    )
    if m:
        result["yearly_yield_kwh_kwp"] = _parse_float(m.group(1))

    # --- Producción específica diaria (derivada) ---
    if result["yearly_yield_kwh_kwp"] is not None:
        result["daily_yield_kwh_kwp"] = round(result["yearly_yield_kwh_kwp"] / 365, 4)

    return result


def _extract_monthly_egrid(text: str) -> dict:
    """
    Extrae la producción mensual E_Grid (MWh) de la tabla 'Balances and main results'.

    Formato de cada fila (columnas: GlobHor DiffHor T_Amb GlobInc GlobEff EArray E_Grid PR):
      January 169.4 58.37 26.33 216.1 207.3 256.7 228.1 0.799
    """
    monthly = {m.lower(): None for m in MONTHS}

    pattern = re.compile(
        r"^("
        + "|".join(MONTHS)
        + r")"
        + r"\s+[\d.]+\s+[\d.]+\s+[\d.]+\s+[\d.]+\s+[\d.]+\s+[\d.]+\s+([\d.]+)\s+[\d.]+",
        re.MULTILINE,
    )

    for match in pattern.finditer(text):
        month_name = match.group(1).lower()
        monthly[month_name] = float(match.group(2))

    return monthly


def extract_metrics(pdf_path: str) -> dict:
    """
    Función principal. Recibe la ruta a un PDF de PVsyst y devuelve un
    diccionario con todas las métricas extraídas.

    Returns:
        {
          "filename": str,
          "peak_power_kwp": float | None,
          "p50": {"annual_energy_mwh", "yearly_yield_kwh_kwp", "daily_yield_kwh_kwp"},
          "p75": {...},
          "p90": {...},
          "p99": {...},
          "e_grid_monthly_mwh": {"january": float, ..., "december": float}
        }
    """
    path = Path(pdf_path)
    if not path.exists():
        raise FileNotFoundError(f"PDF no encontrado: {pdf_path}")

    text = _extract_full_text(str(path))

    metrics = {
        "filename": path.name,
        "peak_power_kwp": _extract_peak_power(text),
        "p50": _extract_probability_metrics(text, "50"),
        "p75": _extract_probability_metrics(text, "75"),
        "p90": _extract_probability_metrics(text, "90"),
        "p99": _extract_probability_metrics(text, "99"),
        "e_grid_monthly_mwh": _extract_monthly_egrid(text),
    }

    # Validar que el PDF tenga al menos los campos mínimos esperados de PVsyst
    p50_ok = metrics["p50"]["annual_energy_mwh"] is not None
    monthly_ok = any(v is not None for v in metrics["e_grid_monthly_mwh"].values())
    if not p50_ok and not monthly_ok:
        raise NotPVsystError(
            f"'{path.name}' no contiene datos de PVsyst reconocibles "
            "(no se encontró producción P50 ni tabla mensual E_Grid)."
        )

    return metrics


if __name__ == "__main__":
    pdf_file = sys.argv[1] if len(sys.argv) > 1 else "datos_prueba/COLATLT119-1760.pdf"

    print(f"\nProcesando: {pdf_file}\n{'='*50}")
    result = extract_metrics(pdf_file)
    print(json.dumps(result, indent=2, ensure_ascii=False))

    # Resumen legible en consola
    print(f"\n{'='*50}")
    print(f"Proyecto : {result['filename']}")
    print(f"Potencia : {result['peak_power_kwp']} kWp")
    print()
    for prob in ["p50", "p75", "p90", "p99"]:
        data = result[prob]
        if data["annual_energy_mwh"] is not None:
            print(
                f"  {prob.upper()}  "
                f"Energía anual: {data['annual_energy_mwh']:>8.1f} MWh  |  "
                f"Yield anual: {data['yearly_yield_kwh_kwp']:>6.0f} kWh/kWp  |  "
                f"Yield diario: {data['daily_yield_kwh_kwp']:.4f} kWh/kWp"
            )
        else:
            print(f"  {prob.upper()}  (no encontrado en el PDF)")

    print()
    print("E_Grid mensual (MWh):")
    for month, value in result["e_grid_monthly_mwh"].items():
        if value is not None:
            print(f"  {month.capitalize():>10}: {value:>8.1f} MWh")
