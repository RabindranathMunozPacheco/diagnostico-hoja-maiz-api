"""Extractor MobileNetV2 con ONNX Runtime, sin PyTorch ni transformers (Actividad 9).

Hace lo mismo que app.modelo.Extractor (procesador de Hugging Face + MobileNetV2 de PyTorch), pero con numpy y
ONNX Runtime, para que la imagen de producción no cargue PyTorch (733 MB de los 2 GB de la imagen actual) y
arranque más rápido. El modelo se exporta y se cuantiza con escalamiento/exportar_onnx.py.

El preprocesado replica el del procesador de Hugging Face de este modelo (preprocessor_config.json): lado corto a
256 con interpolación bilineal, recorte central de 224, escala a [0, 1] y normalización con media 0.5 y
desviación 0.5 (los valores quedan en [-1, 1]). escalamiento/comparar_extractores.py comprueba que la diferencia
con el procesador original es despreciable.
"""
import hashlib
import os
from pathlib import Path

import numpy as np
import onnxruntime as ort
from PIL import Image

LADO_CORTO = 256
RECORTE = 224
MEDIA = DESVIACION = 0.5


def preparar(imagenes):
    """Lista de imágenes PIL RGB -> arreglo float32 (N, 3, 224, 224) como el procesador de Hugging Face."""
    lote = []
    for im in imagenes:
        ancho, alto = im.size
        corto, largo = (ancho, alto) if ancho <= alto else (alto, ancho)
        nuevo_largo = int(LADO_CORTO * largo / corto)
        tamano = (LADO_CORTO, nuevo_largo) if ancho <= alto else (nuevo_largo, LADO_CORTO)
        im = im.resize(tamano, Image.BILINEAR)
        izquierda, arriba = (tamano[0] - RECORTE) // 2, (tamano[1] - RECORTE) // 2
        im = im.crop((izquierda, arriba, izquierda + RECORTE, arriba + RECORTE))
        x = np.asarray(im, dtype=np.float32) * np.float32(1 / 255)
        lote.append(((x - MEDIA) / DESVIACION).transpose(2, 0, 1))
    return np.ascontiguousarray(np.stack(lote), dtype=np.float32)


class ExtractorOnnx:
    """Misma interfaz que app.modelo.Extractor: llamarlo con una imagen PIL regresa un arreglo (1, 1280)."""

    def __init__(self, ruta, hilos=None):
        ruta = Path(ruta)
        self.huella = hashlib.sha256(ruta.read_bytes()).hexdigest()
        self.clave = ("onnx", self.huella)
        opciones = ort.SessionOptions()
        opciones.intra_op_num_threads = int(hilos or os.environ.get("OMP_NUM_THREADS", 1))
        opciones.inter_op_num_threads = 1
        self.sesion = ort.InferenceSession(str(ruta), opciones, providers=["CPUExecutionProvider"])
        self.entrada = self.sesion.get_inputs()[0].name

    def lote(self, imagenes):
        return self.sesion.run(None, {self.entrada: preparar(imagenes)})[0]

    def __call__(self, imagen):
        return self.lote([imagen])
