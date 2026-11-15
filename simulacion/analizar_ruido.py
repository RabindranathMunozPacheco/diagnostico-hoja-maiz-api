"""Reducción de ruido: avisos de un monitoreo ingenuo contra los avisos reales del diseño basado en SLO.

Monitoreo ingenuo: un umbral fijo sobre cada métrica suelta, revisado cada 15 s, que avisa cuando la condición se
cumple y vuelve a avisar cada minuto mientras siga (como un script de cron sin agrupación ni deduplicación). Se
evalúa con PromQL sobre los mismos datos de Prometheus de toda la simulación.

Diseño real: lo que operaciones recibió de Alertmanager según la bitácora (avisos nuevos por canal y
escalamientos), más las alertas que Alertmanager inhibió.

Uso:
    python simulacion/analizar_ruido.py     # deja evidencias/monitoreo/simulacion/ruido.json y ruido.md
"""
import json
from collections import Counter
from datetime import datetime
from pathlib import Path

import httpx

RAIZ = Path(__file__).resolve().parents[1]
SIM = RAIZ / "evidencias" / "monitoreo" / "simulacion"
PROM = "http://localhost:9090"
PASO = 15
D = 'ruta="/api/diagnostico",origen="usuario"'
INGENUAS = {
    "Algún error 5xx en el último minuto": f'sum(increase(http_solicitudes_total{{{D},codigo=~"5.."}}[1m])) > 0',
    "Latencia p95 > 0.5 s en el último minuto": f'histogram_quantile(0.95, sum by (le) (rate(http_duracion_segundos_bucket{{{D},resultado="ok"}}[1m]))) > 0.5',
    "Algún diagnóstico tardó más de 1 s": f'(sum(increase(http_duracion_segundos_count{{{D}}}[1m])) - sum(increase(http_duracion_segundos_bucket{{{D},le="1.0"}}[1m]))) > 0.5',
    "CPU > 80 % del límite": 'rate(process_cpu_seconds_total{job="api"}[1m]) > 0.8',
    "Memoria > 60 % del límite": 'process_resident_memory_bytes{job="api"} / 1073741824 > 0.6',
    "La API no responde": 'up{job="api"} == bool 0',
    "PSI de alguna variable de entrada > 0.1": 'max(drift_psi{variable=~"brillo|contraste|nitidez|verdor"}) > 0.1',
    "PSI de P(enferma) > 0.1": 'drift_psi{variable="p_enferma"} > 0.1',
    "La sonda no cumple": 'sonda_slo_cumplido{conjunto="golden"} == bool 0',
}


def ts(texto):
    return datetime.fromisoformat(texto.replace("Z", "+00:00")).timestamp()


def evaluar(expr, desde, hasta):
    r = httpx.get(f"{PROM}/api/v1/query_range", timeout=60,
                  params={"query": expr if " bool " in expr else f"({expr}) > bool 0", "start": desde, "end": hasta,
                          "step": f"{PASO}s"}).json()
    activos = {}
    for serie in r["data"]["result"]:
        for t, v in serie["values"]:
            activos[float(t)] = activos.get(float(t), 0.0) or float(v)
    pasos = [activos.get(desde + i * PASO, 0.0) for i in range(int((hasta - desde) / PASO) + 1)]
    episodios, avisos, racha = 0, 0, 0
    for v in pasos:
        if v:
            if racha == 0:
                episodios += 1
                avisos += 1
            racha += 1
            if racha > 1 and (racha - 1) * PASO % 60 == 0:      # recordatorio cada minuto mientras siga activa
                avisos += 1
        else:
            racha = 0
    return {"episodios": episodios, "avisos": avisos, "minutos_activa": round(sum(1 for v in pasos if v) * PASO / 60, 1)}


def main():
    resumen = json.loads((SIM / "resumen.json").read_text(encoding="utf-8"))
    desde, hasta = ts(resumen["inicio"]), ts(resumen["fin"])
    ingenuo = {nombre: evaluar(expr, desde, hasta) for nombre, expr in INGENUAS.items()}
    bitacora = [json.loads(l) for l in (RAIZ / "monitoreo" / "bitacora" / "bitacora.jsonl").read_text(encoding="utf-8").splitlines()]
    bitacora = [e for e in bitacora if desde <= ts(e["ts"]) <= hasta + 300]
    canales = Counter(e["canal"] for e in bitacora if e["evento"] == "alerta_recibida")
    escalamientos = sum(1 for e in bitacora if e["evento"] == "escalamiento")
    inhibidas = sorted({a["alerta"] for p in SIM.glob("INC-0?.json")
                        for a in json.loads(p.read_text(encoding="utf-8")).get("alertas_vistas", [])
                        if any("inhibida" in s for s in a["estados"])})
    reales = {"pagina": canales.get("página al responsable de guardia", 0) + escalamientos,
              "ticket": canales.get("ticket en la cola de MLOps", 0), "registro": canales.get("solo registro", 0),
              "escalamientos": escalamientos, "inhibidas": inhibidas,
              "alertas_distintas": sorted({e["alerta"] for e in bitacora if e["evento"] == "alerta_recibida"})}
    total_ingenuo = sum(v["avisos"] for v in ingenuo.values())
    salida = {"ventana": [resumen["inicio"], resumen["fin"]], "paso_s": PASO, "ingenuo": ingenuo,
              "total_ingenuo_avisos": total_ingenuo,
              "total_ingenuo_episodios": sum(v["episodios"] for v in ingenuo.values()), "real": reales,
              "total_real_a_personas": reales["pagina"] + reales["ticket"]}
    (SIM / "ruido.json").write_text(json.dumps(salida, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    lineas = ["# Reducción de ruido", "", "| Regla ingenua | Episodios | Avisos (con recordatorio cada minuto) | Minutos activa |",
              "|---|---|---|---|"] + [f"| {k} | {v['episodios']} | {v['avisos']} | {v['minutos_activa']} |" for k, v in ingenuo.items()]
    lineas += [f"| **Total** | {salida['total_ingenuo_episodios']} | {total_ingenuo} | |", "",
               f"Diseño basado en SLO: {reales['pagina']} páginas (incluye {escalamientos} escalamiento), "
               f"{reales['ticket']} tickets y {reales['registro']} de solo registro; alertas inhibidas en algún momento: "
               f"{', '.join(inhibidas)}."]
    (SIM / "ruido.md").write_text("\n".join(lineas) + "\n", encoding="utf-8", newline="\n")
    print("\n".join(lineas))


if __name__ == "__main__":
    main()
