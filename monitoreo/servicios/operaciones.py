"""Operaciones: recibe las alertas de Alertmanager (webhook) y ejecuta la parte automatizada de cada runbook.

Cada alerta trae en sus etiquetas el runbook y la acción automática que le toca (las define monitoreo/slo.py):
    reiniciar               RB-01 servicio caído: arranca o reinicia el contenedor y espera a que esté sano.
                            Máximo 3 reinicios en 10 minutos; después escala a una persona.
    reconciliar_recursos    RB-02 latencia o saturación: compara CPU y memoria del contenedor con el estado
                            deseado (el declarado en docker-compose, como en GitOps) y lo corrige. Si ya
                            coincidían, la causa es otra (más tráfico, modelo más lento) y escala.
    atender_errores         RB-03 quema rápida del presupuesto de errores: junta evidencia (etapa con más
                            errores y trace_ids de Loki). Si hubo una promoción del modelo en los últimos 30
                            minutos hace rollback; si no, reinicia (falla transitoria) y verifica. Si sigue, escala.
    rollback                RB-04 la sonda detecta que la calidad del modelo cayó: regresa el alias champion a la
                            versión anterior, pide a la API que recargue y corre la sonda para confirmarlo.
                            Máximo un rollback cada 15 minutos (evita ciclos de ida y vuelta).
    solicitar_reentrenamiento  RB-05 data drift: no hace rollback (la versión anterior tampoco conoce esos datos);
                            arma una solicitud de reentrenamiento con la evidencia para que una persona la apruebe.
    ninguna                 solo se registra y se notifica.
Todo queda en la bitácora (bitacora/bitacora.jsonl) con marca de tiempo, y en /metrics (puerto 9102).
"""
import json
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import docker
import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

import comun

TOKEN = os.environ.get("TOKEN_ADMIN", "")
CONTENEDOR = os.environ.get("CONTENEDOR_API", "diagnostico-hoja-maiz")
CPUS_DESEADAS = float(os.environ.get("CPUS_DESEADAS", 1.0))
MEMORIA_DESEADA = int(os.environ.get("MEMORIA_DESEADA", 1024 ** 3))
VIGILANTE = os.environ.get("VIGILANTE_URL", "http://vigilante:9101")
LOKI = os.environ.get("LOKI_URL", "http://loki:3100")
DIR_BITACORA = Path(os.environ.get("DIR_BITACORA", "/datos/bitacora"))
VENTANA_DESPLIEGUE_S = 30 * 60
CANALES = {"pagina": "página al responsable de guardia", "ticket": "ticket en la cola de MLOps",
           "registro": "solo registro"}

logging.basicConfig(level=logging.INFO, format='{"ts": "%(asctime)s", "nivel": "%(levelname)s", "mensaje": "%(message)s"}')
log = logging.getLogger("operaciones")

ALERTAS = Counter("operaciones_alertas_recibidas_total", "Alertas recibidas de Alertmanager",
                  ["alertname", "severidad", "estado"])
NOTIFICACIONES = Counter("operaciones_notificaciones_total", "Notificaciones por canal", ["canal"])
ACCIONES = Counter("operaciones_acciones_total", "Acciones automáticas ejecutadas", ["runbook", "accion", "resultado"])
ESCALAMIENTOS = Counter("operaciones_escalamientos_total", "Casos escalados a una persona", ["runbook", "motivo"])
DURACION = Histogram("operaciones_accion_segundos", "Duración de cada acción automática (hasta verificarla)",
                     ["accion"], buckets=(1, 5, 10, 20, 30, 60, 120, 300))
SOLICITUDES = Counter("operaciones_solicitudes_reentrenamiento_total", "Solicitudes de reentrenamiento creadas")

app = FastAPI(title="Operaciones")
ejecutor = ThreadPoolExecutor(1)               # una acción a la vez: nunca un rollback y un reinicio juntos
_atendidos = set()
_reinicios, _rollbacks = [], []
_candado = threading.Lock()


def anotar(incidente, evento, **datos):
    entrada = {"ts": comun.ahora(), "incidente": incidente, "evento": evento, **datos}
    DIR_BITACORA.mkdir(parents=True, exist_ok=True)
    with _candado, open(DIR_BITACORA / "bitacora.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(entrada, ensure_ascii=False) + "\n")
    log.info("%s %s %s", incidente, evento, json.dumps(datos, ensure_ascii=False)[:300])
    return entrada


def escalar(incidente, runbook, motivo):
    ESCALAMIENTOS.labels(runbook=runbook, motivo=motivo[:40]).inc()
    anotar(incidente, "escalamiento", runbook=runbook, motivo=motivo,
           canal=CANALES["pagina"], mensaje="Se requiere intervención humana (ver el runbook)")


def contenedor():
    return docker.from_env().containers.get(CONTENEDOR)


def esperar_sano(cont, limite_s=120):
    inicio = time.time()
    while time.time() - inicio < limite_s:
        cont.reload()
        if cont.attrs["State"].get("Health", {}).get("Status") == "healthy":
            return round(time.time() - inicio, 1)
        time.sleep(2)
    return None


# ---------------------------------------------------------------------------------------------- acciones
def reiniciar(incidente, runbook, alerta):
    ahora = time.time()
    _reinicios[:] = [t for t in _reinicios if ahora - t < 600]
    if len(_reinicios) >= 3:
        return escalar(incidente, runbook, "3 reinicios en 10 minutos sin recuperarse: posible ciclo de caídas")
    cont = contenedor()
    estado = cont.attrs["State"]
    anotar(incidente, "diagnostico", runbook=runbook, estado_contenedor=estado["Status"],
           codigo_salida=estado.get("ExitCode"), oom=estado.get("OOMKilled"),
           salud=estado.get("Health", {}).get("Status"), terminado=estado.get("FinishedAt"))
    if cont.status == "running":
        cont.restart(timeout=10)
        accion = "docker restart"
    else:
        cont.start()
        accion = "docker start"
    _reinicios.append(ahora)
    anotar(incidente, "accion", runbook=runbook, accion=accion)
    segundos = esperar_sano(cont)
    if segundos is None:
        ACCIONES.labels(runbook=runbook, accion="reiniciar", resultado="fallo").inc()
        return escalar(incidente, runbook, "el contenedor no quedó sano en 120 s")
    ACCIONES.labels(runbook=runbook, accion="reiniciar", resultado="ok").inc()
    anotar(incidente, "verificacion", runbook=runbook, resultado="ok", detalle=f"healthy en {segundos} s")


def reconciliar_recursos(incidente, runbook, alerta):
    cont = contenedor()
    hc = cont.attrs["HostConfig"]
    actual = {"cpus": (hc.get("NanoCpus") or 0) / 1e9, "memoria": hc.get("Memory") or 0}
    deseado = {"cpus": CPUS_DESEADAS, "memoria": MEMORIA_DESEADA}
    diferencias = {k: {"actual": actual[k], "deseado": deseado[k]} for k in actual if actual[k] != deseado[k]}
    p95 = comun.escalar('histogram_quantile(0.95, sum by (le) (rate(http_duracion_segundos_bucket'
                        '{ruta="/api/diagnostico",origen="usuario",resultado="ok"}[1m])))')
    anotar(incidente, "diagnostico", runbook=runbook, recursos_actuales=actual, recursos_deseados=deseado,
           latencia_p95_s=p95, en_curso=comun.escalar("http_solicitudes_en_curso"))
    if not diferencias:
        ACCIONES.labels(runbook=runbook, accion="reconciliar_recursos", resultado="sin_cambios").inc()
        return escalar(incidente, runbook, "los recursos ya coinciden con el estado deseado: revisar tráfico y modelo")
    datos = {"NanoCpus": int(CPUS_DESEADAS * 1e9)}
    if "memoria" in diferencias:
        datos.update({"Memory": MEMORIA_DESEADA, "MemorySwap": 2 * MEMORIA_DESEADA})
    api = docker.from_env().api
    api._result(api._post_json(api._url("/containers/{0}/update", cont.id), data=datos), True)
    anotar(incidente, "accion", runbook=runbook, accion="docker update (estado deseado)", cambios=diferencias)
    time.sleep(45)                                      # que entren solicitudes con los recursos ya corregidos
    p95 = comun.escalar('histogram_quantile(0.95, sum by (le) (rate(http_duracion_segundos_bucket'
                        '{ruta="/api/diagnostico",origen="usuario",resultado="ok"}[30s])))')
    ok = p95 is not None and p95 <= 0.5
    ACCIONES.labels(runbook=runbook, accion="reconciliar_recursos", resultado="ok" if ok else "fallo").inc()
    anotar(incidente, "verificacion", runbook=runbook, resultado="ok" if ok else "fallo", latencia_p95_s=p95)
    if not ok:
        escalar(incidente, runbook, "la latencia no bajó después de corregir los recursos")


def evidencia_errores():
    etapas = comun.consulta('sum by (etapa, tipo) (increase(diagnostico_errores_total[5m])) > 0')
    trazas = []
    try:
        r = httpx.get(f"{LOKI}/loki/api/v1/query_range", timeout=10, params={
            "query": '{service_name="diagnostico-hoja-maiz"} | evento="error_interno"', "limit": 5,
            "start": str(int((time.time() - 600) * 1e9))})
        for flujo in r.json()["data"]["result"]:
            if "trace_id" in flujo["stream"]:
                trazas.append(flujo["stream"]["trace_id"])
    except Exception as e:                              # noqa: BLE001 - la evidencia es complementaria
        trazas.append(f"(no se pudo consultar Loki: {e})")
    return {"errores_por_etapa": {f'{e["metric"]["etapa"]}/{e["metric"]["tipo"]}': round(float(e["value"][1]))
                                  for e in etapas},
            "trace_ids_recientes": trazas[:5],
            "proporcion_errores_1m": comun.escalar('sum(rate(http_solicitudes_total{ruta="/api/diagnostico",'
                                                   'origen="usuario",codigo=~"5.."}[1m])) / sum(rate('
                                                   'http_solicitudes_total{ruta="/api/diagnostico",origen="usuario"}[1m]))')}


def promocion_reciente():
    registro = comun.leer_registro()
    for entrada in reversed(registro["historial"]):
        if entrada["evento"] == "promocion":
            hace = time.time() - datetime.fromisoformat(entrada["fecha"]).timestamp()
            return entrada if hace < VENTANA_DESPLIEGUE_S else None
    return None


def atender_errores(incidente, runbook, alerta):
    anotar(incidente, "diagnostico", runbook=runbook, **evidencia_errores())
    promocion = promocion_reciente()
    if promocion:
        anotar(incidente, "decision", runbook=runbook,
               detalle=f"hubo una promoción a v{promocion['a']} hace menos de 30 min: se trata como release malo")
        return rollback(incidente, runbook, alerta)
    anotar(incidente, "decision", runbook=runbook, detalle="sin despliegues recientes: se trata como falla transitoria")
    reiniciar(incidente, runbook, alerta)
    time.sleep(30)
    proporcion = evidencia_errores()["proporcion_errores_1m"]
    ok = proporcion is not None and proporcion < 0.01
    anotar(incidente, "verificacion", runbook=runbook, resultado="ok" if ok else "fallo",
           proporcion_errores_1m=proporcion)
    if not ok:
        escalar(incidente, runbook, "los errores siguen después del reinicio")


def rollback(incidente, runbook, alerta):
    ahora = time.time()
    _rollbacks[:] = [t for t in _rollbacks if ahora - t < 900]
    registro = comun.leer_registro()
    actual, previa = registro["alias"]["champion"], comun.version_anterior(registro)
    anotar(incidente, "diagnostico", runbook=runbook, version_actual=actual, version_anterior=previa)
    if previa is None:
        return escalar(incidente, runbook, "no hay versión anterior a la cual regresar")
    if _rollbacks:
        return escalar(incidente, runbook, "ya hubo un rollback en los últimos 15 minutos: evitar ciclos")
    comun.mover_alias(previa, motivo=f"Rollback automático por la alerta {alerta['labels']['alertname']}",
                      autor="operaciones (automático)", evento="rollback", estado_anterior="rechazada")
    _rollbacks.append(ahora)
    recarga = comun.recargar_api(TOKEN)
    anotar(incidente, "accion", runbook=runbook, accion=f"rollback del alias champion v{actual} -> v{previa}",
           recarga=recarga)
    sonda = httpx.post(f"{VIGILANTE}/sondear", timeout=120).json()
    ok = sonda.get("golden", {}).get("slo_cumplido") is True
    ACCIONES.labels(runbook=runbook, accion="rollback", resultado="ok" if ok else "fallo").inc()
    anotar(incidente, "verificacion", runbook=runbook, resultado="ok" if ok else "fallo", sonda=sonda.get("golden"))
    if not ok:
        escalar(incidente, runbook, "la sonda sigue fallando después del rollback")


def solicitar_reentrenamiento(incidente, runbook, alerta):
    variables = {r["metric"]["variable"]: round(float(r["value"][1]), 3) for r in comun.consulta("drift_psi")
                 if r["value"][1] not in ("NaN",)}
    impacto = {r["metric"]["conjunto"]: round(float(r["value"][1]), 3) for r in comun.consulta("sonda_f1_macro")}
    version = comun.leer_registro()["alias"]["champion"]
    solicitud = {"fecha": comun.ahora(), "incidente": incidente, "estado": "pendiente de aprobación humana",
                 "version_en_servicio": version, "psi_por_variable": variables,
                 "muestras_en_ventana": comun.escalar("drift_muestras"),
                 "f1_sonda": impacto,
                 "decision_automatica": "Sin rollback: la versión anterior tampoco fue entrenada con estas "
                                        "condiciones. Se pide reentrenar con fotos de las condiciones nuevas.",
                 "siguiente_paso": "Validar que no es un error del pipeline de datos (RB-05 paso 2) y aprobar "
                                   "scripts/reentrenar.py"}
    carpeta = DIR_BITACORA / "solicitudes"
    carpeta.mkdir(parents=True, exist_ok=True)
    ruta = carpeta / f"reentrenamiento_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    ruta.write_text(json.dumps(solicitud, ensure_ascii=False, indent=2), encoding="utf-8")
    SOLICITUDES.inc()
    ACCIONES.labels(runbook=runbook, accion="solicitar_reentrenamiento", resultado="ok").inc()
    anotar(incidente, "accion", runbook=runbook, accion="solicitud de reentrenamiento creada",
           archivo=ruta.name, **{k: v for k, v in solicitud.items() if k in ("psi_por_variable", "f1_sonda")})


ACCIONES_POR_NOMBRE = {"reiniciar": reiniciar, "reconciliar_recursos": reconciliar_recursos,
                       "atender_errores": atender_errores, "rollback": rollback,
                       "solicitar_reentrenamiento": solicitar_reentrenamiento}


def ejecutar(nombre, incidente, runbook, alerta):
    inicio = time.time()
    try:
        ACCIONES_POR_NOMBRE[nombre](incidente, runbook, alerta)
    except Exception as e:                              # noqa: BLE001 - si la automatización falla, escala
        log.exception("la acción %s falló", nombre)
        ACCIONES.labels(runbook=runbook, accion=nombre, resultado="error").inc()
        escalar(incidente, runbook, f"la acción automática {nombre} falló: {e}")
    finally:
        DURACION.labels(accion=nombre).observe(time.time() - inicio)


# ------------------------------------------------------------------------------------------------- HTTP
@app.post("/alertas/{canal}")
async def recibir(canal: str, request: Request):
    carga = await request.json()
    for alerta in carga.get("alerts", []):
        etiquetas = alerta["labels"]
        nombre, severidad = etiquetas.get("alertname"), etiquetas.get("severidad", "?")
        incidente = f"{nombre}@{alerta['startsAt'][:19]}"
        ALERTAS.labels(alertname=nombre, severidad=severidad, estado=alerta["status"]).inc()
        NOTIFICACIONES.labels(canal=canal).inc()
        if alerta["status"] == "resolved":
            anotar(incidente, "alerta_resuelta", alerta=nombre, severidad=severidad, inicio=alerta["startsAt"],
                   fin=alerta.get("endsAt"))
            _atendidos.discard(incidente)
            continue
        if incidente in _atendidos:                    # Alertmanager la repite (repeat_interval): no se re-ejecuta
            anotar(incidente, "notificacion_repetida", alerta=nombre)
            continue
        _atendidos.add(incidente)
        accion, runbook = etiquetas.get("accion", "ninguna"), etiquetas.get("runbook", "-")
        anotar(incidente, "alerta_recibida", alerta=nombre, severidad=severidad, runbook=runbook, accion=accion,
               canal=CANALES.get(canal, canal), inicio=alerta["startsAt"],
               resumen=alerta.get("annotations", {}).get("summary"))
        if accion in ACCIONES_POR_NOMBRE:
            ejecutor.submit(ejecutar, accion, incidente, runbook, alerta)
    return {"recibidas": len(carga.get("alerts", []))}


@app.get("/bitacora")
def bitacora(ultimas: int = 200):
    ruta = DIR_BITACORA / "bitacora.jsonl"
    if not ruta.exists():
        return []
    return [json.loads(l) for l in ruta.read_text(encoding="utf-8").splitlines()[-ultimas:]]


@app.get("/metrics")
def metricas():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/salud")
def salud():
    return {"estado": "ok"}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=9102, log_level="warning")
