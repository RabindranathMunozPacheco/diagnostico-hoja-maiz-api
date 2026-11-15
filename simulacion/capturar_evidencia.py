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
    capturar(f"{JAEGER}/search?service=diagnostico-hoja-maiz&tags=%7B%22error%22%3A%22true%22%7D&limit=20",
             SALIDA / "jaeger_busqueda_errores.png", 1600, 1000)
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
    q = urllib.parse.quote('slo:proporcion_errores:v5m / on(slo) group_left() (1 - slo:objetivo)')
    capturar(f"{PROM}/query?g0.expr={q}&g0.tab=graph&g0.range_input={duracion}m&g0.end_input="
             f"{urllib.parse.quote(resumen['fin'][:19].replace('T', ' '))}", SALIDA / "prometheus_quema.png", 1500, 900)


def main():
    SALIDA.mkdir(parents=True, exist_ok=True)
    for paso in (mlflow, grafana, trazas_y_logs, prometheus):
        try:
            paso()
            print(f"listo: {paso.__name__}")
        except Exception as e:                          # noqa: BLE001 - que una captura no detenga las demás
            print(f"falló {paso.__name__}: {e}")
    print("\n".join(sorted(p.name for p in SALIDA.glob("*"))))


if __name__ == "__main__":
    main()
