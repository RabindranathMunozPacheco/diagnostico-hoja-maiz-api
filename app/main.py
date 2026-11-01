"""API REST del diagnóstico de hoja de maíz (FastAPI).

Endpoints:
    GET  /api/salud        estado del servicio (lo usa el HEALTHCHECK del contenedor)
    GET  /api/modelo       ficha del modelo: origen, clases, umbral y métricas de prueba
    POST /api/diagnostico  recibe una foto (multipart, campo "foto") y regresa el diagnóstico
    GET  /                 página web que consume la API
    GET  /docs             documentación interactiva generada por FastAPI

Uso local:
    uvicorn app.main:app --port 8000
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.config import cargar_config
from app.esquemas import Diagnostico, Error, Salud
from app.modelo import Diagnosticador, ImagenInvalida

log = logging.getLogger("diagnostico")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

config = cargar_config()
NOMBRE_MODELO = f"{config.ficha['nombre']} v{config.ficha['version_registrada']}"


@asynccontextmanager
async def ciclo_de_vida(app):
    # El modelo se carga una sola vez al arrancar; si falla, el servicio no arranca
    app.state.diagnosticador = Diagnosticador(config)
    log.info("Modelo %s cargado (umbral P(enferma) >= %s)", NOMBRE_MODELO, config.umbral_enferma)
    yield


app = FastAPI(title="Diagnóstico de hoja de maíz",
              description="Clasifica una foto de hoja de maíz en Sana, Mancha gris, Tizón o Roya común.",
              version=__version__, lifespan=ciclo_de_vida)


@app.get("/api/salud", response_model=Salud, tags=["servicio"], summary="Estado del servicio")
def salud():
    return {"estado": "ok", "modelo": NOMBRE_MODELO, "version_api": __version__}


@app.get("/api/modelo", tags=["servicio"], summary="Ficha del modelo en uso")
def modelo():
    return {**config.ficha, "umbral_en_uso": config.umbral_enferma, "tamano_max_mb": config.tamano_max_mb}


@app.post("/api/diagnostico", response_model=Diagnostico, tags=["diagnóstico"],
          summary="Diagnosticar la foto de una hoja",
          responses={413: {"model": Error, "description": "Archivo demasiado grande"},
                     415: {"model": Error, "description": "El archivo no es una imagen"}})
def diagnostico(request: Request, foto: UploadFile = File(description="Foto de la hoja (JPG o PNG)")):
    contenido = foto.file.read()
    if len(contenido) > config.tamano_max_mb * 1024 * 1024:
        raise HTTPException(413, f"la foto pesa más de {config.tamano_max_mb:g} MB")
    try:
        resultado = request.app.state.diagnosticador.diagnosticar(contenido)
    except ImagenInvalida as e:
        log.warning("Rechazada %s: %s", foto.filename, e)
        raise HTTPException(415, str(e))
    log.info("%s -> %s (P(enferma) = %.3f, %.0f ms)", foto.filename, resultado["diagnostico"],
             resultado["probabilidad_enferma"], resultado["tiempo_ms"])
    return {"archivo": foto.filename, **resultado}


# La página web se sirve desde el mismo servicio: el navegador y la API comparten origen
app.mount("/static", StaticFiles(directory=config.dir_frontend), name="static")


@app.get("/", include_in_schema=False)
def inicio():
    return FileResponse(config.dir_frontend / "index.html")
