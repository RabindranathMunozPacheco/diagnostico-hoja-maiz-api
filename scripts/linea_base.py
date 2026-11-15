"""Calcula la línea base de una versión del modelo y la guarda en modelo/versiones/<n>/linea_base.json.

La línea base describe lo "normal" para esa versión y es contra lo que el monitor de drift compara la producción:
    variables     cómo se reparten el brillo, el contraste, la nitidez y el verdor de las fotos con las que se
                  entrenó (proporción por bin, los mismos bins de app/calidad.py) y su rango del 99 % central
    predicciones  cómo responde el modelo en fotos que no vio: proporción de cada diagnóstico (con el umbral),
                  distribución de P(enferma) y de la confianza, y proporción decidida por el umbral

Cada versión lleva la suya porque se entrenó con datos distintos: si una versión nueva se entrena con fotos de
temporada de lluvias, su línea base ya incluye esas fotos y dejan de contar como drift.

Uso:
    python scripts/linea_base.py --version 1      # 857 fotos de entrenamiento; predicciones en las 221 de prueba
"""
import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import datos  # noqa: E402

from app.calidad import BINS, VARIABLES, calidad, proporciones_por_bin  # noqa: E402
from app.config import cargar_config, cargar_version  # noqa: E402
from app.observabilidad import BUCKETS_CONFIANZA, BUCKETS_PROB  # noqa: E402


def _proporciones(valores, limites):
    indices = np.searchsorted(np.asarray(limites, dtype=float), np.asarray(valores, dtype=float), side="left")
    conteos = np.bincount(indices, minlength=len(limites) + 1)
    return (conteos / max(conteos.sum(), 1)).round(6).tolist()


def variables_de_entrada(imagenes):
    """Línea base de las cuatro características a partir de una lista de imágenes PIL RGB de 224x224."""
    filas = [calidad(im) for im in imagenes]
    medidas = {v: np.array([f[v] for f in filas]) for v in VARIABLES}
    return {v: {"bins": list(BINS[v]),
                "proporciones": proporciones_por_bin(medidas[v], v).round(6).tolist(),
                "rango_99": np.percentile(medidas[v], [0.5, 99.5]).round(4).tolist(),
                "media": round(float(medidas[v].mean()), 4),
                "percentiles": dict(zip(["p05", "p25", "p50", "p75", "p95"],
                                        np.percentile(medidas[v], [5, 25, 50, 75, 95]).round(4).tolist()))}
            for v in VARIABLES}


def predicciones(probabilidades, clases, umbral):
    """Línea base de las predicciones a partir de una matriz (n fotos x clases) de probabilidades."""
    probabilidades = np.asarray(probabilidades)
    i_sana = clases.index("Sana")
    p_enferma = 1 - probabilidades[:, i_sana]
    mas_probable = np.array(clases)[probabilidades.argmax(1)]
    otras = [i for i, c in enumerate(clases) if c != "Sana"]
    enfermedad = np.array(clases)[np.array(otras)[probabilidades[:, otras].argmax(1)]]
    diagnostico = np.where(p_enferma >= umbral, enfermedad, "Sana")
    return {"n_fotos": int(len(probabilidades)),
            "proporcion_por_diagnostico": {c: round(float(np.mean(diagnostico == c)), 6) for c in clases},
            "proporcion_enferma": round(float(np.mean(p_enferma >= umbral)), 6),
            "proporcion_decidida_por_umbral": round(float(np.mean((p_enferma >= umbral) & (mas_probable == "Sana"))), 6),
            "probabilidad_enferma": {"bins": list(BUCKETS_PROB), "proporciones": _proporciones(p_enferma, BUCKETS_PROB)},
            "confianza": {"bins": list(BUCKETS_CONFIANZA),
                          "proporciones": _proporciones(probabilidades.max(1), BUCKETS_CONFIANZA)},
            "confianza_media": round(float(probabilidades.max(1).mean()), 6)}


def guardar(version, entrada, salida_modelo, descripcion):
    linea = {"version": version.version, "creada": datetime.now(timezone.utc).isoformat(timespec="seconds"),
             "descripcion": descripcion, "variables": entrada, "predicciones": salida_modelo}
    ruta = version.carpeta / "linea_base.json"
    ruta.write_text(json.dumps(linea, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    return ruta


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", default="1")
    args = parser.parse_args()
    config = cargar_config()
    version = cargar_version(config, args.version)
    tabla = datos.tabla()
    entrenamiento = tabla[tabla["parte"] == "entrenamiento"]
    prueba = tabla[tabla["parte"] == "prueba"]
    print(f"Leyendo {len(entrenamiento)} fotos de entrenamiento…")
    with ThreadPoolExecutor(8) as ex:
        imagenes = list(ex.map(datos.imagen, [f for _, f in entrenamiento.iterrows()]))
    entrada = variables_de_entrada(imagenes)

    import skops.io as sio
    clasificador = sio.load(version.ruta_clasificador)
    proba = clasificador.predict_proba(datos.caracteristicas(prueba["id"]).astype(np.float32))
    salida = predicciones(proba, [str(c) for c in clasificador.classes_], config.umbral(version))
    ruta = guardar(version, entrada, salida,
                   f"Entrada: {len(entrenamiento)} fotos de entrenamiento de la versión {version.version}. "
                   f"Predicciones: {len(prueba)} fotos de prueba que el modelo no vio.")
    print(f"Línea base guardada en {ruta.relative_to(datos.RAIZ)}")
    for v in VARIABLES:
        print(f"  {v:10s} p50 {entrada[v]['percentiles']['p50']:>9}  rango 99 % {entrada[v]['rango_99']}")
    print(f"  diagnósticos: {salida['proporcion_por_diagnostico']}  enferma {salida['proporcion_enferma']:.3f}")


if __name__ == "__main__":
    main()
