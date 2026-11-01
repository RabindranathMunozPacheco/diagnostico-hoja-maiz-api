"""Carga del modelo y diagnóstico de una foto.

El diagnóstico tiene dos etapas, igual que en el entrenamiento de la Actividad 5:
    1. MobileNetV2 congelado (Actividad 4) convierte la foto en 1,280 características.
    2. La regresión logística de la Actividad 5 da la probabilidad de cada clase.
Al final se aplica el umbral de la Actividad 6: la hoja se reporta como enferma si P(enferma) >= umbral,
aunque "Sana" sea la clase más probable, porque reportar sana una hoja enferma es el error más caro.

La foto se prepara exactamente como en el entrenamiento (RGB y 224x224 con LANCZOS); si se cambia este
paso, las características ya no son las mismas con las que aprendió el modelo.
"""
import hashlib
import io
import time

import numpy as np
import pandas as pd
import skops.io as sio
import torch
from PIL import Image
from transformers import MobileNetV2ImageProcessorPil, MobileNetV2Model

TAMANO = (224, 224)
Image.MAX_IMAGE_PIXELS = 50_000_000          # más de ~50 megapíxeles se rechaza (protege la memoria)


class ImagenInvalida(ValueError):
    """El archivo no se pudo abrir como imagen."""


def abrir_imagen(contenido):
    """Imagen RGB de 224x224 y datos de la original. Lanza ImagenInvalida si no es una imagen."""
    try:
        with Image.open(io.BytesIO(contenido)) as im:
            original = {"formato": im.format, "modo": im.mode, "ancho": im.width, "alto": im.height}
            im = im.convert("RGB")
            return (im if im.size == TAMANO else im.resize(TAMANO, Image.LANCZOS)), original
    except Image.DecompressionBombError as e:
        raise ImagenInvalida("la imagen tiene demasiados píxeles") from e
    except OSError as e:                     # incluye UnidentifiedImageError y archivos truncados
        raise ImagenInvalida("el archivo no es una imagen válida") from e


class Diagnosticador:
    def __init__(self, config):
        self.config = config
        ficha = config.ficha
        huella = hashlib.sha256(config.ruta_modelo.read_bytes()).hexdigest()
        if huella != ficha["sha256"]:
            raise RuntimeError(f"{config.ruta_modelo.name} no coincide con la huella de la ficha del modelo")
        self.clasificador = sio.load(config.ruta_modelo)
        self.clases = [str(c) for c in self.clasificador.classes_]
        self.columnas = [f"f{i:04d}" for i in range(self.clasificador.n_features_in_)]
        # Procesador con backend PIL, el mismo del entrenamiento (el de torchvision redimensiona distinto)
        self.procesador = MobileNetV2ImageProcessorPil.from_pretrained(config.extractor_repo,
                                                                       revision=config.extractor_revision)
        self.extractor = MobileNetV2Model.from_pretrained(config.extractor_repo,
                                                          revision=config.extractor_revision).eval()

    def caracteristicas(self, imagen):
        with torch.no_grad():
            salida = self.extractor(**self.procesador([imagen], return_tensors="pt")).pooler_output
        return pd.DataFrame(salida.numpy().astype(np.float32), columns=self.columnas)

    def diagnosticar(self, contenido):
        inicio = time.perf_counter()
        imagen, original = abrir_imagen(contenido)
        proba = self.clasificador.predict_proba(self.caracteristicas(imagen))[0]
        probabilidades = dict(zip(self.clases, map(float, proba)))

        p_enferma = 1 - probabilidades["Sana"]
        enferma = p_enferma >= self.config.umbral_enferma
        mas_probable = max(probabilidades, key=probabilidades.get)
        enfermedad = max((c for c in self.clases if c != "Sana"), key=probabilidades.get)
        return {
            "diagnostico": enfermedad if enferma else "Sana",
            "estado": "enferma" if enferma else "sana",
            "probabilidad_enferma": round(p_enferma, 4),
            "umbral_enferma": self.config.umbral_enferma,
            "clase_mas_probable": mas_probable,
            # True cuando el umbral cambia la respuesta: la clase más probable es Sana, pero P(enferma) pasa el umbral
            "decidido_por_umbral": bool(enferma and mas_probable == "Sana"),
            "probabilidades": {c: round(p, 4) for c, p in sorted(probabilidades.items(), key=lambda x: -x[1])},
            "imagen": original,
            "tiempo_ms": round((time.perf_counter() - inicio) * 1000, 1),
        }
