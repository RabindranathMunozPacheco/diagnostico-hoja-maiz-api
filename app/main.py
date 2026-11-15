"""API REST del diagnóstico de hoja de maíz (FastAPI).

Endpoints:
    GET    /api/salud            estado del servicio y versión del modelo (lo usa el HEALTHCHECK del contenedor)
    GET    /api/modelo           ficha del modelo en servicio: origen, clases, umbral y métricas de prueba
    POST   /api/diagnostico      recibe una foto (multipart, campo "foto") y regresa el diagnóstico
    GET    /metrics              métricas para Prometheus (app/observabilidad.py)
    POST   /api/admin/recargar   vuelve a leer modelo/registro.json y carga la versión del alias (promover o rollback)
    POST   /api/admin/fallas     inyecta una falla para simular un incidente (solo con CAOS_HABILITADO=1)
    DELETE /api/admin/fallas     quita la falla inyectada
    GET    /                     página web que consume la API
    GET    /docs                 documentación interactiva generada por FastAPI
Los endpoints /api/admin/* piden la cabecera X-Token-Admin con el valor de TOKEN_ADMIN; sin esa variable no
están disponibles.

Uso local:
    uvicorn app.main:app --port 8000
"""
import hmac
import logging
import threading
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app import __version__
from app import observabilidad as obs
from app.caos import Caos
from app.config import cargar_config, cargar_version
from app.esquemas import Diagnostico, Error, FallaPedida, Salud
from app.modelo import Diagnosticador, ImagenInvalida

config = cargar_config()
obs.configurar(__version__, config.entorno)
log = logging.getLogger("diagnostico")
caos = Caos(config.caos_habilitado)
_recarga = threading.Lock()


def nuevo_diagnosticador(anterior=None):
    version = cargar_version(config)
    umbral = config.umbral(version)
    diagnosticador = Diagnosticador(version, umbral, config.alias, caos,
                                    extractor=anterior.extractor if anterior else None)
    obs.publicar_modelo(version, config.alias, umbral)
    return diagnosticador


@asynccontextmanager
async def ciclo_de_vida(app):
    # El modelo se carga una sola vez al arrancar; si falla, el servicio no arranca
    app.state.diagnosticador = d = nuevo_diagnosticador()
    log.info("Modelo %s cargado (alias %s, umbral P(enferma) >= %s)", d.version.nombre, config.alias, d.umbral,
             extra={"evento": "modelo_cargado", "modelo_version": d.version.version, "umbral": d.umbral,
                    "caos_habilitado": config.caos_habilitado})
    yield


app = FastAPI(title="Diagnóstico de hoja de maíz",
              description="Clasifica una foto de hoja de maíz en Sana, Mancha gris, Tizón o Roya común.",
              version=__version__, lifespan=ciclo_de_vida)
app.add_middleware(obs.MiddlewareObservabilidad)


@app.get("/api/salud", response_model=Salud, tags=["servicio"], summary="Estado del servicio")
def salud(request: Request):
    d = request.app.state.diagnosticador
    falla = caos.activa()
    return {"estado": "ok", "modelo": d.version.nombre, "modelo_version": d.version.version, "alias": d.alias,
            "version_api": __version__, "falla_inyectada": falla.describir() if falla else None}


@app.get("/api/modelo", tags=["servicio"], summary="Ficha del modelo en uso")
def modelo(request: Request):
    d = request.app.state.diagnosticador
    return {**d.version.ficha, "version_en_servicio": d.version.version, "alias": d.alias,
            "umbral_en_uso": d.umbral, "tamano_max_mb": config.tamano_max_mb,
            "linea_base": bool(d.version.linea_base)}


@app.post("/api/diagnostico", response_model=Diagnostico, tags=["diagnóstico"],
          summary="Diagnosticar la foto de una hoja",
          responses={413: {"model": Error, "description": "Archivo demasiado grande"},
                     415: {"model": Error, "description": "El archivo no es una imagen"},
                     500: {"model": Error, "description": "Error interno (se registra con su trace_id)"}})
def diagnostico(request: Request, foto: UploadFile = File(description="Foto de la hoja (JPG o PNG)"),
                x_origen: str | None = Header(None, include_in_schema=False)):
    origen = "sonda" if (x_origen or "").lower() == "sonda" else "usuario"
    contenido = foto.file.read()
    if len(contenido) > config.tamano_max_mb * 1024 * 1024:
        obs.RECHAZOS.labels(motivo="muy_grande", origen=origen).inc()
        raise HTTPException(413, f"la foto pesa más de {config.tamano_max_mb:g} MB")
    d = request.app.state.diagnosticador            # se toma una vez: una recarga a media solicitud no la afecta
    try:
        r = d.diagnosticar(contenido)
    except ImagenInvalida as e:
        obs.RECHAZOS.labels(motivo="no_es_imagen", origen=origen).inc()
        log.warning("Rechazada %s: %s", foto.filename, e, extra={"evento": "rechazo", "motivo": str(e),
                                                                  "origen": origen})
        raise HTTPException(415, str(e))
    except obs.ErrorEnEtapa as e:
        obs.ERRORES.labels(etapa=e.etapa, tipo=type(e.causa).__name__).inc()
        log.error("Error interno en la etapa %s: %s", e.etapa, e.causa,
                  extra={"evento": "error_interno", "etapa": e.etapa, "tipo": type(e.causa).__name__,
                         "modelo_version": d.version.version, "origen": origen})
        raise HTTPException(500, f"error interno en {e.etapa}; reporte el trace_id {obs.trace_id_actual()}")

    v = d.version.version
    obs.PREDICCIONES.labels(version=v, diagnostico=r["diagnostico"], estado=r["estado"], origen=origen).inc()
    obs.PROB_ENFERMA.labels(version=v, origen=origen).observe(r["probabilidad_enferma"])
    obs.CONFIANZA.labels(version=v, origen=origen).observe(max(r["probabilidades"].values()))
    if r["decidido_por_umbral"]:
        obs.POR_UMBRAL.labels(version=v, origen=origen).inc()
    for variable, valor in r["calidad_imagen"].items():
        obs.ENTRADA[variable].labels(origen=origen).observe(valor)
    for variable in r["fuera_de_rango"]:
        obs.FUERA_DE_RANGO.labels(variable=variable, origen=origen).inc()
    log.info("%s -> %s (P(enferma) = %.3f, %.0f ms)", foto.filename, r["diagnostico"], r["probabilidad_enferma"],
             r["tiempo_ms"], extra={"evento": "diagnostico", "diagnostico": r["diagnostico"], "estado": r["estado"],
                                    "p_enferma": r["probabilidad_enferma"], "modelo_version": v,
                                    "tiempo_ms": r["tiempo_ms"], "origen": origen,
                                    "fuera_de_rango": ",".join(r["fuera_de_rango"]),
                                    **{f"calidad_{k}": val for k, val in r["calidad_imagen"].items()}})
    return {"archivo": foto.filename, **r}


@app.get("/metrics", include_in_schema=False)
def metricas():
    if caos.activa() is None:                        # la falla venció sola: que la métrica lo refleje
        obs.FALLA_ACTIVA.clear()
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


# ----------------------------------------------------------------------------------------- administración
def requiere_token(x_token_admin: str | None = Header(None)):
    if not config.token_admin:
        raise HTTPException(404, "administración deshabilitada (falta TOKEN_ADMIN)")
    if not x_token_admin or not hmac.compare_digest(x_token_admin, config.token_admin):
        raise HTTPException(401, "token de administración inválido")


@app.post("/api/admin/recargar", tags=["administración"], dependencies=[Depends(requiere_token)],
          summary="Cargar la versión a la que apunta el alias (promover o regresar)")
def recargar(request: Request):
    with _recarga:
        anterior = request.app.state.diagnosticador
        try:
            nuevo = nuevo_diagnosticador(anterior)
        except Exception as e:                       # noqa: BLE001 - el servicio sigue con la versión anterior
            obs.RECARGAS.labels(resultado="error").inc()
            obs.publicar_modelo(anterior.version, anterior.alias, anterior.umbral)
            log.error("No se pudo recargar el modelo: %s", e, extra={"evento": "recarga_fallida",
                                                                     "modelo_version": anterior.version.version})
            raise HTTPException(500, f"no se pudo recargar: {e}; sigue la versión {anterior.version.version}")
        request.app.state.diagnosticador = nuevo
        obs.RECARGAS.labels(resultado="ok").inc()
    log.warning("Modelo recargado: v%s -> v%s", anterior.version.version, nuevo.version.version,
                extra={"evento": "modelo_recargado", "version_anterior": anterior.version.version,
                       "modelo_version": nuevo.version.version, "umbral": nuevo.umbral})
    return {"version_anterior": anterior.version.version, "version_actual": nuevo.version.version,
            "alias": nuevo.alias, "umbral": nuevo.umbral}


@app.post("/api/admin/fallas", tags=["administración"], dependencies=[Depends(requiere_token)],
          summary="Inyectar una falla (simulación de incidentes)")
def inyectar_falla(pedida: FallaPedida):
    try:
        falla = caos.activar(pedida.etapa, pedida.tasa, pedida.latencia_ms, pedida.duracion_s)
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except ValueError as e:
        raise HTTPException(422, str(e))
    obs.FALLA_ACTIVA.clear()
    obs.FALLA_ACTIVA.labels(etapa=falla.etapa, tasa=f"{falla.tasa:g}", latencia_ms=str(falla.latencia_ms)).set(1)
    log.warning("Falla inyectada en %s", falla.etapa, extra={"evento": "falla_inyectada", **falla.describir()})
    return falla.describir()


@app.delete("/api/admin/fallas", tags=["administración"], dependencies=[Depends(requiere_token)],
            summary="Quitar la falla inyectada")
def quitar_falla():
    anterior = caos.desactivar()
    obs.FALLA_ACTIVA.clear()
    log.warning("Falla retirada", extra={"evento": "falla_retirada", "habia_falla": anterior is not None})
    return {"falla_retirada": anterior.describir() if anterior else None}


# La página web se sirve desde el mismo servicio: el navegador y la API comparten origen
app.mount("/static", StaticFiles(directory=config.dir_frontend), name="static")


@app.get("/", include_in_schema=False)
def inicio():
    return FileResponse(config.dir_frontend / "index.html")
