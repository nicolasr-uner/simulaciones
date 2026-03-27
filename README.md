# simulaciones

Pipeline de automatización para análisis de simulaciones PVsyst.
Extrae métricas de PDFs de PVsyst, las almacena en una base de datos SQLite
y genera un reporte Excel con el análisis P50/P75/P90/P99.

## Requisitos

```bash
pip install -r requirements.txt
```

## Uso

### 1. Importar PDFs

Colocá los PDFs de PVsyst en la carpeta `pdfs/` y ejecutá:

```bash
python main.py
```

El script:
- Procesa todos los PDFs nuevos de la carpeta `pdfs/`
- Omite los que ya fueron importados anteriormente
- Pide la radiación Solargis (kWh/m²/día) para cada proyecto nuevo

También podés apuntar a otra carpeta:

```bash
python main.py ruta/a/otra/carpeta
```

### 2. Actualizar la radiación Solargis

Si omitiste el dato Solargis al importar, o necesitás corregirlo:

```bash
python main.py --solargis CODIGO_PROYECTO VALOR
```

Ejemplo:

```bash
python main.py --solargis COLATLT119 4.48
```

### 3. Generar el reporte Excel

```bash
python exportar.py
```

Genera `reporte_simulaciones.xlsx` con una hoja por proyecto,
en el mismo formato que `Ejemplo.xlsx`.

También podés especificar el nombre de salida:

```bash
python exportar.py mi_reporte.xlsx
```

## Estructura del proyecto

```
simulaciones/
├── pdfs/                      # PDFs de PVsyst a procesar (no versionado)
├── extractor_pvsyst.py        # Extrae métricas del PDF
├── db_manager.py              # Gestiona la base de datos SQLite
├── main.py                    # Importador de PDFs + carga de Solargis
├── exportar.py                # Generador del reporte Excel
├── Ejemplo.xlsx               # Formato de referencia esperado
├── requirements.txt           # Dependencias Python
└── simulaciones.db            # Base de datos local (no versionado)
```

## Flujo completo

```
PDFs de PVsyst
      |
  main.py  <-- ingreso de Solargis por proyecto
      |
 SQLite DB
      |
 exportar.py
      |
reporte_simulaciones.xlsx
```
