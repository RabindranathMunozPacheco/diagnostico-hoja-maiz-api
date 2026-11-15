# Runbooks del servicio de diagnóstico de hoja de maíz

Cada alerta de `monitoreo/slo.py` apunta a uno de estos runbooks (etiqueta `runbook` y anotación `runbook_url`) y trae la acción automática que le toca (etiqueta `accion`), que ejecuta el servicio de operaciones (`monitoreo/servicios/operaciones.py`). Todos siguen la misma estructura para que quien esté de guardia no tenga que adivinar:

**Detección → Impacto → Diagnóstico → Acción automática → Acción manual → Validación → Escalamiento → Cierre**

Severidades: **P1** caída o pérdida rápida del presupuesto (página inmediata), **P2** degradación seria (página), **P3** riesgo que puede esperar al horario laboral (ticket), **info** solo registro.

Herramientas: tablero en Grafana (`http://localhost:3000`), Prometheus (`:9090`), Alertmanager (`:9093`), trazas en Jaeger (`:16686`), logs en Loki (desde Grafana, *Explore*), bitácora de operaciones (`:9102/bitacora`).

---

## RB-01

### Servicio caído

| | |
|---|---|
| Alerta | `ServicioCaido` (P1) |
| Detección | `up{job="api"} == 0` durante 3 min (15 s en el laboratorio) |
| Impacto | Nadie puede diagnosticar. Las métricas del servidor dejan de contar, así que el SLO de disponibilidad del lado del servidor **no ve** esta caída; por eso la alerta se basa en `up` y la sonda, no en el SLO |
| Acción automática | `reiniciar`: `docker start` (o `restart` si está colgado) y espera a que el healthcheck diga *healthy*. Máximo 3 reinicios en 10 min |

**Diagnóstico**
1. `docker ps -a --filter name=diagnostico-hoja-maiz`: ¿salió (`Exited`), se reinicia en ciclo o está *unhealthy*?
2. `docker inspect diagnostico-hoja-maiz --format '{{.State.ExitCode}} {{.State.OOMKilled}}'`: código 137 con OOMKilled = falta de memoria (ir a RB-02).
3. `docker logs --tail 50 diagnostico-hoja-maiz`: el último log JSON dice qué pasó al arrancar (p. ej. *no coincide con la huella de la ficha*: el modelo del registro está dañado).

**Acción manual** (si la automática escala): corregir la causa (memoria, archivo del modelo, registro) y `docker compose -f monitoreo/docker-compose.yml up -d api`.

**Validación**: `/api/salud` responde `ok`; `up{job="api"} == 1`; la sonda vuelve a cumplir (`sonda_slo_cumplido{conjunto="golden"} == 1`).

**Escalamiento**: si no queda sano en 120 s o hubo 3 reinicios en 10 min (posible ciclo de caídas).

**Cierre**: anotar en la bitácora la causa y los minutos de caída (los ve el usuario: `simulacion/` mide los errores del lado del cliente).

---

## RB-02

### Latencia alta o saturación

| | |
|---|---|
| Alertas | `LatenciaQuemaRapida` (P2), `LatenciaQuemaMedia` (P3), `SaturacionMemoria` (P3) |
| Detección | Más del 1 % de los diagnósticos tarda más de 0.5 s, quemando el presupuesto 14.4x (1 h y 5 min) o 6x (6 h y 30 min) |
| Impacto | El agricultor espera; con latencias de varios segundos la app parece colgada |
| Acción automática | `reconciliar_recursos` (solo en la P2): compara CPU y memoria del contenedor con el estado deseado (1 CPU, 1 GiB, como en `docker-compose.yml`) y lo restaura con `docker update`, igual que un agente de GitOps. Si ya coincidían, escala |

**Diagnóstico**
1. Tablero, *Señales doradas*: ¿subió el tráfico (más solicitudes por segundo) o solo la latencia?
2. Tablero, *Duración p95 por etapa*: si sube `extraer_caracteristicas`, el cuello de botella es la CPU (MobileNetV2); si sube `abrir_imagen`, llegan fotos muy grandes.
3. *Saturación*: CPU usada cerca del límite = estrangulamiento. `docker inspect diagnostico-hoja-maiz --format '{{.HostConfig.NanoCpus}}'` debe ser `1000000000`.
4. En Jaeger, buscar trazas de más de 500 ms (*Min Duration*) y ver qué span ocupa el tiempo.

**Acción manual**: si los recursos están bien y sube el tráfico, escalar horizontalmente (más réplicas detrás de un balanceador) o subir CPU en el estado deseado (y en `docker-compose.yml`, para que quede versionado).

**Validación**: p95 de nuevo por debajo de 0.5 s en el tablero; la alerta se resuelve sola en Alertmanager.

**Escalamiento**: si los recursos ya coincidían o la latencia no baja 45 s después de corregirlos.

**Cierre**: anotar quién o qué cambió los recursos (en el laboratorio fue una inyección deliberada).

---

## RB-03

### Quema del presupuesto de errores

| | |
|---|---|
| Alertas | `PresupuestoErroresQuemaRapida` (P1), `PresupuestoErroresQuemaMedia` (P2), `PresupuestoErroresQuemaLenta` (P3) |
| Detección | La proporción de respuestas 5xx gasta el presupuesto de 0.5 % (SLO 99.5 %) 14.4x, 6x o 1x más rápido de lo permitido |
| Impacto | El usuario recibe "error interno" en lugar del diagnóstico |
| Acción automática | `atender_errores` (solo en la P1): junta evidencia (etapa con más errores, `trace_id` recientes de Loki). Si hubo una promoción del modelo en los últimos 30 min, hace rollback (RB-04); si no, reinicia (falla transitoria) y verifica a los 30 s |

**Diagnóstico**
1. `sum by (etapa, tipo) (increase(diagnostico_errores_total[5m]))`: en qué etapa falla y con qué excepción.
2. En Grafana, *Explore* con Loki: `{service_name="diagnostico-hoja-maiz"} | evento="error_interno"`. Cada log trae el `trace_id`; el enlace abre la traza en Jaeger, con el span que falló marcado en rojo y su excepción.
3. El usuario también puede dar el `trace_id`: viene en el mensaje de error y en la cabecera `X-Trace-Id`.
4. Revisar `modelo/registro.json` (historial): ¿hubo un despliegue reciente?

**Acción manual**: si el error es de código, regresar la imagen a la versión anterior (`git checkout vX && docker compose up -d --build api`). Si es del modelo, RB-04.

**Validación**: la proporción de 5xx vuelve a 0 en el tablero; el presupuesto restante deja de bajar.

**Escalamiento**: si los errores siguen después del reinicio, o desde la P2 (no hay acción automática).

**Cierre**: postmortem si se gastó más del 20 % del presupuesto.

---

## RB-04

### Calidad del modelo degradada (release malo)

| | |
|---|---|
| Alertas | `CalidadModeloDegradada` (P2), `DistribucionPrediccionesCambio` (P3) |
| Detección | La sonda manda el golden set (40 fotos con clase conocida) y el F1 macro baja de 0.60 o las enfermas reportadas como sanas pasan del 15 %. Sin etiquetas, el cambio en la distribución de P(enferma) (PSI > 0.25) es la primera señal |
| Impacto | El servicio responde rápido y sin errores, pero se equivoca: hojas enfermas reportadas como sanas. Es una falla silenciosa que ningún SLO técnico ve |
| Acción automática | `rollback`: mueve el alias `champion` del registro a la versión anterior (`historial` de `modelo/registro.json`), pide a la API que recargue (`POST /api/admin/recargar`, sin reiniciar) y corre la sonda para confirmarlo. Marca la versión mala como `rechazada`. Máximo un rollback cada 15 min |

**Diagnóstico**
1. Tablero, *Modelo*: ¿la caída de F1 coincide con una anotación de *modelo_recargado*?
2. `modelo/registro.json`: versión anterior, quién y por qué promovió la actual.
3. *Mezcla de diagnósticos* contra la línea base: un release que confunde clases cambia mucho la proporción de cada diagnóstico.
4. Si **no** hubo despliegue y la calidad del golden set está bien pero las predicciones cambian, no es un release: ir a RB-05.

**Acción manual**: no volver a promover la versión rechazada sin corregirla; `scripts/desplegar.py` exige pasar la compuerta de calidad (`--sin-compuerta` solo en emergencias y queda en el historial).

**Validación**: la sonda vuelve a cumplir el SLO con la versión anterior; `modelo_info` muestra la versión correcta.

**Escalamiento**: si no hay versión anterior, si ya hubo un rollback en 15 min (posible ciclo) o si la sonda sigue fallando después del rollback.

**Cierre**: postmortem obligatorio: cómo llegó a producción una versión que no pasa el golden set.

---

## RB-05

### Data drift (las fotos cambiaron)

| | |
|---|---|
| Alertas | `DriftDatosEntrada` (P3), `DriftDatosAdvertencia` (info), `FotosFueraDeDominio` (info) |
| Detección | PSI de brillo, contraste, nitidez o verdor contra la línea base de la versión en servicio mayor que 0.25 (advertencia desde 0.2), con al menos 50 fotos en la ventana de 1 h (5 min en el laboratorio) |
| Impacto | El modelo recibe fotos distintas a las del entrenamiento (temporada de lluvias, otra cámara, otra región). La calidad puede caer aunque no haya errores ni lentitud: en el laboratorio, el golden set en condición de lluvia baja el F1 de 0.73 a 0.25 |
| Acción automática | `solicitar_reentrenamiento`: arma una solicitud con la evidencia (PSI por variable, fotos en la ventana, F1 de la sonda normal y adversa). **No hace rollback**: la versión anterior tampoco fue entrenada con esas condiciones, regresar no arregla nada |

**Diagnóstico**
1. Tablero, *Datos de entrada*: qué variable cambió. Brillo y contraste bajos = fotos oscuras; nitidez baja = borrosas; verdor bajo = fotos que no son hojas.
2. Descartar un error del pipeline: ¿cambió la versión de la app móvil, la compresión o el tamaño de las fotos (`imagen.ancho`, `imagen.formato` en los spans de Jaeger)? Si es eso, se corrige la app, no el modelo.
3. Estimar el impacto: F1 de la sonda con el conjunto **adverso** contra el golden set normal.
4. Revisar las advertencias que ya recibe el usuario (`advertencias` en la respuesta: "la foto está más oscura…").

**Acción manual**: aprobar la solicitud y correr `python scripts/reentrenar.py` (entrena con aumento de datos de las condiciones nuevas, registra la versión como candidata con su nueva línea base), luego `python scripts/desplegar.py --version N`, que pasa la compuerta de calidad en fotos normales **y** adversas. Con la nueva línea base, las fotos de lluvia dejan de contar como drift.

**Validación**: la sonda adversa sube; el PSI contra la nueva línea base baja de 0.25.

**Escalamiento**: P3, se atiende en horario laboral; sube a P2 si además la sonda normal falla (RB-04).

**Cierre**: documentar la temporada o el cambio que causó el drift y si hace falta recolectar y etiquetar fotos reales de esas condiciones.

---

## RB-06

### El monitoreo dejó de reportar

| | |
|---|---|
| Alerta | `MonitoreoSinDatos` (P3) |
| Detección | El vigilante u operaciones no responden a Prometheus, o la sonda no corre desde hace más de 4 periodos |
| Impacto | Sin sonda no se detecta un release malo; sin operaciones no hay respuesta automática. El servicio puede estar bien, pero se está "volando a ciegas" |
| Acción automática | Ninguna (lo que falla es la automatización misma) |

**Diagnóstico**: `docker compose -f monitoreo/docker-compose.yml ps` y `docker compose -f monitoreo/docker-compose.yml logs --tail 50 vigilante operaciones`.

**Acción manual**: `docker compose -f monitoreo/docker-compose.yml up -d vigilante operaciones`.

**Validación**: `up{job=~"vigilante|operaciones"} == 1` y `sonda_ultima_ejecucion_timestamp_segundos` reciente.

**Escalamiento**: si el monitoreo no vuelve, avisar al equipo de que las alertas del modelo no están activas.

**Cierre**: anotar el tiempo sin monitoreo.
