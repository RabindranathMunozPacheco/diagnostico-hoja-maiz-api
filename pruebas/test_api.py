"""Pruebas de integración de la API: cada endpoint responde con la forma y el código esperados.

Usan imágenes generadas en el momento (no hacen falta las fotos del dataset). La primera vez descargan el
extractor MobileNetV2 de Hugging Face si no está en la caché. Las pruebas de administración e inyección de fallas
arrancan la API con un token y CAOS_HABILITADO=1 (ver conftest.py).

Uso:
    python -m pytest pruebas -v
"""
import hashlib
import io
import json
import shutil

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import main
from app.config import RAIZ
from app.main import app

TOKEN = {"X-Token-Admin": "token-de-prueba"}


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
    registro = json.loads((RAIZ / "modelo" / "registro.json").read_text(encoding="utf-8"))
    assert ficha["version_en_servicio"] == registro["alias"]["champion"]
    carpeta = RAIZ / "modelo" / registro["versiones"][ficha["version_en_servicio"]]["carpeta"]
    assert hashlib.sha256((carpeta / "model.skops").read_bytes()).hexdigest() == ficha["sha256"]


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


# ------------------------------------------------------------------------------------- observabilidad (v0.4.0)
def test_respuesta_trae_calidad_y_trace_id(cliente):
    r = cliente.post("/api/diagnostico", files={"foto": ("hoja.jpg", imagen(), "image/jpeg")})
    assert r.status_code == 200
    assert len(r.headers["x-trace-id"]) == 32
    d = r.json()
    assert set(d["calidad_imagen"]) == {"brillo", "contraste", "nitidez", "verdor"}
    # Una imagen lisa no tiene contraste ni bordes: queda fuera del rango del entrenamiento y lleva advertencias
    assert {"contraste", "nitidez"} <= set(d["fuera_de_rango"])
    assert d["advertencias"]


def test_foto_oscura_avisa(cliente):
    oscura = imagen(color=(12, 30, 10))
    d = cliente.post("/api/diagnostico", files={"foto": ("oscura.jpg", oscura, "image/jpeg")}).json()
    assert "brillo" in d["fuera_de_rango"]
    assert any("oscura" in a for a in d["advertencias"])


def test_metricas_prometheus(cliente):
    cliente.post("/api/diagnostico", files={"foto": ("hoja.jpg", imagen(), "image/jpeg")},
                 headers={"X-Origen": "sonda"})
    texto = cliente.get("/metrics").text
    for serie in ('http_solicitudes_total{codigo="200",metodo="POST",origen="sonda",ruta="/api/diagnostico"}',
                  "http_duracion_segundos_bucket", "diagnostico_etapa_segundos_bucket",
                  'modelo_info{alias="champion"', "modelo_predicciones_total", "entrada_brillo_bucket",
                  "entrada_fuera_de_rango_total"):
        assert serie in texto, serie


def test_rutas_desconocidas_no_crean_series_nuevas(cliente):
    cliente.get("/no/existe/123")
    texto = cliente.get("/metrics").text
    assert "/no/existe/123" not in texto
    assert 'ruta="sin_ruta"' in texto


def test_administracion_pide_token(cliente):
    assert cliente.post("/api/admin/recargar").status_code == 401
    assert cliente.post("/api/admin/recargar", headers={"X-Token-Admin": "otro"}).status_code == 401


def test_falla_inyectada_responde_500_y_se_cuenta(cliente):
    r = cliente.post("/api/admin/fallas", headers=TOKEN,
                     json={"etapa": "clasificar", "tasa": 1.0, "duracion_s": 30})
    assert r.status_code == 200
    try:
        assert cliente.get("/api/salud").json()["falla_inyectada"]["etapa"] == "clasificar"
        r = cliente.post("/api/diagnostico", files={"foto": ("hoja.jpg", imagen(), "image/jpeg")})
        assert r.status_code == 500
        assert r.headers["x-trace-id"] in r.json()["detail"]        # el usuario puede reportar su trace_id
        assert 'diagnostico_errores_total{etapa="clasificar",tipo="FallaInyectada"}' in cliente.get("/metrics").text
    finally:
        assert cliente.delete("/api/admin/fallas", headers=TOKEN).status_code == 200
    assert cliente.post("/api/diagnostico", files={"foto": ("hoja.jpg", imagen(), "image/jpeg")}).status_code == 200


def test_recargar_cambia_de_version_y_regresa(cliente, tmp_path, monkeypatch):
    # Registro temporal con una "versión 9" (copia de la que está en servicio): promover y luego regresar, sin
    # reiniciar la API. La versión en servicio se lee del registro (era la 1 hasta la Actividad 8; ahora es la 3)
    copia = tmp_path / "modelo"
    shutil.copytree(RAIZ / "modelo", copia)
    registro = json.loads((copia / "registro.json").read_text(encoding="utf-8"))
    actual = registro["alias"]["champion"]
    shutil.copytree(copia / "versiones" / actual, copia / "versiones" / "9")
    registro["versiones"]["9"] = {**registro["versiones"][actual], "carpeta": "versiones/9"}
    monkeypatch.setattr(main, "config", main.config.__class__(**{**main.config.__dict__, "dir_modelos": copia}))

    def mover_alias(version):
        registro["alias"]["champion"] = version
        (copia / "registro.json").write_text(json.dumps(registro), encoding="utf-8")

    mover_alias("9")
    r = cliente.post("/api/admin/recargar", headers=TOKEN)
    assert r.status_code == 200 and r.json() == {**r.json(), "version_anterior": actual, "version_actual": "9"}
    assert cliente.get("/api/salud").json()["modelo_version"] == "9"
    mover_alias(actual)
    assert cliente.post("/api/admin/recargar", headers=TOKEN).json()["version_actual"] == actual


def test_recarga_con_modelo_danado_conserva_la_version(cliente, tmp_path, monkeypatch):
    copia = tmp_path / "modelo"
    shutil.copytree(RAIZ / "modelo", copia)
    actual = json.loads((copia / "registro.json").read_text(encoding="utf-8"))["alias"]["champion"]
    (copia / "versiones" / actual / "model.skops").write_bytes(b"archivo danado")
    monkeypatch.setattr(main, "config", main.config.__class__(**{**main.config.__dict__, "dir_modelos": copia}))
    r = cliente.post("/api/admin/recargar", headers=TOKEN)
    assert r.status_code == 500
    assert cliente.get("/api/salud").json()["modelo_version"] == actual
    assert cliente.post("/api/diagnostico", files={"foto": ("hoja.jpg", imagen(), "image/jpeg")}).status_code == 200
