"""Crea la versión 2 del modelo con un defecto a propósito, para simular un release malo (incidente INC-04).

La historia: al reentrenar con un script de migración de etiquetas, las carpetas "Healthy" y "Gray_Leaf_Spot"
quedaron cruzadas, así que la v2 aprendió a llamar "Sana" a la mancha gris (la enfermedad más común del dataset) y
"Mancha gris" a las hojas sanas. El archivo es válido, la API la carga sin errores y responde igual de rápido:
ningún SLO técnico la ve. Solo la sonda con el golden set (fotos con clase conocida) lo nota.

Se entrena igual que la v1 (misma configuración, mismas 857 fotos y semilla 42); lo único distinto son las
etiquetas cruzadas. Queda registrada como candidata; el incidente la promueve con
scripts/desplegar.py --sin-compuerta (como un despliegue de emergencia que se salta la compuerta de calidad).

Uso:
    python simulacion/crear_version_defectuosa.py
"""
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import skops.io as sio
from sklearn.base import clone

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "scripts"))
import datos  # noqa: E402
import evaluacion  # noqa: E402
import versiones  # noqa: E402

CRUCE = {"Sana": "Mancha gris", "Mancha gris": "Sana"}


def main():
    v1 = sio.load(RAIZ / "modelo" / "versiones" / "1" / "model.skops")
    tabla = datos.tabla()
    ent = tabla[tabla["parte"] == "entrenamiento"]
    X = datos.caracteristicas(ent["id"]).astype(np.float32)
    y = ent["clase"].map(lambda c: CRUCE.get(c, c)).values
    v2 = clone(v1).fit(X, y)
    umbral = {"valor": 0.2494, "origen": "el mismo de la versión 1", "criterio": "sin recalibrar"}
    metricas = evaluacion.evaluar(v2, umbral["valor"])
    print({k: (m["f1_macro"], m["enferma_como_sana"]) for k, m in metricas.items()})
    prueba = tabla[tabla["parte"] == "prueba"]
    with ThreadPoolExecutor(8) as ex:
        imagenes = list(ex.map(datos.imagen, [f for _, f in ent.iterrows()]))
    proba = v2.predict_proba(datos.caracteristicas(prueba["id"]).astype(np.float32))
    carpeta = versiones.guardar_version(
        v2, versiones.comun.siguiente_version(versiones.DIR_MODELOS), umbral, imagenes, proba,
        "DEFECTUOSA a propósito (simulación INC-04): etiquetas Sana y Mancha gris cruzadas al reentrenar",
        {"origen": {"tipo": "reentrenamiento con etiquetas cruzadas (defecto simulado)", "semilla": 42,
                    "datos": "857 fotos de entrenamiento de la Actividad 5 con Sana <-> Mancha gris"},
         "metricas_prueba": metricas["prueba"], "metricas_golden": metricas["golden"]},
        "Entrada: las mismas 857 fotos de entrenamiento de la v1. Predicciones: 221 fotos de prueba.")
    print(f"Versión defectuosa guardada en {carpeta.relative_to(RAIZ)} (candidata)")


if __name__ == "__main__":
    main()
