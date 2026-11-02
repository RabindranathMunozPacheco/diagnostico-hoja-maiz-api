"""Pruebas de integración de la API: cada endpoint responde con la forma y el código esperados.

Usan imágenes generadas en el momento (no hacen falta las fotos del dataset). La primera vez descargan el
extractor MobileNetV2 de Hugging Face si no está en la caché.

Uso:
    python -m pytest pruebas -v
"""
import hashlib
import io

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.config import RAIZ
from app.main import app


@pytest.fixture(scope="module")
def cliente():
    with TestClient(app) as c:          # el "with" ejecuta el arranque: carga el modelo una sola vez
        yield c


def imagen(formato="JPEG", modo="RGB", tamano=(640, 480), color=(60, 130, 50)):
    b = io.BytesIO()
    Image.new(modo, tamano, color if modo != "L" else 128).save(b, formato)
    return b.getvalue()


def test_salud(cliente):
    r = cliente.get("/api/salud")
    assert r.status_code == 200
    assert r.json()["estado"] == "ok"


def test_modelo_es_el_de_la_ficha(cliente):
    ficha = cliente.get("/api/modelo").json()
    assert ficha["clases"] == ["Mancha gris", "Roya común", "Sana", "Tizón"]
    assert ficha["umbral_en_uso"] == ficha["umbral_enferma"]["valor"]
    assert hashlib.sha256((RAIZ / "modelo" / "model.skops").read_bytes()).hexdigest() == ficha["sha256"]


@pytest.mark.parametrize("formato,modo", [("JPEG", "RGB"), ("PNG", "RGBA"), ("PNG", "L")])
def test_diagnostico_responde(cliente, formato, modo):
    r = cliente.post("/api/diagnostico", files={"foto": (f"hoja.{formato.lower()}", imagen(formato, modo), "image/*")})
    assert r.status_code == 200
    d = r.json()
    assert set(d["probabilidades"]) == {"Mancha gris", "Roya común", "Sana", "Tizón"}
    assert sum(d["probabilidades"].values()) == pytest.approx(1, abs=1e-3)
    assert d["probabilidad_enferma"] == pytest.approx(1 - d["probabilidades"]["Sana"], abs=1e-3)
    # Regla de decisión: enferma si P(enferma) >= umbral
    assert (d["estado"] == "enferma") == (d["probabilidad_enferma"] >= d["umbral_enferma"])
    assert (d["diagnostico"] == "Sana") == (d["estado"] == "sana")


def test_archivo_que_no_es_imagen(cliente):
    r = cliente.post("/api/diagnostico", files={"foto": ("notas.txt", b"no es una foto", "text/plain")})
    assert r.status_code == 415


def test_archivo_demasiado_grande(cliente):
    r = cliente.post("/api/diagnostico", files={"foto": ("enorme.jpg", b"\xff" * (11 * 1024 * 1024), "image/jpeg")})
    assert r.status_code == 413


def test_sin_archivo(cliente):
    assert cliente.post("/api/diagnostico").status_code == 422


def test_imagen_con_demasiados_pixeles(cliente):
    # 64 megapíxeles: pasa el límite de 50 aunque comprimida pese poco (caso extremo A7)
    r = cliente.post("/api/diagnostico", files={"foto": ("64mp.png", imagen("PNG", tamano=(8000, 8000)), "image/png")})
    assert r.status_code == 415


def test_foto_de_celular_con_orientacion_exif(cliente):
    # Imagen asimétrica: de lado + etiqueta EXIF que la endereza debe dar lo mismo que derecha (caso extremo B7)
    yy, xx = np.mgrid[0:224, 0:224]
    derecha = Image.fromarray(np.stack([xx, yy, 255 - xx], -1).astype(np.uint8))
    exif = Image.Exif()
    exif[0x0112] = 8                         # el visor la gira 90 grados para mostrarla derecha
    de_lado, normal = io.BytesIO(), io.BytesIO()
    derecha.rotate(-90).save(de_lado, "PNG", exif=exif.tobytes())
    derecha.save(normal, "PNG")
    a = cliente.post("/api/diagnostico", files={"foto": ("de_lado.png", de_lado.getvalue(), "image/png")}).json()
    b = cliente.post("/api/diagnostico", files={"foto": ("derecha.png", normal.getvalue(), "image/png")}).json()
    assert a["probabilidades"] == pytest.approx(b["probabilidades"], abs=1e-4)


def test_pagina_web(cliente):
    r = cliente.get("/")
    assert r.status_code == 200 and "Diagnóstico de hoja de maíz" in r.text
    assert cliente.get("/static/app.js").status_code == 200
