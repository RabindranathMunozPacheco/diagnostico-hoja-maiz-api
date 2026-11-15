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
| MLflow | Model Registry (versiones y alias champion) y experimentos de monitoreo, reentrenamiento, despliegues y respuesta a incidentes | http://localhost:5000 |

## Arrancar

```bash
python scripts/preparar_datos.py          # fotos del golden set y del tráfico simulado (no se suben al repo)
python monitoreo/slo.py                   # genera las reglas de Prometheus y la configuración de Alertmanager
python monitoreo/tablero.py               # genera el tablero de Grafana
docker compose -f monitoreo/docker-compose.yml up -d --build
python scripts/sincronizar_mlflow.py      # copia las versiones del registro a MLflow (una vez)
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
| `servicios/espejo_mlflow.py` | MLflow: registro de versiones con alias, monitoreo por versión en servicio y bitácora de eventos |
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

## Resultados de la simulación de incidentes

Corrida del 15 de noviembre de 2026 (40 minutos, 4,856 solicitudes de usuarios simuladas, tráfico Poisson a 2 por segundo). Los detalles están en `evidencias/monitoreo/simulacion/` (un JSON y una gráfica por incidente, `linea_de_tiempo.png`, `trafico.csv` y `consola.txt`).

| Incidente | Runbook | Detección (MTTD) | Recuperación (MTTR) | Cómo se resolvió |
|---|---|---|---|---|
| INC-01 Caída del contenedor | RB-01 | 28 s | 47 s | Reinicio automático |
| INC-02 CPU estrangulada | RB-02 | 80 s | 121 s | Reconciliación automática del límite de CPU |
| INC-03 Ráfaga de errores 500 | RB-03 | 64 s | 94 s | Reinicio automático y verificación |
| INC-04 Release malo del modelo (v2) | RB-04 | 44 s | ~11 min | El rollback movió el alias, pero la recarga falló por permisos; escaló y se terminó a mano (`INC-04_intervencion_manual.json`). Corregido en ea5c050 |
| INC-05 Data drift (temporada de lluvias) | RB-05 | 168 s | 246 s | Solicitud de reentrenamiento; la v3 se promovió con aprobación humana |

Reducción de ruido (`ruido.md`): con umbrales fijos sobre cada métrica habrían salido 72 avisos (17 episodios); el diseño basado en SLO mandó 10 avisos a personas (6 páginas y 4 tickets). El bloque `ruido` de `resumen.json` es un conteo preliminar por episodios; el análisis completo es el de `ruido.md`.

El reentrenamiento de la v3 (`evidencias/monitoreo/reentrenamiento/`) es un trade-off: mejora mucho con fotos de lluvia (enfermas reportadas como sanas 31.8 % → 4.5 %) a cambio de perder un poco con fotos normales (F1 0.72 → 0.66), por eso la compuerta de despliegue pide `--aprobado-por`.
