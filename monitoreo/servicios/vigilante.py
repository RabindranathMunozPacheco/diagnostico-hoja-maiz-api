"""Vigilante: monitor de data drift y sonda de calidad del modelo.

Monitor de drift (cada PERIODO_DRIFT segundos)
    Lee de Prometheus cuántas fotos de usuarios cayeron en cada bin de brillo, contraste, nitidez y verdor, y de
    P(enferma), durante la VENTANA_DRIFT, y las compara con la línea base de la versión en servicio:
        PSI = suma de (p_prod - p_base) * ln(p_prod / p_base)    más de 0.25 es un cambio importante
        KS  = mayor diferencia entre las distribuciones acumuladas (sobre los bins finos)
    Para el PSI, los bins vecinos se juntan hasta que cada uno tenga al menos MASA_MIN (5 %) de la línea base: un
    bin casi vacío en el entrenamiento infla el PSI con muy pocas fotos. Esto se calibró con tráfico normal: con
    bins finos el PSI máximo por ventana llegaba a 0.28 (p99) sin que hubiera drift; con bins agrupados, 0.18.
    Con menos de MUESTRAS_MIN fotos en la ventana no se calcula (con pocas fotos el PSI sale alto por azar).

Sonda de calidad (cada PERIODO_SONDA segundos)
    Manda las 40 fotos del golden set a la API con la cabecera X-Origen: sonda (no cuentan en los SLO de usuarios
    ni en el drift) y, como se conoce su clase, mide el F1 macro y la proporción de enfermas reportadas como
    sanas. El SLO de calidad se cumple si F1 >= F1_MIN y enfermas como sanas <= FNR_MAX. También manda la
    versión "lluvia" del golden set (conjunto adverso, cada ADVERSO_CADA rondas): estima cuánto le pegaría a la
    calidad un cambio de condiciones, pero no dispara alertas. Las fotos van una por una para que la sonda no
    compita con los usuarios por la CPU. Si más del 20 % de las solicitudes de la sonda fallan, la calidad no se
    evalúa (eso es un problema de disponibilidad y ya lo cubre otra alerta).

Publica todo en /metrics (puerto 9101) y, además, en MLflow (experimento "monitoreo-produccion", una corrida por
versión en servicio), donde queda la historia de la calidad y del drift de cada versión. POST /sondear corre la
sonda en ese momento (operaciones la usa para verificar un rollback).
"""
import csv
import logging
import math
import os
import threading
import time
from pathlib import Path

import httpx
import numpy as np
import uvicorn
from fastapi import FastAPI
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, Gauge, generate_latest

import comun
import espejo_mlflow

PERIODO_DRIFT = float(os.environ.get("PERIODO_DRIFT", 15))
PERIODO_SONDA = float(os.environ.get("PERIODO_SONDA", 30))
VENTANA_DRIFT = os.environ.get("VENTANA_DRIFT", "5m")
MUESTRAS_MIN = int(os.environ.get("MUESTRAS_MIN", 50))
F1_MIN = float(os.environ.get("F1_MIN", 0.60))
FNR_MAX = float(os.environ.get("FNR_MAX", 0.15))
DIR_FOTOS = Path(os.environ.get("DIR_FOTOS", "/datos/fotos"))
ADVERSO_CADA = int(os.environ.get("ADVERSO_CADA", 4))   # el conjunto adverso solo es informativo: cada 4 rondas
EPSILON = 1e-4                       # en bins vacíos: evita dividir entre cero o ln(0) en el PSI
MASA_MIN = float(os.environ.get("MASA_MIN", 0.05))
CLASES = ["Mancha gris", "Roya común", "Sana", "Tizón"]
VARIABLES = {"brillo": "entrada_brillo", "contraste": "entrada_contraste", "nitidez": "entrada_nitidez",
             "verdor": "entrada_verdor", "p_enferma": "modelo_probabilidad_enferma"}

logging.basicConfig(level=logging.INFO, format='{"ts": "%(asctime)s", "nivel": "%(levelname)s", "mensaje": "%(message)s"}')
log = logging.getLogger("vigilante")
logging.getLogger("httpx").setLevel(logging.WARNING)

PSI = Gauge("drift_psi", "PSI de la ventana contra la línea base de la versión en servicio", ["variable"])
KS = Gauge("drift_ks", "KS (sobre bins) de la ventana contra la línea base", ["variable"])
MUESTRAS = Gauge("drift_muestras", "Fotos de usuarios en la ventana del drift")
PROPORCION = Gauge("drift_proporcion_diagnostico", "Proporción de cada diagnóstico en la ventana", ["diagnostico"])
PROPORCION_BASE = Gauge("drift_proporcion_diagnostico_base", "Proporción esperada según la línea base", ["diagnostico"])
LINEA_BASE = Gauge("drift_linea_base_info", "Versión cuya línea base se usa (vale 1)", ["version"])
EVALUACION_DRIFT = Gauge("drift_ultima_evaluacion_timestamp_segundos", "Última evaluación del drift")
SONDA_F1 = Gauge("sonda_f1_macro", "F1 macro de la sonda (4 clases)", ["conjunto", "version"])
SONDA_FNR = Gauge("sonda_enferma_como_sana", "Proporción de enfermas reportadas como sanas", ["conjunto", "version"])
SONDA_SFR = Gauge("sonda_sana_como_enferma", "Proporción de sanas reportadas como enfermas", ["conjunto", "version"])
SONDA_EXACTITUD = Gauge("sonda_exactitud", "Exactitud de la sonda", ["conjunto", "version"])
SONDA_ERRORES = Gauge("sonda_solicitudes_fallidas", "Solicitudes de la sonda que no respondieron 200", ["conjunto"])
SONDA_SLO = Gauge("sonda_slo_cumplido", "1 si se cumple el SLO de calidad, 0 si no (NaN si no se pudo evaluar)",
                  ["conjunto"])
SONDA_TIEMPO = Gauge("sonda_ultima_ejecucion_timestamp_segundos", "Última ejecución de la sonda")
SONDA_UMBRALES = Gauge("sonda_umbral", "Umbrales del SLO de calidad", ["metrica"])
SONDA_UMBRALES.labels(metrica="f1_min").set(F1_MIN)
SONDA_UMBRALES.labels(metrica="enferma_como_sana_max").set(FNR_MAX)

app = FastAPI(title="Vigilante")
_candado_sonda = threading.Lock()
_version_publicada = {}
_mlflow = espejo_mlflow.MonitoreoMLflow()


# ------------------------------------------------------------------------------------------------- drift
def agrupar(base, produccion, masa=MASA_MIN):
    """Junta bins vecinos hasta que cada uno tenga al menos `masa` de la línea base."""
    grupos, acum_base, acum_prod = [], 0.0, 0.0
    for b, p in zip(base, produccion):
        acum_base, acum_prod = acum_base + b, acum_prod + p
        if acum_base >= masa:
            grupos.append((acum_base, acum_prod))
            acum_base = acum_prod = 0.0
    if acum_base > 0 or acum_prod > 0:                   # lo que sobra al final se une al último grupo
        if grupos:
            ultimo = grupos.pop()
            grupos.append((ultimo[0] + acum_base, ultimo[1] + acum_prod))
        else:
            grupos.append((acum_base, acum_prod))
    return np.array([g[0] for g in grupos]), np.array([g[1] for g in grupos])


def psi_y_ks(conteos, esperadas):
    produccion = np.asarray(conteos, dtype=float)
    produccion = produccion / produccion.sum()
    base = np.asarray(esperadas, dtype=float)
    ks = float(np.max(np.abs(np.cumsum(produccion) - np.cumsum(base))))
    q, p = agrupar(base, produccion)
    p, q = np.clip(p, EPSILON, None), np.clip(q, EPSILON, None)
    psi = float(np.sum((p - q) * np.log(p / q)))
    return psi, ks


def version_servida():
    """La versión que la API realmente sirve (modelo_info), no la que dice el registro.

    Casi siempre coinciden, pero en la simulación INC-04 el rollback movió el alias y la recarga de la API falló:
    el registro decía v1 mientras la API servía v2, y el monitoreo le atribuía a la v1 los errores de la v2.
    Si la API no responde, se usa el registro.
    """
    try:
        resultado = comun.consulta('max by (version) (modelo_info{job="api"})')
        if resultado:
            return resultado[0]["metric"]["version"]
    except Exception:                                   # noqa: BLE001 - sin Prometheus se usa el registro
        pass
    return comun.leer_registro()["alias"]["champion"]


def evaluar_drift():
    version = version_servida()
    linea = comun.linea_base(version)
    LINEA_BASE.clear()
    LINEA_BASE.labels(version=version).set(1)
    if linea is None:
        return version, {}
    filtro = 'origen="usuario"'
    n = comun.escalar(f'sum(increase(entrada_brillo_count{{{filtro}}}[{VENTANA_DRIFT}]))') or 0.0
    MUESTRAS.set(n)
    EVALUACION_DRIFT.set(time.time())
    if n < MUESTRAS_MIN:
        for variable in VARIABLES:                      # sin suficientes fotos no hay evaluación
            PSI.labels(variable=variable).set(math.nan)
            KS.labels(variable=variable).set(math.nan)
        return version, {"drift_muestras": n}
    resultado = {"drift_muestras": n}
    for variable, metrica in VARIABLES.items():
        esperadas = (linea["predicciones"]["probabilidad_enferma"] if variable == "p_enferma"
                     else linea["variables"][variable])["proporciones"]
        conteos = [c for _, c in comun.histograma(metrica, filtro, VENTANA_DRIFT)]
        if len(conteos) != len(esperadas) or sum(conteos) == 0:
            continue
        psi, ks = psi_y_ks(conteos, esperadas)
        PSI.labels(variable=variable).set(round(psi, 4))
        KS.labels(variable=variable).set(round(ks, 4))
        resultado[f"psi_{variable}"], resultado[f"ks_{variable}"] = round(psi, 4), round(ks, 4)
    por_diagnostico = comun.consulta(
        f'sum by (diagnostico) (increase(modelo_predicciones_total{{{filtro}}}[{VENTANA_DRIFT}]))')
    total = sum(float(r["value"][1]) for r in por_diagnostico) or 1.0
    for clase in CLASES:
        valor = next((float(r["value"][1]) for r in por_diagnostico if r["metric"].get("diagnostico") == clase), 0.0)
        PROPORCION.labels(diagnostico=clase).set(round(valor / total, 4))
        PROPORCION_BASE.labels(diagnostico=clase).set(linea["predicciones"]["proporcion_por_diagnostico"][clase])
        resultado[f"proporcion_{clase.lower().replace(' ', '_').replace('ó', 'o').replace('í', 'i')}"] = round(valor / total, 4)
    return version, resultado


# ------------------------------------------------------------------------------------------------- sonda
def _golden():
    with open(DIR_FOTOS / "golden" / "clases.csv", encoding="utf-8") as f:
        return [(fila["id"], fila["clase"]) for fila in csv.DictReader(f)]


def f1_macro(reales, predichas):
    valores = []
    for c in CLASES:
        vp = sum(1 for r, p in zip(reales, predichas) if r == c and p == c)
        fp = sum(1 for r, p in zip(reales, predichas) if r != c and p == c)
        fn = sum(1 for r, p in zip(reales, predichas) if r == c and p != c)
        valores.append(2 * vp / (2 * vp + fp + fn) if vp + fp + fn else 0.0)
    return sum(valores) / len(valores)


def _diagnosticar(http, id_foto, condicion):
    ruta = DIR_FOTOS / "golden" / condicion / f"{id_foto}.png"
    try:
        r = http.post("/api/diagnostico", files={"foto": (ruta.name, ruta.read_bytes(), "image/png")},
                      headers={"X-Origen": "sonda"})
        return r.json() if r.status_code == 200 else None
    except httpx.HTTPError:
        return None


def correr_sonda(con_adverso=True):
    with _candado_sonda:
        fotos = _golden()
        resultados = {}
        conjuntos = (("golden", "original"), ("adverso", "lluvia")) if con_adverso else (("golden", "original"),)
        with httpx.Client(base_url=comun.API, timeout=30) as http:
            for conjunto, condicion in conjuntos:
                respuestas = [_diagnosticar(http, f[0], condicion) for f in fotos]
                validas = [(clase, r) for (_, clase), r in zip(fotos, respuestas) if r is not None]
                fallidas = len(fotos) - len(validas)
                SONDA_ERRORES.labels(conjunto=conjunto).set(fallidas)
                anterior = _version_publicada.pop(conjunto, None)
                if anterior is not None:                       # una sola versión vigente por conjunto
                    for g in (SONDA_F1, SONDA_FNR, SONDA_SFR, SONDA_EXACTITUD):
                        g.remove(conjunto, anterior)
                if fallidas > 0.2 * len(fotos):
                    SONDA_SLO.labels(conjunto=conjunto).set(math.nan)
                    resultados[conjunto] = {"evaluada": False, "fallidas": fallidas}
                    continue
                reales = [c for c, _ in validas]
                predichas = [r["clase_mas_probable"] for _, r in validas]
                enfermas = [r for c, r in validas if c != "Sana"]
                sanas = [r for c, r in validas if c == "Sana"]
                version = validas[0][1]["modelo_version"]
                m = {"f1_macro": f1_macro(reales, predichas),
                     "exactitud": sum(r == p for r, p in zip(reales, predichas)) / len(validas),
                     "enferma_como_sana": sum(r["estado"] == "sana" for r in enfermas) / max(len(enfermas), 1),
                     "sana_como_enferma": sum(r["estado"] == "enferma" for r in sanas) / max(len(sanas), 1)}
                SONDA_F1.labels(conjunto=conjunto, version=version).set(round(m["f1_macro"], 4))
                SONDA_EXACTITUD.labels(conjunto=conjunto, version=version).set(round(m["exactitud"], 4))
                SONDA_FNR.labels(conjunto=conjunto, version=version).set(round(m["enferma_como_sana"], 4))
                SONDA_SFR.labels(conjunto=conjunto, version=version).set(round(m["sana_como_enferma"], 4))
                _version_publicada[conjunto] = version
                cumple = m["f1_macro"] >= F1_MIN and m["enferma_como_sana"] <= FNR_MAX
                SONDA_SLO.labels(conjunto=conjunto).set(1 if cumple else 0)
                resultados[conjunto] = {"evaluada": True, "version": version, "fallidas": fallidas,
                                        "slo_cumplido": cumple, **{k: round(v, 4) for k, v in m.items()}}
        SONDA_TIEMPO.set(time.time())
        return resultados


# ------------------------------------------------------------------------------------------ ciclo y HTTP
def ciclo():
    proximo_drift = proxima_sonda = 0.0
    ronda = 0
    while True:
        ahora = time.time()
        try:
            if ahora >= proximo_drift:
                proximo_drift = ahora + PERIODO_DRIFT
                version, metricas = evaluar_drift()
                _mlflow.registrar(version, metricas)
            if ahora >= proxima_sonda:
                proxima_sonda = ahora + PERIODO_SONDA
                r = correr_sonda(con_adverso=ronda % ADVERSO_CADA == 0)
                ronda += 1
                servida = r.get("golden", {}).get("version") or version_servida()
                _mlflow.registrar(servida,
                                  {f"sonda_{c}_{k}": v for c, m in r.items() if m.get("evaluada")
                                   for k, v in m.items() if k in ("f1_macro", "enferma_como_sana",
                                                                   "sana_como_enferma", "exactitud")})
                log.info("sonda: %s", {k: v.get("f1_macro", "sin evaluar") for k, v in r.items()})
        except Exception as e:                          # noqa: BLE001 - el vigilante no debe caerse por un error
            log.warning("ciclo con error: %s", e)
        time.sleep(1)


@app.get("/metrics")
def metricas():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/sondear")
def sondear():
    return correr_sonda(con_adverso=False)


@app.get("/salud")
def salud():
    return {"estado": "ok"}


if __name__ == "__main__":
    threading.Thread(target=ciclo, daemon=True).start()
    uvicorn.run(app, host="0.0.0.0", port=9101, log_level="warning")
