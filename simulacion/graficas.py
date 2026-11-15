"""Gráficas de cada incidente simulado a partir de lo que guardó Prometheus (evidencias/monitoreo/simulacion/).

Por incidente, una figura con dos paneles: la señal que lo detectó (el SLI o la métrica del modelo) y una señal
de apoyo, con líneas verticales en la inyección, la alerta en Alertmanager, las acciones de operaciones y la
resolución. También una figura con la línea de tiempo de toda la simulación.

Uso:
    python simulacion/graficas.py
"""
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402

RAIZ = Path(__file__).resolve().parents[1]
CARPETA = RAIZ / "evidencias" / "monitoreo" / "simulacion"
PROM = "http://localhost:9090"
D = 'ruta="/api/diagnostico",origen="usuario"'
COLORES = {"inyeccion": "#c0392b", "alerta": "#e67e22", "accion": "#2980b9", "resuelto": "#27ae60"}

SENALES = {
    "INC-01": [("API arriba (up)", 'up{job="api"}', None, None),
               ("Solicitudes por segundo (servidor)", f'sum(rate(http_solicitudes_total{{{D}}}[30s]))', None, None)],
    "INC-02": [("Latencia p95 (s)", f'histogram_quantile(0.95, sum by (le) (rate(http_duracion_segundos_bucket{{{D},resultado="ok"}}[30s])))',
                0.5, "límite del SLO"),
               ("Quema del presupuesto de latencia (veces)", 'slo:proporcion_errores:v5m{slo="latencia"} / 0.01', 14.4, "umbral P2 (14.4x)")],
    "INC-03": [("Errores 5xx (proporción)", f'sum(rate(http_solicitudes_total{{{D},codigo=~"5.."}}[30s])) / sum(rate(http_solicitudes_total{{{D}}}[30s]))',
                None, None),
               ("Quema del presupuesto de disponibilidad (veces)", 'slo:proporcion_errores:v5m{slo="disponibilidad"} / 0.005', 14.4,
                "umbral P1 (14.4x)")],
    "INC-04": [("F1 macro de la sonda (golden set)", 'sonda_f1_macro{conjunto="golden"}', 0.60, "mínimo del SLO"),
               ("Enfermas reportadas como sanas (sonda)", 'sonda_enferma_como_sana{conjunto="golden"}', 0.15, "máximo del SLO")],
    "INC-05": [("PSI de las fotos contra la línea base", 'drift_psi{variable=~"brillo|contraste|nitidez"}', 0.25, "umbral de alerta"),
               ("F1 de la sonda", 'sonda_f1_macro', 0.60, "mínimo del SLO")],
}


def ts(texto):
    return datetime.fromisoformat(texto.replace("Z", "+00:00"))


def rango(expr, desde, hasta, paso="5s"):
    r = httpx.get(f"{PROM}/api/v1/query_range", timeout=30,
                  params={"query": expr, "start": desde.timestamp(), "end": hasta.timestamp(), "step": paso}).json()
    series = []
    for s in r["data"]["result"]:
        etiqueta = s["metric"].get("variable") or s["metric"].get("conjunto") or ""
        if s["metric"].get("version"):
            etiqueta += f" v{s['metric']['version']}"
        series.append((etiqueta, [datetime.fromtimestamp(float(t), timezone.utc) for t, _ in s["values"]],
                       [float(v) for _, v in s["values"]]))
    return series


def marcas(inc):
    m = [("inyeccion", ts(inc["t_inyeccion"]), "inyección")]
    if inc.get("t_alerta_alertmanager"):
        m.append(("alerta", ts(inc["t_alerta_alertmanager"]), "alerta"))
    for e in inc["bitacora"]:
        if e["evento"] == "accion":
            m.append(("accion", ts(e["ts"]), "acción automática"))
    for p in inc.get("pasos_manuales", []):
        if "desplegar" in p["paso"]:
            m.append(("accion", ts(p["ts"]), "despliegue aprobado"))
    if inc.get("t_resuelto"):
        m.append(("resuelto", ts(inc["t_resuelto"]), "resuelto"))
    return m


def figura(inc):
    desde, hasta = ts(inc["ventana"]["desde"]), ts(inc["ventana"]["hasta"])
    fig, ejes = plt.subplots(2, 1, figsize=(10, 6.2), sharex=True)
    for eje, (titulo, expr, umbral, nombre_umbral) in zip(ejes, SENALES[inc["id"]]):
        for etiqueta, x, y in rango(expr, desde, hasta):
            eje.plot(x, y, lw=1.8, label=etiqueta or None)
        if umbral is not None:
            eje.axhline(umbral, color="gray", ls="--", lw=1, label=nombre_umbral)
        vistas = set()
        for tipo, t, texto in marcas(inc):
            eje.axvline(t, color=COLORES[tipo], lw=1.2, alpha=0.8, label=texto if texto not in vistas else None)
            vistas.add(texto)
        eje.set_title(titulo, fontsize=10, loc="left")
        eje.grid(alpha=0.25)
        eje.legend(fontsize=7, loc="upper right", ncol=3)
    ejes[-1].xaxis.set_major_formatter(mdates.DateFormatter("%H:%M", tz=datetime.now().astimezone().tzinfo))
    fig.suptitle(f"{inc['id']} · {inc['nombre']}  (MTTD {inc['mttd_s']} s, MTTR {inc['mttr_s']} s)", fontsize=11)
    fig.tight_layout()
    ruta = CARPETA / f"{inc['id']}.png"
    fig.savefig(ruta, dpi=150)
    plt.close(fig)
    return ruta


def linea_de_tiempo(incidentes):
    fig, eje = plt.subplots(figsize=(10, 3.2))
    for i, inc in enumerate(incidentes):
        t0 = ts(inc["t_inyeccion"])
        fin = ts(inc["t_resuelto"]) if inc.get("t_resuelto") else ts(inc["ventana"]["hasta"])
        eje.barh(i, (fin - t0).total_seconds() / 60, height=0.5, color="#bdc3c7")
        detect = inc["mttd_s"] or 0
        eje.barh(i, detect / 60, height=0.5, color=COLORES["alerta"])
        eje.text((fin - t0).total_seconds() / 60 + 0.2, i, f"MTTD {inc['mttd_s']} s · MTTR {inc['mttr_s']} s",
                 va="center", fontsize=8)
    eje.set_yticks(range(len(incidentes)), [f"{inc['id']} {inc['alerta_esperada']}" for inc in incidentes], fontsize=8)
    eje.invert_yaxis()
    eje.set_xlabel("minutos desde la inyección (naranja: detección; gris: hasta la recuperación)")
    eje.grid(axis="x", alpha=0.25)
    fig.tight_layout()
    fig.savefig(CARPETA / "linea_de_tiempo.png", dpi=150)
    plt.close(fig)


def main():
    incidentes = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(CARPETA.glob("INC-*.json"))]
    for inc in incidentes:
        print("escrita", figura(inc).relative_to(RAIZ))
    linea_de_tiempo(incidentes)
    print("escrita", (CARPETA / "linea_de_tiempo.png").relative_to(RAIZ))


if __name__ == "__main__":
    main()
