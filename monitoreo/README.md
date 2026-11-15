# Monitoreo, alertas y respuesta automatizada

Laboratorio de observabilidad de la API de diagnóstico (Actividad 8). Levanta la API junto con:

| Servicio | Para qué | URL |
|---|---|---|
| Prometheus | Guarda las métricas, calcula los SLI y evalúa las alertas | http://localhost:9090 |
| Alertmanager | Agrupa, deduplica e inhibe alertas y avisa por severidad | http://localhost:9093 |
| Grafana | Tablero operativo (métricas, logs y trazas en un solo lugar) | http://localhost:3000 |
| Loki | Guarda los logs JSON de la API (llegan por OTLP) | (desde Grafana) |
| Jaeger | Guarda las trazas (un span por etapa del diagnóstico) | http://localhost:16686 |
| vigilante | Monitor de data drift (PSI y KS) y sonda de calidad con el golden set | http://localhost:9101/metrics (dentro de la red) |
| operaciones | Recibe las alertas y ejecuta la parte automática de los runbooks | http://localhost:9102/bitacora |

## Arrancar

```bash
python scripts/preparar_datos.py          # fotos del golden set y del tráfico simulado (no se suben al repo)
python monitoreo/slo.py                   # genera las reglas de Prometheus y la configuración de Alertmanager
python monitoreo/tablero.py               # genera el tablero de Grafana
docker compose -f monitoreo/docker-compose.yml up -d --build
python simulacion/trafico.py --tasa 2 --minutos 5     # tráfico de usuarios simulado
```

## Archivos

| Archivo | Qué es |
|---|---|
| `slo.py` | **Fuente única de verdad**: SLO, presupuestos de error, alertas (severidad, runbook y acción) e inhibiciones. Genera `prometheus/` y `alertmanager/` |
| `tablero.py` | Genera `grafana/tableros/operativo.json` |
| `runbooks.md` | Procedimientos de respuesta (RB-01 a RB-06) |
| `servicios/vigilante.py` | Drift (PSI con bins agrupados al 5 %, KS) y sonda de calidad (F1 y enfermas como sanas) |
| `servicios/operaciones.py` | Respuesta automatizada: reiniciar, reconciliar recursos, rollback del modelo, solicitud de reentrenamiento |
| `golden_set.json` | Las 40 fotos de la sonda (10 por clase) |
| `docker-compose.yml` | El laboratorio completo, con el estado deseado de la API (1 CPU, 1 GiB) |

## SLO

| SLO | SLI | Objetivo | Presupuesto (30 días) |
|---|---|---|---|
| Disponibilidad | Diagnósticos de usuarios sin error 5xx | 99.5 % | 0.5 % de las solicitudes |
| Latencia | Diagnósticos exitosos en 0.5 s o menos | 99 % | 1 % de las solicitudes |
| Calidad (sonda) | F1 macro del golden set y enfermas reportadas como sanas | F1 ≥ 0.60 y ≤ 15 % | se evalúa en cada ronda |
| Datos | PSI de brillo, contraste, nitidez y verdor contra la línea base | ≤ 0.25 | ventana de 1 h |

En el laboratorio las ventanas de tiempo se comprimen 12 veces (1 h → 5 min) para que un incidente se vea en minutos.
