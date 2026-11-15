"""Genera el tablero operativo de Grafana (grafana/tableros/operativo.json).

El tablero se lee de arriba hacia abajo, de lo más urgente a lo más detallado:
    1. Estado general     ¿está arriba?, ¿qué versión sirve?, ¿cuánto presupuesto de errores queda?, alertas activas
    2. Señales doradas    tráfico, errores, latencia (con la línea del SLO) y saturación; duración por etapa
    3. Presupuesto        tasa de quema por ventana contra los umbrales de las alertas (14.4x, 6x, 1x)
    4. Modelo             calidad medida por la sonda (golden set y conjunto adverso) y mezcla de diagnósticos
    5. Datos              PSI por característica (0.1 advertencia, 0.25 alerta), KS y fotos fuera de rango
    6. Operación          acciones automáticas, notificaciones por canal, escalamientos y los logs del servicio
                          (cada log con trace_id enlaza a su traza en Jaeger)
Las anotaciones marcan en todas las gráficas cuándo se recargó el modelo y cuándo se inyectó o quitó una falla.

Uso:
    python monitoreo/tablero.py
"""
import json
from pathlib import Path

AQUI = Path(__file__).resolve().parent
PROM = {"type": "prometheus", "uid": "prometheus"}
LOKI = {"type": "loki", "uid": "loki"}
D = 'ruta="/api/diagnostico",origen="usuario"'
_id = iter(range(1, 1000))


def q(expr, leyenda="", instant=False, ref=None, formato="time_series"):
    return {"datasource": PROM, "expr": expr, "legendFormat": leyenda, "instant": instant, "range": not instant,
            "refId": ref or "ABCDEFGHIJ"[0], "format": formato}


def umbrales(*pasos):
    return {"mode": "absolute", "steps": [{"color": c, "value": v} for c, v in pasos]}


def panel(tipo, titulo, x, y, w, h, consultas, descripcion="", unidad=None, umbral=None, opciones=None,
          personal=None, extra=None, datasource=PROM):
    for i, c in enumerate(consultas):
        c["refId"] = "ABCDEFGHIJ"[i]
    defaults = {"unit": unidad} if unidad else {}
    if umbral:
        defaults["thresholds"] = umbral
    if personal:
        defaults["custom"] = personal
    p = {"id": next(_id), "type": tipo, "title": titulo, "description": descripcion, "datasource": datasource,
         "gridPos": {"x": x, "y": y, "w": w, "h": h}, "targets": consultas,
         "fieldConfig": {"defaults": defaults, "overrides": []}, "options": opciones or {}}
    p.update(extra or {})
    return p


def fila(titulo, y):
    return {"id": next(_id), "type": "row", "title": titulo, "collapsed": False, "gridPos": {"x": 0, "y": y, "w": 24, "h": 1},
            "panels": []}


def serie(titulo, x, y, w, h, consultas, descripcion="", unidad=None, umbral=None, apilado=False, minimo=None,
          maximo=None):
    personal = {"lineWidth": 2, "fillOpacity": 25 if apilado else 8, "showPoints": "never",
                "thresholdsStyle": {"mode": "dashed" if umbral else "off"},
                "stacking": {"mode": "normal" if apilado else "none"}}
    p = panel("timeseries", titulo, x, y, w, h, consultas, descripcion, unidad, umbral,
              {"legend": {"displayMode": "list", "placement": "bottom"}, "tooltip": {"mode": "multi"}}, personal)
    if minimo is not None:
        p["fieldConfig"]["defaults"]["min"] = minimo
    if maximo is not None:
        p["fieldConfig"]["defaults"]["max"] = maximo
    return p


def dato(titulo, x, y, w, h, consultas, descripcion="", unidad=None, umbral=None, mapeos=None, texto="auto"):
    p = panel("stat", titulo, x, y, w, h, consultas, descripcion, unidad, umbral,
              {"colorMode": "background", "graphMode": "none", "textMode": texto, "justifyMode": "center",
               "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}})
    if mapeos:
        p["fieldConfig"]["defaults"]["mappings"] = mapeos
    p["fieldConfig"]["defaults"]["noValue"] = "sin tráfico"
    return p


def construir():
    paneles, y = [], 0
    rojo_verde = umbrales(("red", None), ("green", 1))

    paneles.append(fila("1. Estado general", y)); y += 1
    paneles += [
        dato("Servicio", 0, y, 4, 4, [q('up{job="api"}', instant=True)], "La API responde a Prometheus.",
             umbral=rojo_verde, mapeos=[{"type": "value", "options": {"0": {"text": "CAÍDO", "color": "red"},
                                                                       "1": {"text": "EN LÍNEA", "color": "green"}}}]),
        dato("Modelo en servicio", 4, y, 4, 4, [q("max by (version) (modelo_info)", "v{{version}}", instant=True)],
             "Versión del alias champion del registro.", texto="name", umbral=umbrales(("blue", None))),
        # Con tráfico y sin la proporción grabada (datos anteriores a la corrección en slo.py) se muestra 100 %;
        # "sin tráfico" solo cuando de verdad no hubo solicitudes en la ventana
        dato("Disponibilidad (1 h)", 8, y, 4, 4, [q('1 - (slo:proporcion_errores:v1h{slo="disponibilidad"} or on() '
                                                    '(0 * (slo:solicitudes:v1h{slo="disponibilidad"} > 0)))', instant=True)],
             "SLO: 99.5 % sin errores 5xx.", "percentunit", umbrales(("red", None), ("orange", 0.99), ("green", 0.995))),
        dato("Diagnósticos en ≤ 0.5 s (1 h)", 12, y, 5, 4, [q('1 - slo:proporcion_errores:v1h{slo="latencia"}', instant=True)],
             "SLO: 99 % en 0.5 s o menos.", "percentunit", umbrales(("red", None), ("orange", 0.95), ("green", 0.99))),
        dato("Presupuesto de errores restante", 17, y, 7, 4,
             [q('slo:presupuesto_restante', "{{slo}}", instant=True)],
             "Cuánto del presupuesto de la ventana del SLO (30 días; 2.5 días en el laboratorio) queda sin gastar.",
             "percentunit", umbrales(("red", None), ("orange", 0.25), ("green", 0.5))),
    ]
    y += 4
    # Fila propia y a todo lo ancho: en un panel de 8 columnas por 4 de alto solo se leía una alerta y media
    tabla_alertas = panel("table", "Alertas activas", 0, y, 24, 6,
                          [q('ALERTS{alertstate="firing"}', instant=True, formato="table")],
                          "Alertas que Prometheus tiene disparadas (Alertmanager decide a quién avisar y cuáles se "
                          "inhiben).", opciones={"showHeader": True, "sortBy": [{"displayName": "severidad", "desc": False}]},
                          extra={"transformations": [{"id": "organize", "options": {
                              "includeByName": {"alertname": True, "severidad": True, "runbook": True, "accion": True},
                              "indexByName": {"severidad": 0, "alertname": 1, "runbook": 2, "accion": 3},
                              "renameByName": {"alertname": "alerta", "accion": "acción automática"}}}]})
    tabla_alertas["fieldConfig"]["overrides"] = [
        {"matcher": {"id": "byName", "options": n}, "properties": [{"id": "custom.width", "value": w}]}
        for n, w in (("severidad", 110), ("runbook", 110), ("acción automática", 260))]
    tabla_alertas["fieldConfig"]["defaults"]["noValue"] = "sin alertas activas"
    paneles.append(tabla_alertas)
    y += 6

    paneles.append(fila("2. Señales doradas", y)); y += 1
    paneles += [
        serie("Tráfico por código (solicitudes/s)", 0, y, 6, 7,
              [q(f'sum by (codigo) (rate(http_solicitudes_total{{{D}}}[1m]))', "{{codigo}}"),
               q('sum(rate(http_solicitudes_total{ruta="/api/diagnostico",origen="sonda"}[1m]))', "sonda")],
              "Diagnósticos de usuarios por código HTTP; la sonda aparte.", "reqps", apilado=True),
        serie("Errores 5xx (%)", 6, y, 6, 7,
              [q(f'sum(rate(http_solicitudes_total{{{D},codigo=~"5.."}}[1m])) / sum(rate(http_solicitudes_total{{{D}}}[1m]))',
                 "errores 5xx"),
               q(f'sum(rate(diagnostico_rechazos_total{{origen="usuario"}}[1m])) / sum(rate(http_solicitudes_total{{{D}}}[1m]))',
                 "rechazos 4xx (no gastan presupuesto)")],
              "Solo los 5xx cuentan contra el SLO.", "percentunit", umbrales(("green", None), ("red", 0.005)), minimo=0),
        serie("Latencia (s)", 12, y, 6, 7,
              [q(f'histogram_quantile({p}, sum by (le) (rate(http_duracion_segundos_bucket{{{D},resultado="ok"}}[1m])))',
                 f"p{int(p * 100)}") for p in (0.5, 0.95, 0.99)],
              "Línea punteada: límite del SLO (0.5 s).", "s", umbrales(("green", None), ("red", 0.5)), minimo=0),
        serie("Saturación", 18, y, 6, 7,
              [q('rate(process_cpu_seconds_total{job="api"}[1m])', "CPU usada (núcleos)"),
               q('process_resident_memory_bytes{job="api"} / 1073741824', "memoria (fracción de 1 GiB)"),
               q('http_solicitudes_en_curso', "solicitudes en curso")],
              "CPU en núcleos (límite 1), memoria como fracción del límite.", None, minimo=0),
    ]
    y += 7
    paneles.append(serie("Duración p95 por etapa del diagnóstico (s)", 0, y, 24, 5,
                         [q('histogram_quantile(0.95, sum by (le, etapa) (rate(diagnostico_etapa_segundos_bucket[1m])))',
                            "{{etapa}}")],
                         "Dónde se va el tiempo: abrir la imagen, medir su calidad, extraer características o clasificar.",
                         "s", minimo=0))
    y += 5

    paneles.append(fila("3. Presupuesto de errores", y)); y += 1
    quemas = [q(f'slo:proporcion_errores:v{v}{{slo="{s}"}} / {1 - o}', f"{s} {v}")
              for s, o in (("disponibilidad", 0.995), ("latencia", 0.99)) for v in ("5m", "1h", "6h")]
    paneles += [
        serie("Tasa de quema del presupuesto (veces lo permitido)", 0, y, 16, 7, quemas,
              "1 = se gasta justo al ritmo que permite el SLO. Las alertas: 14.4x (P1/P2), 6x y 1x, cada una en "
              "dos ventanas (las etiquetas muestran la ventana de producción; el laboratorio las divide entre 12).",
              None, umbrales(("green", None), ("yellow", 1), ("orange", 6), ("red", 14.4)), minimo=0),
        serie("Presupuesto restante", 16, y, 8, 7, [q("slo:presupuesto_restante", "{{slo}}")],
              "Fracción del presupuesto de la ventana del SLO que queda.", "percentunit",
              umbrales(("red", None), ("orange", 0.25), ("green", 0.5)), maximo=1),
    ]
    y += 7

    paneles.append(fila("4. Modelo", y)); y += 1
    paneles += [
        serie("Calidad medida por la sonda: F1 macro", 0, y, 8, 7,
              [q('sonda_f1_macro', "{{conjunto}} (v{{version}})")],
              "Golden set: 40 fotos con clase conocida. Adverso: las mismas en condición de lluvia (no alerta). "
              "Línea: mínimo del SLO.", None, umbrales(("red", None), ("green", 0.60)), minimo=0, maximo=1),
        serie("Enfermas reportadas como sanas", 8, y, 8, 7,
              [q('sonda_enferma_como_sana', "{{conjunto}} (v{{version}})")],
              "El error más caro para el agricultor. Línea: máximo del SLO (15 %).", "percentunit",
              umbrales(("green", None), ("red", 0.15)), minimo=0),
        serie("Mezcla de diagnósticos (usuarios, ventana de drift)", 16, y, 8, 7,
              [q("drift_proporcion_diagnostico", "{{diagnostico}}"),
               q("drift_proporcion_diagnostico_base", "{{diagnostico}} (línea base)")],
              "Sin etiquetas en producción, la mezcla de respuestas es la primera señal de un cambio.",
              "percentunit", minimo=0),
    ]
    y += 7

    paneles.append(fila("5. Datos de entrada (data drift)", y)); y += 1
    paneles += [
        serie("PSI contra la línea base", 0, y, 10, 7, [q("drift_psi", "{{variable}}")],
              "Menos de 0.1 estable; 0.1 a 0.25 advertencia; más de 0.25 alerta (DriftDatosEntrada).", None,
              umbrales(("green", None), ("orange", 0.1), ("red", 0.25)), minimo=0),
        serie("KS contra la línea base", 10, y, 7, 7, [q("drift_ks", "{{variable}}")],
              "Mayor diferencia entre las distribuciones acumuladas.", None, minimo=0, maximo=1),
        serie("Fotos fuera del rango del entrenamiento (por minuto)", 17, y, 7, 7,
              [q('sum by (variable) (rate(entrada_fuera_de_rango_total{origen="usuario"}[1m])) * 60', "{{variable}}"),
               q("drift_muestras", "fotos en la ventana del drift")],
              "Fotos con brillo, contraste, nitidez o verdor fuera del 99 % central del entrenamiento.", None, minimo=0),
    ]
    y += 7

    paneles.append(fila("6. Operación y respuesta", y)); y += 1
    paneles += [
        serie("Acciones automáticas", 0, y, 8, 7,
              [q('sum by (accion, resultado) (increase(operaciones_acciones_total[5m]))', "{{accion}} ({{resultado}})"),
               q('sum(increase(operaciones_escalamientos_total[5m]))', "escaladas a una persona")],
              "Reinicios, reconciliaciones de recursos, rollbacks y solicitudes de reentrenamiento.", None, minimo=0),
        serie("Notificaciones por canal", 8, y, 8, 7,
              [q('sum by (canal) (increase(operaciones_notificaciones_total[5m]))', "{{canal}}")],
              "Página (P1/P2), ticket (P3) y solo registro (info), después de agrupar e inhibir.", None, minimo=0),
        panel("logs", "Logs del servicio (errores, rechazos y cambios)", 16, y, 8, 7,
              [{"datasource": LOKI, "expr": '{service_name="diagnostico-hoja-maiz"} | evento=~"error_interno|rechazo|'
                                            'modelo_recargado|recarga_fallida|falla_inyectada|falla_retirada|modelo_cargado"'}],
              "Cada línea trae su trace_id: al abrirla aparece el enlace a la traza en Jaeger.",
              opciones={"showTime": True, "wrapLogMessage": True, "sortOrder": "Descending", "enableLogDetails": True},
              datasource=LOKI),
    ]
    y += 7
    paneles.append(panel("text", "Enlaces", 0, y, 24, 4, [], opciones={"mode": "markdown", "content": (
        "[Trazas en Jaeger](http://localhost:16686/search?service=diagnostico-hoja-maiz) · "
        "[Alertmanager](http://localhost:9093) · [Reglas en Prometheus](http://localhost:9090/alerts) · "
        "[Bitácora de operaciones](http://localhost:9102/bitacora) · "
        "[MLflow: versiones y monitoreo del modelo](http://localhost:5000) · "
        "[Runbooks](https://github.com/RabindranathMunozPacheco/diagnostico-hoja-maiz-api/blob/main/monitoreo/runbooks.md)")}))

    anotaciones = [
        {"name": "Cambios de modelo y fallas", "datasource": LOKI, "enable": True, "iconColor": "purple",
         "expr": '{service_name="diagnostico-hoja-maiz"} | evento=~"modelo_recargado|falla_inyectada|falla_retirada"',
         "textFormat": "{{evento}}", "titleFormat": "{{evento}}", "tagKeys": "evento"},
        {"name": "Acciones de operaciones", "datasource": PROM, "enable": True, "iconColor": "orange",
         "expr": 'changes(operaciones_acciones_total[15s]) > 0', "titleFormat": "{{accion}} ({{resultado}})",
         "step": "5s"},
    ]
    return {"uid": "operativo-maiz", "title": "Diagnóstico de hoja de maíz: tablero operativo", "editable": False,
            "tags": ["mlops", "slo"], "timezone": "browser", "refresh": "5s", "schemaVersion": 39, "version": 1,
            "time": {"from": "now-30m", "to": "now"}, "panels": paneles,
            "annotations": {"list": anotaciones}}


def main():
    ruta = AQUI / "grafana" / "tableros" / "operativo.json"
    ruta.parent.mkdir(parents=True, exist_ok=True)
    tablero = construir()
    ruta.write_text(json.dumps(tablero, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"escrito {ruta.relative_to(AQUI.parent)} con {sum(p['type'] != 'row' for p in tablero['panels'])} paneles")


if __name__ == "__main__":
    main()
