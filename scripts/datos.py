"""Acceso a las fotos y características de la Actividad 5 (línea base, golden set, tráfico simulado y reentrenamiento).

Las fotos no se suben al repositorio porque el dataset no declara licencia: se toman de la caché de Hugging Face
(o se descargan) en la revisión fija y se comprueba el sha256 de cada una. Las características de MobileNetV2 y la
tabla de clases, sesiones y grupos salen de la Actividad 5 (DIR_ACT5, por defecto ../../actividad5/Actividad5).

Partes del dataset (las mismas de la Actividad 5 y la Fase II):
    prueba         las 221 fotos de pruebas/prueba_act5.json (primer fold de StratifiedGroupKFold, semilla 42)
    entrenamiento  las 857 restantes, con las que se entrenó la versión 1

Condiciones de foto que se usan para simular el data drift (temporada de lluvias: nublado y gotas en la lente):
    lluvia        brillo al 55 % y desenfoque gaussiano de radio 1.5 (conjunto adverso fijo de la sonda y de la prueba)
    lluvia_azar   brillo entre 35 y 80 % y desenfoque de 0 a 2.5 al azar (tráfico simulado con semilla 7 y aumento de
                  datos del reentrenamiento con semilla 42)
"""
import hashlib
import io
import json
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from huggingface_hub import hf_hub_download
from PIL import Image, ImageEnhance, ImageFilter

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))                       # para importar app.* desde los scripts

ACT5 = Path(os.environ.get("DIR_ACT5", RAIZ.parents[1] / "actividad5" / "Actividad5"))
PRUEBA = json.loads((RAIZ / "pruebas" / "prueba_act5.json").read_text(encoding="utf-8"))
REPO, TIPO, REVISION = PRUEBA["repositorio"], PRUEBA["tipo"], PRUEBA["revision"]
CLASES = ["Mancha gris", "Roya común", "Sana", "Tizón"]


def tabla():
    """Una fila por foto (1,078) con id, ruta, sha256, clase, sesión, grupo y parte (prueba o entrenamiento)."""
    df = pd.read_csv(ACT5 / "datos" / "datos_limp" / "maiz_parcela.csv", encoding="utf-8")
    ids_prueba = {f["id"] for f in PRUEBA["fotos"]}
    df["parte"] = np.where(df["id"].isin(ids_prueba), "prueba", "entrenamiento")
    return df


def caracteristicas(ids=None):
    """Características de MobileNetV2 calculadas en la Actividad 5 (fotos originales), indexadas por id."""
    c = pd.read_parquet(ACT5 / "datos" / "datos_limp" / "caracteristicas.parquet").set_index("id")
    return c if ids is None else c.loc[list(ids)]


def bytes_foto(ruta, sha256):
    archivo = hf_hub_download(REPO, ruta, repo_type=TIPO, revision=REVISION)
    contenido = Path(archivo).read_bytes()
    if hashlib.sha256(contenido).hexdigest() != sha256:
        raise SystemExit(f"La foto {ruta} no coincide con su sha256")
    return contenido


def imagen(fila):
    """Foto RGB de una fila de tabla(); las del dataset ya vienen en 224x224."""
    return Image.open(io.BytesIO(bytes_foto(fila["ruta"], fila["sha256"]))).convert("RGB")


def lluvia(im, brillo=0.55, radio=1.5):
    return ImageEnhance.Brightness(im.filter(ImageFilter.GaussianBlur(radio))).enhance(brillo)


def lluvia_azar(im, azar):
    return lluvia(im, brillo=float(azar.uniform(0.35, 0.8)), radio=float(azar.uniform(0.0, 2.5)))


def png(im):
    b = io.BytesIO()
    im.save(b, "PNG")
    return b.getvalue()
