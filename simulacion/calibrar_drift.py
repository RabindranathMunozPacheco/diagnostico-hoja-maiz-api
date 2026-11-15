"""Calibra los umbrales del monitor de drift con tráfico normal y con tráfico de lluvia (sin levantar nada).

Simula muchas ventanas como las del vigilante (570 fotos, lo que llega en 5 min a 2 fotos por segundo) sacadas
al azar de las 181 fotos de tráfico y calcula el PSI contra la línea base de la versión 1:
    1. con tráfico normal: cuánto sube el PSI sin que haya drift (falsas alarmas), con bins finos y con bins
       agrupados hasta tener al menos 5 % de la línea base cada uno
    2. con una fracción creciente de fotos en condición de lluvia: desde qué fracción se dispara la alerta
Así los umbrales (advertencia 0.2, alerta 0.25) salen de los datos y no solo de la regla general.

Uso:
    python simulacion/calibrar_drift.py      # deja evidencias/monitoreo/calibracion/calibracion_drift.{json,md}
"""
import csv
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "monitoreo" / "servicios"))
from app.calidad import VARIABLES, calidad, proporciones_por_bin  # noqa: E402
from vigilante import agrupar  # noqa: E402

FOTOS = RAIZ / "monitoreo" / "datos_locales" / "trafico"
SALIDA = RAIZ / "evidencias" / "monitoreo" / "calibracion"
N_VENTANA, REPETICIONES, SEMILLA, EPSILON = 570, 300, 42, 1e-4


def psi(base, produccion, agrupada):
    if agrupada:
        base, produccion = agrupar(base, produccion, 0.05)
    p, q = np.clip(produccion, EPSILON, None), np.clip(base, EPSILON, None)
    return float(np.sum((p - q) * np.log(p / q)))


def main():
    linea = json.loads((RAIZ / "modelo" / "versiones" / "1" / "linea_base.json").read_text(encoding="utf-8"))
    base = {v: np.array(linea["variables"][v]["proporciones"]) for v in VARIABLES}
    with open(FOTOS / "clases.csv", encoding="utf-8") as f:
        ids = [fila["id"] for fila in csv.DictReader(f)]
    medidas = {c: {i: calidad(Image.open(FOTOS / c / f"{i}.png").convert("RGB")) for i in ids}
               for c in ("original", "lluvia")}
    azar = np.random.default_rng(SEMILLA)
    resultados = {"n_ventana": N_VENTANA, "repeticiones": REPETICIONES, "normal": {}, "lluvia": {}}
    for nombre, agrupada in (("bins_finos", False), ("bins_agrupados_5pc", True)):
        maximos = []
        for _ in range(REPETICIONES):
            muestra = azar.choice(ids, N_VENTANA)
            maximos.append(max(psi(base[v], proporciones_por_bin([medidas["original"][i][v] for i in muestra], v),
                                   agrupada) for v in VARIABLES))
        resultados["normal"][nombre] = {f"p{q}": round(float(np.percentile(maximos, q)), 3) for q in (50, 95, 99)}
        resultados["normal"][nombre]["falsas_alarmas_0_25"] = round(float(np.mean(np.array(maximos) > 0.25)), 3)
        for fraccion in (0.1, 0.2, 0.3, 0.5, 1.0):
            psis = {v: [] for v in VARIABLES}
            for _ in range(100):
                muestra = azar.choice(ids, N_VENTANA)
                lluvia = azar.random(N_VENTANA) < fraccion
                for v in VARIABLES:
                    valores = [medidas["lluvia" if l else "original"][i][v] for i, l in zip(muestra, lluvia)]
                    psis[v].append(psi(base[v], proporciones_por_bin(valores, v), agrupada))
            resultados["lluvia"].setdefault(nombre, {})[f"{fraccion:.0%}"] = {
                v: round(float(np.median(psis[v])), 3) for v in VARIABLES}
    SALIDA.mkdir(parents=True, exist_ok=True)
    (SALIDA / "calibracion_drift.json").write_text(json.dumps(resultados, ensure_ascii=False, indent=2) + "\n",
                                                   encoding="utf-8", newline="\n")
    lineas = ["# Calibración del monitor de drift", "",
              f"Ventanas de {N_VENTANA} fotos sacadas al azar de las 181 fotos de tráfico (semilla {SEMILLA}).", "",
              "## Tráfico normal: PSI máximo por ventana (sin drift)", "",
              "| Bins | p50 | p95 | p99 | Ventanas arriba de 0.25 |", "|---|---|---|---|---|"]
    for nombre, r in resultados["normal"].items():
        lineas.append(f"| {nombre} | {r['p50']} | {r['p95']} | {r['p99']} | {r['falsas_alarmas_0_25']:.0%} |")
    lineas += ["", "## Fracción de fotos en lluvia: mediana del PSI (bins agrupados)", "",
               "| Lluvia | " + " | ".join(VARIABLES) + " |", "|---" * (len(VARIABLES) + 1) + "|"]
    for fraccion, r in resultados["lluvia"]["bins_agrupados_5pc"].items():
        lineas.append(f"| {fraccion} | " + " | ".join(str(r[v]) for v in VARIABLES) + " |")
    (SALIDA / "calibracion_drift.md").write_text("\n".join(lineas) + "\n", encoding="utf-8", newline="\n")
    print("\n".join(lineas))


if __name__ == "__main__":
    main()
