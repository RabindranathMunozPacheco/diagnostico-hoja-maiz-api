"""Las tres señales de observabilidad del servicio: métricas, logs y trazas.

Métricas (Prometheus): se definen aquí y se publican en GET /metrics. Prometheus las lee cada pocos segundos.
    http_*         tráfico, códigos y latencia de cada ruta (señales doradas: tráfico, errores y latencia)
    diagnostico_*  duración de cada etapa del diagnóstico, errores internos por etapa y fotos rechazadas
    modelo_*       versión en servicio, diagnósticos por clase, P(enferma) y confianza (calidad del modelo sin etiquetas)
    entrada_*      brillo, contraste, nitidez y verdor de las fotos (para detectar data drift)
    process_*      CPU y memoria del proceso (saturación); las agrega prometheus_client en Linux
Toda métrica de tráfico lleva la etiqueta origen: "usuario" o "sonda" (el monitor que manda fotos de prueba con
la cabecera X-Origen: sonda). Los SLO y el drift se calculan solo con el tráfico de usuarios.

Logs: JSON, una línea por evento, con trace_id y span_id para pasar del log a la traza. Salen por la salida
estándar (docker logs) y, si OTEL_EXPORTER_OTLP_LOGS_ENDPOINT está definido, también a Loki por OTLP. Nunca se
registra la foto: solo sus medidas (tamaño, brillo, etc.) y el resultado.

Trazas (OpenTelemetry): una traza por solicitud con un span por etapa (abrir imagen, calidad, extraer
características, clasificar). Si OTEL_EXPORTER_OTLP_TRACES_ENDPOINT está definido se mandan a Jaeger por
OTLP/HTTP; si no (pruebas, uso local), igual se crean para que los logs lleven trace_id. La respuesta incluye la
cabecera X-Trace-Id, así quien reporta un problema puede dar el identificador exacto de su solicitud.
"""
import json
import logging
import os
import sys
import time
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone

from opentelemetry import propagate, trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import SpanKind, Status, StatusCode
from prometheus_client import Counter, Gauge, Histogram

from app.calidad import BINS, VARIABLES

SERVICIO = "diagnostico-hoja-maiz"
tracer = trace.get_tracer(SERVICIO)
log = logging.getLogger("diagnostico")

# --------------------------------------------------------------------------------------------- métricas
_LATENCIA = (0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30)          # 0.5 s es el límite del SLO de latencia
BUCKETS_PROB = tuple(round(0.1 * i, 1) for i in range(1, 10))
BUCKETS_CONFIANZA = (0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95)

SOLICITUDES = Counter("http_solicitudes_total", "Solicitudes HTTP atendidas",
                      ["ruta", "metodo", "codigo", "origen"])
DURACION = Histogram("http_duracion_segundos", "Duración de las solicitudes HTTP",
                     ["ruta", "metodo", "resultado", "origen"], buckets=_LATENCIA)
EN_CURSO = Gauge("http_solicitudes_en_curso", "Solicitudes que se están atendiendo en este momento")
ETAPA = Histogram("diagnostico_etapa_segundos", "Duración de cada etapa del diagnóstico", ["etapa"],
                  buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10))
ERRORES = Counter("diagnostico_errores_total", "Errores internos (respuestas 5xx) por etapa", ["etapa", "tipo"])
RECHAZOS = Counter("diagnostico_rechazos_total", "Fotos rechazadas por el cliente (4xx) por motivo",
                   ["motivo", "origen"])
PREDICCIONES = Counter("modelo_predicciones_total", "Diagnósticos entregados",
                       ["version", "diagnostico", "estado", "origen"])
POR_UMBRAL = Counter("modelo_decididos_por_umbral_total",
                     "Diagnósticos donde el umbral cambió 'Sana' por enferma", ["version", "origen"])
PROB_ENFERMA = Histogram("modelo_probabilidad_enferma", "P(enferma) de cada diagnóstico", ["version", "origen"],
                         buckets=BUCKETS_PROB)
CONFIANZA = Histogram("modelo_confianza", "Probabilidad de la clase más probable", ["version", "origen"],
                      buckets=BUCKETS_CONFIANZA)
ENTRADA = {v: Histogram(f"entrada_{v}", f"{v.capitalize()} de las fotos recibidas (ver app/calidad.py)",
                        ["origen"], buckets=BINS[v]) for v in VARIABLES}
FUERA_DE_RANGO = Counter("entrada_fuera_de_rango_total",
                         "Fotos con una característica fuera del 99 % central de la línea base", ["variable", "origen"])
MODELO_INFO = Gauge("modelo_info", "Versión del modelo en servicio (vale 1)",
                    ["nombre", "version", "alias", "sha256", "umbral"])
MODELO_CARGADO = Gauge("modelo_cargado_timestamp_segundos", "Momento en que se cargó la versión en servicio")
RECARGAS = Counter("modelo_recargas_total", "Recargas del modelo pedidas por la administración", ["resultado"])
FALLA_ACTIVA = Gauge("caos_falla_activa", "Falla inyectada activa (solo en el entorno de simulación)",
                     ["etapa", "tasa", "latencia_ms"])


def publicar_modelo(version, alias, umbral):
    MODELO_INFO.clear()
    MODELO_INFO.labels(nombre=version.ficha["nombre"], version=version.version, alias=alias,
                       sha256=version.ficha["sha256"][:12], umbral=f"{umbral:g}").set(1)
    MODELO_CARGADO.set(time.time())


# ------------------------------------------------------------------------------------------------- logs
_CAMPOS_ESTANDAR = set(vars(logging.makeLogRecord({}))) | {"message", "asctime", "taskName", "color_message"}


def _extras(record):
    return {k: v for k, v in vars(record).items() if k not in _CAMPOS_ESTANDAR and not k.startswith("_")}


class FormatoJSON(logging.Formatter):
    def format(self, record):
        datos = {"ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
                 "nivel": record.levelname, "logger": record.name, "mensaje": record.getMessage(),
                 "servicio": SERVICIO}
        contexto = trace.get_current_span().get_span_context()
        if contexto.is_valid:
            datos["trace_id"] = format(contexto.trace_id, "032x")
            datos["span_id"] = format(contexto.span_id, "016x")
        datos.update(_extras(record))
        if record.exc_info:
            datos["excepcion"] = self.formatException(record.exc_info)
        return json.dumps(datos, ensure_ascii=False, default=str)


def configurar(version_api, entorno):
    """Logs en JSON a la salida estándar y exportadores OTLP de trazas y logs si hay destino configurado."""
    salida = logging.StreamHandler(sys.stdout)
    salida.setFormatter(FormatoJSON())
    raiz = logging.getLogger()
    raiz.handlers[:] = [salida]
    raiz.setLevel(logging.INFO)
    for nombre in ("uvicorn", "uvicorn.error"):           # los logs propios de uvicorn también en JSON
        logging.getLogger(nombre).handlers[:] = []
        logging.getLogger(nombre).propagate = True
    logging.getLogger("uvicorn.access").disabled = True    # el middleware ya registra cada solicitud

    recurso = Resource.create({"service.name": SERVICIO, "service.version": version_api,
                               "deployment.environment": entorno})
    proveedor = TracerProvider(resource=recurso)
    if os.environ.get("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"):
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        proveedor.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(proveedor)

    if os.environ.get("OTEL_EXPORTER_OTLP_LOGS_ENDPOINT"):
        from opentelemetry._logs import set_logger_provider
        from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
        from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
        from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
        proveedor_logs = LoggerProvider(resource=recurso)
        proveedor_logs.add_log_record_processor(BatchLogRecordProcessor(OTLPLogExporter()))
        set_logger_provider(proveedor_logs)
        raiz.addHandler(LoggingHandler(level=logging.INFO, logger_provider=proveedor_logs))


def trace_id_actual():
    contexto = trace.get_current_span().get_span_context()
    return format(contexto.trace_id, "032x") if contexto.is_valid else None


# ---------------------------------------------------------------------------------------------- trazas
class ErrorEnEtapa(RuntimeError):
    """Error interno durante una etapa del diagnóstico (se responde 500 y cuenta contra el SLO)."""

    def __init__(self, etapa, causa):
        super().__init__(f"falló la etapa {etapa}: {causa}")
        self.etapa, self.causa = etapa, causa


@contextmanager
def etapa(nombre, caos=None, **atributos):
    """Span + duración de una etapa. Si hay una falla inyectada para la etapa, se aplica aquí."""
    inicio = time.perf_counter()
    with tracer.start_as_current_span(nombre, attributes=atributos) as span:
        try:
            if caos is not None:
                caos.aplicar(nombre)
            yield span
        except Exception as e:                           # noqa: BLE001 - se re-lanza con la etapa identificada
            span.record_exception(e)
            if getattr(e, "es_del_cliente", False):     # foto inválida: es un 4xx, no un error del servicio
                raise
            span.set_status(Status(StatusCode.ERROR, str(e)))
            if isinstance(e, ErrorEnEtapa):
                raise
            raise ErrorEnEtapa(nombre, e) from e
        finally:
            ETAPA.labels(etapa=nombre).observe(time.perf_counter() - inicio)


# ------------------------------------------------------------------------------------------- middleware
_SIN_LOG = {"/metrics", "/api/salud"}                    # Prometheus y el healthcheck: cada pocos segundos


class MiddlewareObservabilidad:
    """Middleware ASGI: métricas HTTP, la cabecera X-Trace-Id y un log por solicitud.

    El span de servidor lo crea FastAPI (desde la 0.14x trae OpenTelemetry nativo, con spans de dependencias,
    endpoint y serialización); aquí solo se le agrega el origen. Si el span nativo no existe, se crea uno propio.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        cabeceras = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        origen = "sonda" if cabeceras.get("x-origen", "").lower() == "sonda" else "usuario"
        metodo = scope["method"]
        estado = {"codigo": 500}
        inicio = time.perf_counter()
        actual = trace.get_current_span()
        propio = not actual.get_span_context().is_valid
        contexto = (tracer.start_as_current_span(f"{metodo} {scope['path']}", kind=SpanKind.SERVER,
                                                 context=propagate.extract(cabeceras))
                    if propio else nullcontext(actual))
        EN_CURSO.inc()
        with contexto as span:
            trace_id = format(span.get_span_context().trace_id, "032x")
            span.set_attribute("app.origen", origen)

            async def enviar(mensaje):
                if mensaje["type"] == "http.response.start":
                    estado["codigo"] = mensaje["status"]
                    mensaje = {**mensaje, "headers": [*mensaje.get("headers", []), (b"x-trace-id", trace_id.encode())]}
                await send(mensaje)

            try:
                await self.app(scope, receive, enviar)
            finally:
                EN_CURSO.dec()
                duracion = time.perf_counter() - inicio
                ruta = getattr(scope.get("route"), "path", None)
                if ruta is None:                          # archivos estáticos o rutas que no existen
                    ruta = "/static" if scope["path"].startswith("/static") else "sin_ruta"
                codigo = estado["codigo"]
                resultado = "ok" if codigo < 400 else ("rechazo" if codigo < 500 else "error")
                if propio:
                    span.update_name(f"{metodo} {ruta}")
                    span.set_attribute("http.route", ruta)
                    span.set_attribute("http.response.status_code", codigo)
                    if codigo >= 500:
                        span.set_status(Status(StatusCode.ERROR))
                SOLICITUDES.labels(ruta=ruta, metodo=metodo, codigo=str(codigo), origen=origen).inc()
                DURACION.labels(ruta=ruta, metodo=metodo, resultado=resultado, origen=origen).observe(duracion)
                if ruta not in _SIN_LOG:
                    nivel = logging.ERROR if codigo >= 500 else (logging.WARNING if codigo >= 400 else logging.INFO)
                    log.log(nivel, "%s %s -> %s", metodo, ruta, codigo,
                            extra={"evento": "solicitud", "ruta": ruta, "metodo": metodo, "codigo": codigo,
                                   "duracion_ms": round(duracion * 1000, 1), "origen": origen})
