"""Capturas y exportes de las herramientas para la evidencia de implementación (después de la simulación).

Deja en evidencias/monitoreo/capturas/:
    mlflow_*.png        Model Registry, monitoreo por versión, reentrenamiento, despliegues y respuesta a incidentes
    mlflow_monitoreo_exportado.png / .csv   historia de métricas exportada de MLflow (get_metric_history)
    grafana_tablero_*.png   el tablero operativo en el momento de cada incidente (la tabla de alertas activas
                            muestra las que estaban disparadas en ese instante)
    jaeger_*.png        una traza con error del INC-03 y una normal
    loki_*.png          logs de errores en Grafana Explore
    prometheus_*.png    reglas de alerta y la tasa de quema
Uso:
    python simulacion/capturar_evidencia.py
"""
import csv
import json
import sys
import urllib.parse
from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from mlflow import MlflowClient  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from capturas import capturar  # noqa: E402

RAIZ = Path(__file__).resolve().parents[1]
SIM = RAIZ / "evidencias" / "monitoreo" / "simulacion"
SALIDA = RAIZ / "evidencias" / "monitoreo" / "capturas"
MLFLOW, GRAFANA, JAEGER, PROM = "http://localhost:5000", "http://localhost:3000", "http://localhost:16686", "http://localhost:9090"
NOMBRE = "clasificador-hoja-maiz"


def ms(texto):
    return int(datetime.fromisoformat(texto.replace("Z", "+00:00")).timestamp() * 1000)


def mlflow():
    cliente = MlflowClient(MLFLOW)
    capturar(f"{MLFLOW}/#/models/{NOMBRE}", SALIDA / "mlflow_registro.png", 1600, 900)
    for mv in cliente.search_model_versions(f"name='{NOMBRE}'"):
        if mv.tags.get("version_registro") == "3" or mv.version == "3":
            capturar(f"{MLFLOW}/#/models/{NOMBRE}/versions/{mv.version}", SALIDA / "mlflow_version_3.png", 1600, 1100)
    for nombre in ("monitoreo-produccion", "despliegues", "respuesta-a-incidentes", "reentrenamiento",
                   "versiones-del-modelo"):
        exp = cliente.get_experiment_by_name(nombre)
        if not exp:
            continue
        capturar(f"{MLFLOW}/#/experiments/{exp.experiment_id}/runs", SALIDA / f"mlflow_{nombre}.png", 1700, 900)
        if nombre == "monitoreo-produccion":
            ruta = capturar(f"{MLFLOW}/#/experiments/{exp.experiment_id}/runs?compareRunsMode=CHART",
                            SALIDA / "mlflow_monitoreo_graficas.png", 1700, 3300, 40000)
            recortar_graficas(ruta)
            exportar_monitoreo(cliente, exp.experiment_id)


DETALLES = {   # corrida de MLflow -> captura de su página de detalle (parámetros, métricas y etiquetas)
    ("despliegues", "v1 -> v3 (promovida)"): "mlflow_detalle_despliegue_v3.png",
    ("respuesta-a-incidentes", "RB-04 rollback"): "mlflow_detalle_rb04_rollback.png",
    ("reentrenamiento", "v1 -> candidata v3"): "mlflow_detalle_reentrenamiento_v3.png",
}


def mlflow_detalle():
    """La lista de corridas solo muestra nombres; el detalle muestra qué se registra de cada evento."""
    cliente = MlflowClient(MLFLOW)
    for (experimento, corrida), archivo in DETALLES.items():
        exp = cliente.get_experiment_by_name(experimento)
        run = next((r for r in cliente.search_runs([exp.experiment_id]) if r.info.run_name == corrida), None) if exp else None
        if run:
            capturar(f"{MLFLOW}/#/experiments/{exp.experiment_id}/runs/{run.info.run_id}", SALIDA / archivo, 1700, 1500)


def recortar_graficas(ruta):
    """De la vista de gráficas (orden alfabético) se queda con el encabezado y las filas de PSI y de la sonda."""
    from PIL import Image
    im = Image.open(ruta)
    encabezado = im.crop((0, 0, 1260, 285))
    cuerpo = im.crop((0, 1752, 1260, 3270))
    salida = Image.new("RGB", (1260, encabezado.height + cuerpo.height), "white")
    salida.paste(encabezado, (0, 0))
    salida.paste(cuerpo, (0, encabezado.height))
    salida.save(ruta.with_name("mlflow_monitoreo_graficas_psi_sonda.png"))


def exportar_monitoreo(cliente, experimento):
    """Exporta de MLflow la historia de métricas de cada corrida de monitoreo y la grafica."""
    corridas = sorted(cliente.search_runs([experimento]), key=lambda r: r.info.start_time)
    claves = ["sonda_golden_f1_macro", "sonda_golden_enferma_como_sana", "psi_nitidez", "psi_brillo"]
    filas = []
    fig, ejes = plt.subplots(len(claves), 1, figsize=(10, 9), sharex=True)
    for r in corridas:
        for eje, clave in zip(ejes, claves):
            historia = cliente.get_metric_history(r.info.run_id, clave)
            if not historia:
                continue
            x = [datetime.fromtimestamp(h.timestamp / 1000) for h in historia]
            eje.plot(x, [h.value for h in historia], lw=1.6, label=r.info.run_name)
            filas += [{"corrida": r.info.run_name, "metrica": clave, "timestamp": h.timestamp, "paso": h.step,
                       "valor": h.value} for h in historia]
    umbrales = {"sonda_golden_f1_macro": 0.60, "sonda_golden_enferma_como_sana": 0.15, "psi_nitidez": 0.25,
                "psi_brillo": 0.25}
    for eje, clave in zip(ejes, claves):
        eje.axhline(umbrales[clave], color="gray", ls="--", lw=1)
        eje.set_title(clave, fontsize=9, loc="left")
        eje.grid(alpha=0.25)
        eje.legend(fontsize=7, loc="upper left", ncol=4)
    fig.suptitle("Monitoreo del modelo exportado de MLflow (una corrida por versión en servicio)", fontsize=11)
    fig.tight_layout()
    fig.savefig(SALIDA / "mlflow_monitoreo_exportado.png", dpi=150)
    plt.close(fig)
    with open(SALIDA / "mlflow_monitoreo_exportado.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["corrida", "metrica", "timestamp", "paso", "valor"])
        w.writeheader()
        w.writerows(filas)


def grafana():
    for p in sorted(SIM.glob("INC-0?.json")):
        inc = json.loads(p.read_text(encoding="utf-8"))
        if not inc.get("t_alerta_alertmanager"):
            continue
        hasta = ms(inc["t_alerta_alertmanager"]) + 20000            # 20 s después de la alerta: sigue activa
        capturar(f"{GRAFANA}/d/operativo-maiz/?orgId=1&kiosk&from={hasta - 15 * 60000}&to={hasta}",
                 SALIDA / f"grafana_tablero_{inc['id'].lower().replace('-', '')}.png", 1800, 2420, 30000)
    resumen = json.loads((SIM / "resumen.json").read_text(encoding="utf-8"))
    capturar(f"{GRAFANA}/d/operativo-maiz/?orgId=1&kiosk&from={ms(resumen['inicio'])}&to={ms(resumen['fin'])}",
             SALIDA / "grafana_tablero_simulacion_completa.png", 1800, 2420, 30000)


def trazas_y_logs():
    inc = json.loads((SIM / "INC-03.json").read_text(encoding="utf-8"))
    ids = [t for e in inc["bitacora"] for t in e.get("trace_ids_recientes", []) if len(t) == 32]
    if ids:
        capturar(f"{JAEGER}/trace/{ids[0]}", SALIDA / "jaeger_traza_error.png", 1600, 900)
    with open(SIM / "trafico.csv", encoding="utf-8") as f:
        normal = next(r["trace_id"] for r in csv.DictReader(f) if r["codigo"] == "200" and r["trace_id"])
    capturar(f"{JAEGER}/trace/{normal}", SALIDA / "jaeger_traza_normal.png", 1600, 900)
    us = lambda texto: ms(texto) * 1000  # noqa: E731 - Jaeger usa microsegundos
    busqueda = urllib.parse.urlencode({"end": us(inc["ventana"]["hasta"]), "limit": 20, "lookback": "custom",
                                       "service": "diagnostico-hoja-maiz", "start": us(inc["ventana"]["desde"]),
                                       "tags": json.dumps({"error": "true"})})
    # con lookback=1h (lo que pone la interfaz por defecto) la búsqueda fallaba con 400; con el rango del INC-03 no
    capturar(f"{JAEGER}/search?{busqueda}", SALIDA / "jaeger_busqueda_errores.png", 1600, 1000, 25000)
    desde, hasta = ms(inc["t_inyeccion"]) - 30000, ms(inc["t_resuelto"] or inc["ventana"]["hasta"]) + 30000
    panes = {"a": {"datasource": "loki", "queries": [{"refId": "A", "datasource": {"type": "loki", "uid": "loki"},
                                                      "expr": '{service_name="diagnostico-hoja-maiz"} | evento=~"error_interno|modelo_recargado|falla_inyectada"'}],
                   "range": {"from": str(desde), "to": str(hasta)}}}
    capturar(f"{GRAFANA}/explore?schemaVersion=1&orgId=1&panes={urllib.parse.quote(json.dumps(panes))}",
             SALIDA / "loki_logs_errores.png", 1700, 1200, 25000)


def prometheus():
    capturar(f"{PROM}/alerts", SALIDA / "prometheus_reglas.png", 1500, 1800)
    resumen = json.loads((SIM / "resumen.json").read_text(encoding="utf-8"))
    duracion = int((ms(resumen["fin"]) - ms(resumen["inicio"])) / 60000) + 2
    del duracion
    # La interfaz nueva de Prometheus no dibuja la gráfica en Edge sin ventana; la consulta se hace desde Grafana
    # Explore, que lee el mismo Prometheus
    panes = {"q": {"datasource": "prometheus", "queries": [
        {"refId": "A", "datasource": {"type": "prometheus", "uid": "prometheus"}, "legendFormat": "{{slo}}",
         "expr": 'slo:proporcion_errores:v5m / on(slo) group_left() (1 - slo:objetivo)'}],
        "range": {"from": str(ms(resumen["inicio"])), "to": str(ms(resumen["fin"]))}}}
    capturar(f"{GRAFANA}/explore?schemaVersion=1&orgId=1&panes={urllib.parse.quote(json.dumps(panes))}",
             SALIDA / "prometheus_quema.png", 1700, 1000, 25000)
    alertas_en_el_tiempo(resumen)


SEVERIDAD = {   # paleta de estado (fija): el color repite la severidad que ya va escrita en cada fila
    "P1": ("#d03b3b", "P1 (página inmediata)"), "P2": ("#ec835a", "P2 (página)"),
    "P3": ("#fab219", "P3 (ticket)"), "info": ("#898781", "info (solo registro)")}


def alertas_en_el_tiempo(resumen):
    """Cuándo estuvo disparada cada alerta durante la simulación (serie ALERTS de Prometheus), con los incidentes."""
    import matplotlib.dates as mdates
    import httpx
    from matplotlib.patches import Patch
    fin = datetime.fromisoformat(resumen["fin"].replace("Z", "+00:00")).timestamp() + 120
    r = httpx.get(f"{PROM}/api/v1/query_range", params={
        "query": 'ALERTS{alertstate="firing"}', "start": resumen["inicio"], "end": fin, "step": "5s"}).json()
    tramos = {}
    for serie in r["data"]["result"]:
        clave = (serie["metric"]["alertname"], serie["metric"].get("severidad", "info"))
        tiempos = [float(t) for t, _ in serie["values"]]
        inicio = previo = tiempos[0]
        for t in tiempos[1:] + [None]:
            if t is None or t - previo > 10:
                tramos.setdefault(clave, []).append((inicio, previo + 5))
                inicio = t
            previo = t if t is not None else previo
    orden = sorted(tramos, key=lambda k: (list(SEVERIDAD).index(k[1]), min(a for a, _ in tramos[k])))
    zona = datetime.now().astimezone().tzinfo
    a_fecha = lambda t: datetime.fromtimestamp(t, zona)  # noqa: E731
    fig, eje = plt.subplots(figsize=(11, 0.42 * len(orden) + 1.6))
    fig.patch.set_facecolor("#fcfcfb")
    eje.set_facecolor("#fcfcfb")
    for fila, (alerta, sev) in enumerate(orden):
        for a, b in tramos[(alerta, sev)]:
            eje.barh(fila, (b - a) / 86400, left=mdates.date2num(a_fecha(a)), height=0.5,
                     color=SEVERIDAD[sev][0], edgecolor="#fcfcfb", linewidth=1)
    for i, p in enumerate(sorted(SIM.glob("INC-0?.json"))):
        inc = json.loads(p.read_text(encoding="utf-8"))
        x = mdates.date2num(a_fecha(ms(inc["t_inyeccion"]) / 1000))
        eje.axvline(x, color="#898781", lw=1, ls=(0, (3, 3)), zorder=0)
        # alturas alternadas: el INC-01 y el INC-02 quedaron a un minuto y sus etiquetas se encimaban
        eje.text(x, -0.75 - 0.55 * (i % 2), inc["id"], fontsize=8, color="#52514e", ha="center", va="bottom")
    eje.set_yticks(range(len(orden)), [f"{sev} · {alerta}" for alerta, sev in orden], fontsize=8.5, color="#0b0b0b")
    eje.set_ylim(len(orden) - 0.4, -1.6)
    eje.xaxis.set_major_locator(mdates.MinuteLocator(byminute=range(0, 60, 5), tz=zona))
    eje.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M", tz=zona))
    eje.tick_params(axis="x", labelsize=8, colors="#52514e")
    eje.tick_params(axis="y", length=0)
    eje.grid(axis="x", color="#e1e0d9", lw=0.6)
    eje.set_axisbelow(True)
    for lado in ("top", "right", "left"):
        eje.spines[lado].set_visible(False)
    eje.spines["bottom"].set_color("#c3c2b7")
    eje.set_xlabel("hora local (líneas punteadas: inyección de cada incidente)", fontsize=8, color="#52514e")
    eje.legend(handles=[Patch(color=c, label=t) for c, t in SEVERIDAD.values()], fontsize=8, ncol=4,
               loc="upper center", bbox_to_anchor=(0.5, -0.16), frameon=False)
    fig.suptitle("Alertas disparadas en Prometheus durante la simulación", fontsize=11, color="#0b0b0b", x=0.02,
                 ha="left")
    fig.tight_layout()
    fig.savefig(SALIDA / "prometheus_alertas_en_el_tiempo.png", dpi=170, facecolor=fig.get_facecolor())
    plt.close(fig)


def main():
    SALIDA.mkdir(parents=True, exist_ok=True)
    for paso in (mlflow, mlflow_detalle, grafana, trazas_y_logs, prometheus):
        try:
            paso()
            print(f"listo: {paso.__name__}")
        except Exception as e:                          # noqa: BLE001 - que una captura no detenga las demás
            print(f"falló {paso.__name__}: {e}")
    print("\n".join(sorted(p.name for p in SALIDA.glob("*"))))


if __name__ == "__main__":
    main()
