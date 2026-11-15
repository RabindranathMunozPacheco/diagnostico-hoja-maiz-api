"""Evaluación de versiones del modelo fuera de línea (compuerta de despliegue y reentrenamiento).

Usa las mismas métricas que la sonda del vigilante para que lo que se mide antes de desplegar y lo que se mide en
producción sea comparable:
    f1_macro            F1 macro de la clase más probable (4 clases)
    enferma_como_sana   proporción de hojas enfermas reportadas como sanas con el umbral de la versión
    sana_como_enferma   proporción de hojas sanas reportadas como enfermas con el umbral

Conjuntos:
    golden / adverso          las 40 fotos de la sonda, normales y en condición de lluvia
    prueba / prueba_lluvia    las 221 fotos de prueba, normales y en condición de lluvia
Las características de las fotos normales salen de la Actividad 5; las de lluvia se calculan con el mismo
MobileNetV2 de la API y se guardan en monitoreo/datos_locales/ para no recalcularlas.
"""
import json
import sys
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd
import torch
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
import datos  # noqa: E402

from app.modelo import Extractor  # noqa: E402

LOCALES = datos.RAIZ / "monitoreo" / "datos_locales"
CLASES = datos.CLASES


@lru_cache(maxsize=1)
def extractor():
    ficha = json.loads((datos.RAIZ / "modelo" / "versiones" / "1" / "ficha_modelo.json").read_text(encoding="utf-8"))
    return Extractor(ficha["extractor"]["repositorio"], ficha["extractor"]["revision"])


def caracteristicas_imagenes(imagenes, lote=32):
    """Las 1,280 características de MobileNetV2 de una lista de imágenes PIL RGB de 224x224 (por lotes)."""
    ext, salida = extractor(), []
    for i in range(0, len(imagenes), lote):
        with torch.no_grad():
            entrada = ext.procesador(imagenes[i:i + lote], return_tensors="pt")
            salida.append(ext.modelo(**entrada).pooler_output.numpy().astype(np.float32))
    return pd.DataFrame(np.vstack(salida), columns=[f"f{i:04d}" for i in range(salida[0].shape[1])])


def metricas(y, proba, clases, umbral):
    from sklearn.metrics import f1_score
    y, proba, clases = np.asarray(y), np.asarray(proba), list(clases)
    mas_probable = np.array(clases)[proba.argmax(1)]
    enferma = (1 - proba[:, clases.index("Sana")]) >= umbral
    return {"n": int(len(y)),
            "f1_macro": round(float(f1_score(y, mas_probable, average="macro", labels=CLASES)), 4),
            "exactitud": round(float(np.mean(y == mas_probable)), 4),
            "enferma_como_sana": round(float(np.mean(~enferma[y != "Sana"])), 4),
            "sana_como_enferma": round(float(np.mean(enferma[y == "Sana"])), 4)}


def _lluvia_en_cache(nombre, filas):
    ruta = LOCALES / f"caracteristicas_{nombre}.parquet"
    if ruta.exists():
        return pd.read_parquet(ruta).set_index("id")
    imagenes = [datos.lluvia(datos.imagen(f)) for _, f in filas.iterrows()]
    c = caracteristicas_imagenes(imagenes)
    c.insert(0, "id", filas["id"].values)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    c.to_parquet(ruta, index=False)
    return c.set_index("id")


def conjuntos():
    """{nombre: (X, y)} con los cuatro conjuntos de evaluación."""
    tabla = datos.tabla().set_index("id", drop=False)
    golden_ids = [f["id"] for f in json.loads((datos.RAIZ / "monitoreo" / "golden_set.json")
                                              .read_text(encoding="utf-8"))["fotos"]]
    prueba = tabla[tabla["parte"] == "prueba"]
    normal = datos.caracteristicas(prueba["id"])
    lluvia = _lluvia_en_cache("prueba_lluvia", prueba)
    return {"golden": (normal.loc[golden_ids], tabla.loc[golden_ids, "clase"].values),
            "adverso": (lluvia.loc[golden_ids], tabla.loc[golden_ids, "clase"].values),
            "prueba": (normal.loc[prueba["id"]], prueba["clase"].values),
            "prueba_lluvia": (lluvia.loc[prueba["id"]], prueba["clase"].values)}


def evaluar(modelo, umbral, nombres=("golden", "adverso", "prueba", "prueba_lluvia")):
    todos = conjuntos()
    clases = [str(c) for c in modelo.classes_]
    return {n: metricas(todos[n][1], modelo.predict_proba(todos[n][0].astype(np.float32)), clases, umbral)
            for n in nombres}


def imagen_png(ruta):
    return Image.open(ruta).convert("RGB")
