"""Carga del modelo y diagnóstico de una foto.

El diagnóstico tiene dos etapas, igual que en el entrenamiento de la Actividad 5:
    1. MobileNetV2 congelado (Actividad 4) convierte la foto en 1,280 características.
    2. La regresión logística de la versión en servicio da la probabilidad de cada clase.
Al final se aplica el umbral de la Actividad 6: la hoja se reporta como enferma si P(enferma) >= umbral,
aunque "Sana" sea la clase más probable, porque reportar sana una hoja enferma es el error más caro.

La foto se prepara exactamente como en el entrenamiento (RGB y 224x224 con LANCZOS); si se cambia este
paso, las características ya no son las mismas con las que aprendió el modelo. Antes se endereza según su
etiqueta EXIF de orientación (fotos de celular tomadas de lado); las fotos del entrenamiento no traen esa
etiqueta, así que para ellas no cambia nada.

Cada etapa es un span de la traza (app/observabilidad.py) y en cada una se puede inyectar una falla para
simular incidentes (app/caos.py). Recargar el modelo reutiliza el extractor si la versión nueva usa el mismo.

Motor del extractor (Actividad 9): MOTOR_EXTRACTOR=pytorch (por defecto) usa MobileNetV2 de PyTorch;
MOTOR_EXTRACTOR=onnx usa la misma red exportada a ONNX (EXTRACTOR_ONNX elige el archivo de modelo/extractores/,
por defecto la versión int8 estática) y no carga PyTorch ni transformers. Antes de usar el archivo ONNX se comprueba
que venga del mismo MobileNetV2 que pide la ficha de la versión y que su sha256 coincida con extractores.json.
"""
import hashlib
import io
import json
import os
import time

import numpy as np
import pandas as pd
import skops.io as sio
from PIL import Image, ImageOps

from app.calidad import calidad, fuera_de_rango
from app.observabilidad import etapa

MOTOR = (os.environ.get("MOTOR_EXTRACTOR", "") or "pytorch").strip().lower()
ARCHIVO_ONNX = (os.environ.get("EXTRACTOR_ONNX", "") or "mobilenet_v2_int8_estatico.onnx").strip()
if MOTOR not in ("pytorch", "onnx"):
    raise ValueError(f"MOTOR_EXTRACTOR debe ser 'pytorch' u 'onnx' (se recibió '{MOTOR}')")

TAMANO = (224, 224)
LIMITE_PIXELES = 50_000_000                  # más de 50 megapíxeles se rechaza (protege la memoria)
# Pillow solo lanza el error arriba del doble de este valor; entre 1x y 2x solo avisa, por eso además se
# revisa el tamaño a mano en abrir_imagen (lo encontró la prueba de casos extremos A7)
Image.MAX_IMAGE_PIXELS = LIMITE_PIXELES

# Mensaje para el usuario cuando una característica queda fuera del rango de las fotos del entrenamiento
ADVERTENCIAS = {
    ("brillo", "bajo"): "La foto está más oscura que las del entrenamiento; el diagnóstico puede ser menos confiable.",
    ("brillo", "alto"): "La foto está sobreexpuesta; el diagnóstico puede ser menos confiable.",
    ("contraste", "bajo"): "La foto tiene muy poco contraste (¿neblina o sombra?).",
    ("nitidez", "bajo"): "La foto está borrosa o movida; conviene tomarla otra vez.",
    ("verdor", "bajo"): "La foto no parece una hoja de maíz.",
}


class ImagenInvalida(ValueError):
    """El archivo no se pudo abrir como imagen."""
    es_del_cliente = True


def enderezar(im):
    """Aplica la orientación EXIF; si la etiqueta viene dañada, deja la imagen como llegó."""
    try:
        return ImageOps.exif_transpose(im)
    except Exception:                          # noqa: BLE001 - una etiqueta rara no debe tumbar el diagnóstico
        return im


def abrir_imagen(contenido):
    """Imagen RGB de 224x224 y datos de la original. Lanza ImagenInvalida si no es una imagen."""
    try:
        with Image.open(io.BytesIO(contenido)) as im:
            original = {"formato": im.format, "modo": im.mode, "ancho": im.width, "alto": im.height}
            if im.width * im.height > LIMITE_PIXELES:
                raise ImagenInvalida("la imagen tiene demasiados píxeles")
            im = enderezar(im).convert("RGB")
            return (im if im.size == TAMANO else im.resize(TAMANO, Image.LANCZOS)), original
    except Image.DecompressionBombError as e:
        raise ImagenInvalida("la imagen tiene demasiados píxeles") from e
    except OSError as e:                     # incluye UnidentifiedImageError y archivos truncados
        raise ImagenInvalida("el archivo no es una imagen válida") from e


class Extractor:
    """MobileNetV2 congelado. Procesador con backend PIL, el mismo del entrenamiento."""

    def __init__(self, repositorio, revision):
        import torch                                  # solo el motor PyTorch los necesita
        from transformers import MobileNetV2ImageProcessorPil, MobileNetV2Model
        self._torch = torch
        self.clave = ("pytorch", repositorio, revision)
        self.procesador = MobileNetV2ImageProcessorPil.from_pretrained(repositorio, revision=revision)
        self.modelo = MobileNetV2Model.from_pretrained(repositorio, revision=revision).eval()

    def __call__(self, imagen):
        with self._torch.no_grad():
            return self.modelo(**self.procesador([imagen], return_tensors="pt")).pooler_output.numpy()


def clave_extractor(version):
    return ("onnx", *version.extractor, ARCHIVO_ONNX) if MOTOR == "onnx" else ("pytorch", *version.extractor)


def crear_extractor(version):
    """Extractor del motor configurado para la versión; con ONNX verifica origen y huella del archivo."""
    if MOTOR == "pytorch":
        return Extractor(*version.extractor)
    from app.extractor_onnx import ExtractorOnnx
    carpeta = version.carpeta.parents[1] / "extractores"
    ficha = json.loads((carpeta / "extractores.json").read_text(encoding="utf-8"))
    if (ficha["origen"]["repositorio"], ficha["origen"]["revision"]) != tuple(version.extractor):
        raise RuntimeError(f"{carpeta / 'extractores.json'} no viene del extractor de la versión {version.version}")
    if ARCHIVO_ONNX not in ficha["archivos"]:
        raise RuntimeError(f"{ARCHIVO_ONNX} no está en {carpeta / 'extractores.json'}")
    extractor = ExtractorOnnx(carpeta / ARCHIVO_ONNX)
    if extractor.huella != ficha["archivos"][ARCHIVO_ONNX]["sha256"]:
        raise RuntimeError(f"{ARCHIVO_ONNX} no coincide con la huella de extractores.json")
    extractor.clave = clave_extractor(version)
    return extractor


class Diagnosticador:
    def __init__(self, version, umbral, alias, caos=None, extractor=None):
        huella = hashlib.sha256(version.ruta_clasificador.read_bytes()).hexdigest()
        if huella != version.ficha["sha256"]:
            raise RuntimeError(f"{version.ruta_clasificador} no coincide con la huella de la ficha del modelo")
        self.version, self.umbral, self.alias, self.caos = version, umbral, alias, caos
        self.clasificador = sio.load(version.ruta_clasificador)
        self.clases = [str(c) for c in self.clasificador.classes_]
        self.columnas = [f"f{i:04d}" for i in range(self.clasificador.n_features_in_)]
        reutilizable = extractor is not None and extractor.clave == clave_extractor(version)
        self.extractor = extractor if reutilizable else crear_extractor(version)
        self.cargado = time.time()

    def advertencias(self, medidas):
        avisos = []
        for variable in fuera_de_rango(medidas, self.version.linea_base):
            bajo = medidas[variable] < self.version.linea_base["variables"][variable]["rango_99"][0]
            aviso = ADVERTENCIAS.get((variable, "bajo" if bajo else "alto"))
            if aviso:
                avisos.append(aviso)
        return avisos

    def diagnosticar(self, contenido):
        inicio = time.perf_counter()
        with etapa("abrir_imagen", self.caos, bytes=len(contenido)) as span:
            imagen, original = abrir_imagen(contenido)
            span.set_attributes({f"imagen.{k}": v for k, v in original.items() if v is not None})
        with etapa("calidad_imagen", self.caos) as span:
            medidas = calidad(imagen)
            fuera = fuera_de_rango(medidas, self.version.linea_base)
            span.set_attributes({f"calidad.{k}": round(v, 3) for k, v in medidas.items()})
            span.set_attribute("calidad.fuera_de_rango", ",".join(fuera))
        with etapa("extraer_caracteristicas", self.caos):
            caracteristicas = pd.DataFrame(self.extractor(imagen).astype(np.float32), columns=self.columnas)
        with etapa("clasificar", self.caos, **{"modelo.version": self.version.version}) as span:
            proba = self.clasificador.predict_proba(caracteristicas)[0]
            probabilidades = dict(zip(self.clases, map(float, proba)))
            p_enferma = 1 - probabilidades["Sana"]
            enferma = p_enferma >= self.umbral
            mas_probable = max(probabilidades, key=probabilidades.get)
            enfermedad = max((c for c in self.clases if c != "Sana"), key=probabilidades.get)
            diagnostico = enfermedad if enferma else "Sana"
            span.set_attributes({"modelo.diagnostico": diagnostico, "modelo.p_enferma": round(p_enferma, 4),
                                 "modelo.umbral": self.umbral})
        return {
            "diagnostico": diagnostico,
            "estado": "enferma" if enferma else "sana",
            "probabilidad_enferma": round(p_enferma, 4),
            "umbral_enferma": self.umbral,
            "clase_mas_probable": mas_probable,
            # True cuando el umbral cambia la respuesta: la clase más probable es Sana, pero P(enferma) pasa el umbral
            "decidido_por_umbral": bool(enferma and mas_probable == "Sana"),
            "probabilidades": {c: round(p, 4) for c, p in sorted(probabilidades.items(), key=lambda x: -x[1])},
            "imagen": original,
            "calidad_imagen": {k: round(v, 3) for k, v in medidas.items()},
            "fuera_de_rango": fuera,
            "advertencias": self.advertencias(medidas),
            "modelo_version": self.version.version,
            "tiempo_ms": round((time.perf_counter() - inicio) * 1000, 1),
        }
