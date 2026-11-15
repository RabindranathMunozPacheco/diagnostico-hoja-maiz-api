"""SLO, presupuestos de error y alertas del servicio de diagnóstico: la única fuente de verdad del monitoreo.

Aquí se definen los objetivos de servicio (SLO), las alertas, su severidad, su runbook y la acción automática que
les toca. El script genera a partir de esto la configuración que leen las herramientas:
    prometheus/prometheus.yml    qué se lee y cada cuándo
    prometheus/reglas/slo.yml    reglas de registro (SLI por ventana) y reglas de alerta
    alertmanager/alertmanager.yml  rutas por severidad, agrupación e inhibiciones (reducción de ruido)
Así nadie edita los YAML a mano y cualquier cambio a un SLO queda revisado en el código.

Alertas por quema del presupuesto de errores (multiventana, como recomienda el SRE Workbook de Google):
    P1  14.4x en 1 h y en 5 min    se gasta el 2 % del presupuesto de 30 días en una hora   -> página
    P2   6x   en 6 h y en 30 min   el 5 % en seis horas                                    -> página
    P3   1x   en 3 días y en 6 h   el 10 % en tres días                                    -> ticket
La ventana larga evita alertar por un pico aislado; la corta hace que la alerta se apague pronto cuando el
problema se corrige.

Escala de tiempo: en producción las ventanas son las de arriba. En el laboratorio todo se comprime 12 veces
(1 h -> 5 min, 5 min -> 25 s, 30 días -> 2.5 días) para que un incidente se vea en minutos y no en horas; los
umbrales y la lógica son los mismos.

Uso:
    python monitoreo/slo.py                       # laboratorio (lo que usa el docker-compose de monitoreo)
    python monitoreo/slo.py --escala produccion   # cómo quedaría en producción (solo para revisar)
"""
import argparse
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

AQUI = Path(__file__).resolve().parent
REPO = "https://github.com/RabindranathMunozPacheco/diagnostico-hoja-maiz-api/blob/main/monitoreo/runbooks.md"
SERVICIO = "diagnostico"
DIAGNOSTICOS = 'ruta="/api/diagnostico",origen="usuario"'

ESCALAS = {
    "laboratorio": {"factor": 1 / 12, "lectura": "5s", "evaluacion": "5s", "espera_grupo": "5s",
                    "intervalo_grupo": "30s", "repeticion": "30m"},
    "produccion": {"factor": 1.0, "lectura": "15s", "evaluacion": "30s", "espera_grupo": "30s",
                   "intervalo_grupo": "5m", "repeticion": "4h"},
}


@dataclass
class SLO:
    nombre: str
    titulo: str
    objetivo: float           # proporción de eventos buenos en la ventana del SLO
    ventana_dias: int
    sli: str                  # descripción del indicador
    malos: str                # expresión con {v} donde va la ventana
    totales: str
    severidades: dict         # quema -> (severidad, acción automática, runbook)

    @property
    def presupuesto(self):
        return round(1 - self.objetivo, 6)


SLOS = [
    SLO(nombre="disponibilidad", titulo="Disponibilidad del diagnóstico", objetivo=0.995, ventana_dias=30,
        sli="Proporción de solicitudes de diagnóstico de usuarios que no terminan en error del servidor (5xx). "
            "Las fotos inválidas (4xx) son responsabilidad del cliente y no gastan presupuesto.",
        malos=f'sum(rate(http_solicitudes_total{{{DIAGNOSTICOS},codigo=~"5.."}}[{{v}}]))',
        totales=f'sum(rate(http_solicitudes_total{{{DIAGNOSTICOS}}}[{{v}}]))',
        severidades={"rapida": ("P1", "atender_errores", "RB-03"), "media": ("P2", "ninguna", "RB-03"),
                     "lenta": ("P3", "ninguna", "RB-03")}),
    SLO(nombre="latencia", titulo="Latencia del diagnóstico", objetivo=0.99, ventana_dias=30,
        sli="Proporción de diagnósticos exitosos que responden en 0.5 s o menos.",
        malos=f'(sum(rate(http_duracion_segundos_count{{{DIAGNOSTICOS},resultado="ok"}}[{{v}}])) - '
              f'sum(rate(http_duracion_segundos_bucket{{{DIAGNOSTICOS},resultado="ok",le="0.5"}}[{{v}}])))',
        totales=f'sum(rate(http_duracion_segundos_count{{{DIAGNOSTICOS},resultado="ok"}}[{{v}}]))',
        severidades={"rapida": ("P2", "reconciliar_recursos", "RB-02"), "media": ("P3", "ninguna", "RB-02")}),
]

# quema -> (factor, ventana larga, ventana corta, espera "for" en producción, cuánto presupuesto gasta)
QUEMAS = {
    "rapida": (14.4, "1h", "5m", "2m", "el 2 % del presupuesto de 30 días en 1 hora"),
    "media": (6.0, "6h", "30m", "15m", "el 5 % del presupuesto en 6 horas"),
    "lenta": (1.0, "3d", "6h", "1h", "el 10 % del presupuesto en 3 días"),
}
MIN_SOLICITUDES = 20          # con menos solicitudes en la ventana larga, una sola falla dispararía la alerta


def segundos(duracion):
    unidades = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    return sum(int(n) * unidades[u] for n, u in re.findall(r"(\d+)([smhd])", duracion))


def texto(seg):
    seg = max(int(round(seg)), 5)
    for unidad, valor in (("d", 86400), ("h", 3600), ("m", 60)):
        if seg % valor == 0:
            return f"{seg // valor}{unidad}"
    return f"{seg}s"


def ventana(duracion, escala):
    return texto(segundos(duracion) * escala["factor"])


def etiquetas(severidad, runbook, accion, **extra):
    return {"severidad": severidad, "servicio": SERVICIO, "runbook": runbook, "accion": accion, **extra}


def reglas_slo(escala):
    registro, alertas = [], []
    for slo in SLOS:
        usadas = {"30d"} | {v for q in slo.severidades for v in QUEMAS[q][1:3]}
        for v in sorted(usadas, key=segundos):
            real = ventana(v, escala)
            registro.append({"record": f"slo:proporcion_errores:v{v}", "labels": {"slo": slo.nombre, "ventana_real": real},
                             "expr": f"({slo.malos.replace('{v}', real)}) / ({slo.totales.replace('{v}', real)})"})
            registro.append({"record": f"slo:solicitudes:v{v}", "labels": {"slo": slo.nombre, "ventana_real": real},
                             "expr": slo.totales.replace("rate(", "increase(").replace("{v}", real)})
        registro.append({"record": "slo:presupuesto_restante", "labels": {"slo": slo.nombre},
                         "expr": f'clamp_min(1 - (slo:proporcion_errores:v30d{{slo="{slo.nombre}"}} / {slo.presupuesto}), -1)'})
        registro.append({"record": "slo:objetivo", "labels": {"slo": slo.nombre}, "expr": f"vector({slo.objetivo})"})
        for quema, (severidad, accion, runbook) in slo.severidades.items():
            factor, larga, corta, espera, gasto = QUEMAS[quema]
            umbral = round(factor * slo.presupuesto, 6)
            nombre = {"disponibilidad": "PresupuestoErrores", "latencia": "Latencia"}[slo.nombre] + "Quema" + quema.capitalize()
            dias_restantes = slo.ventana_dias / factor
            alertas.append({
                "alert": nombre,
                "expr": (f'slo:proporcion_errores:v{larga}{{slo="{slo.nombre}"}} > {umbral} '
                         f'and on(slo) slo:proporcion_errores:v{corta}{{slo="{slo.nombre}"}} > {umbral} '
                         f'and on(slo) slo:solicitudes:v{larga}{{slo="{slo.nombre}"}} >= {MIN_SOLICITUDES}'),
                "for": ventana(espera, escala),
                "labels": etiquetas(severidad, runbook, accion, slo=slo.nombre),
                "annotations": {
                    "summary": f"{slo.titulo}: el presupuesto de errores se quema {factor:g} veces más rápido de lo permitido",
                    "description": (f"Proporción de eventos malos en {ventana(larga, escala)} = {{{{ $value | humanizePercentage }}}} "
                                    f"(el SLO permite {slo.presupuesto:.1%}). Se gasta {gasto}; a este ritmo el "
                                    f"presupuesto se acaba en {dias_restantes:.1f} días."),
                    "runbook_url": f"{REPO}#{runbook.lower()}"}})
    return registro, alertas


def otras_alertas(escala):
    e = lambda d: ventana(d, escala)             # noqa: E731
    sonda = segundos(e("6m"))
    return [
        {"alert": "ServicioCaido", "expr": f'up{{job="api"}} == 0', "for": e("3m"),
         "labels": etiquetas("P1", "RB-01", "reiniciar"),
         "annotations": {"summary": "La API de diagnóstico no responde a Prometheus",
                         "description": "El objetivo api lleva caído al menos " + e("3m") + ". Nadie puede diagnosticar.",
                         "runbook_url": f"{REPO}#rb-01"}},
        {"alert": "CalidadModeloDegradada", "expr": 'sonda_slo_cumplido{conjunto="golden"} == 0', "for": e("4m"),
         "labels": etiquetas("P2", "RB-04", "rollback"),
         "annotations": {"summary": "La sonda detecta que la calidad del modelo bajó del SLO",
                         "description": "En el golden set: F1 macro < {{ with query \"sonda_umbral{metrica='f1_min'}\" }}"
                                        "{{ . | first | value }}{{ end }} o enfermas reportadas como sanas por arriba del "
                                        "límite. Típico de un release malo del modelo.",
                         "runbook_url": f"{REPO}#rb-04"}},
        {"alert": "DistribucionPrediccionesCambio",
         "expr": 'drift_psi{variable="p_enferma"} > 0.25 and on() drift_muestras >= 50', "for": e("10m"),
         "labels": etiquetas("P3", "RB-04", "ninguna"),
         "annotations": {"summary": "Cambió la distribución de P(enferma) respecto a la línea base",
                         "description": "PSI de P(enferma) = {{ $value }}. Sin etiquetas, es la primera señal de un "
                                        "cambio de modelo o de datos.", "runbook_url": f"{REPO}#rb-04"}},
        {"alert": "DriftDatosEntrada",
         "expr": 'max(drift_psi{variable=~"brillo|contraste|nitidez|verdor"}) > 0.25 and on() drift_muestras >= 50',
         "for": e("12m"), "labels": etiquetas("P3", "RB-05", "solicitar_reentrenamiento"),
         "annotations": {"summary": "Las fotos que llegan ya no se parecen a las del entrenamiento (data drift)",
                         "description": "PSI máximo de las características de entrada = {{ $value }} (más de 0.25 es "
                                        "un cambio importante).", "runbook_url": f"{REPO}#rb-05"}},
        {"alert": "DriftDatosAdvertencia",
         "expr": 'max(drift_psi{variable=~"brillo|contraste|nitidez|verdor"}) > 0.2 and on() drift_muestras >= 50',
         "for": e("12m"), "labels": etiquetas("info", "RB-05", "ninguna"),
         "annotations": {"summary": "Las fotos empiezan a cambiar (PSI entre 0.2 y 0.25)",
                         "description": "PSI máximo = {{ $value }}. Solo se registra. El umbral de 0.2 (y no el 0.1 "
                                        "de la regla general) sale de la calibración con tráfico normal: entre "
                                        "sesiones de campo distintas el PSI ronda 0.1 y llega a 0.18 (p99).",
                         "runbook_url": f"{REPO}#rb-05"}},
        {"alert": "FotosFueraDeDominio",
         "expr": (f'sum(rate(entrada_fuera_de_rango_total{{variable="verdor",origen="usuario"}}[{e("1h")}])) / '
                  f'sum(rate(entrada_verdor_count{{origen="usuario"}}[{e("1h")}])) > 0.05'),
         "for": e("12m"), "labels": etiquetas("info", "RB-05", "ninguna"),
         "annotations": {"summary": "Más del 5 % de las fotos no parecen hojas de maíz",
                         "description": "Proporción = {{ $value | humanizePercentage }}.", "runbook_url": f"{REPO}#rb-05"}},
        {"alert": "SaturacionMemoria", "expr": 'process_resident_memory_bytes{job="api"} / 1073741824 > 0.85',
         "for": e("10m"), "labels": etiquetas("P3", "RB-02", "ninguna"),
         "annotations": {"summary": "La API usa más del 85 % de su límite de memoria",
                         "description": "Uso = {{ $value | humanizePercentage }} de 1 GiB.", "runbook_url": f"{REPO}#rb-02"}},
        {"alert": "MonitoreoSinDatos",
         "expr": (f'up{{job=~"vigilante|operaciones"}} == 0 or '
                  f'(time() - sonda_ultima_ejecucion_timestamp_segundos) > {4 * sonda}'),
         "for": e("6m"), "labels": etiquetas("P3", "RB-06", "ninguna"),
         "annotations": {"summary": "El monitoreo mismo dejó de reportar (sonda u operaciones caídos)",
                         "description": "Sin sonda no se detecta un release malo; sin operaciones no hay respuesta "
                                        "automática.", "runbook_url": f"{REPO}#rb-06"}},
    ]


def alertmanager(escala):
    receptor = lambda canal: {"name": canal, "webhook_configs": [  # noqa: E731
        {"url": f"http://operaciones:9102/alertas/{canal}", "send_resolved": True, "max_alerts": 0}]}
    return {
        "route": {"receiver": "registro", "group_by": ["alertname", "servicio"],
                  "group_wait": escala["espera_grupo"], "group_interval": escala["intervalo_grupo"],
                  "repeat_interval": escala["repeticion"],
                  "routes": [{"matchers": ['severidad=~"P1|P2"'], "receiver": "pagina"},
                             {"matchers": ['severidad="P3"'], "receiver": "ticket"},
                             {"matchers": ['severidad="info"'], "receiver": "registro"}]},
        "inhibit_rules": [
            # Si el servicio está caído, todo lo demás es consecuencia: solo se avisa la causa
            {"source_matchers": ['alertname="ServicioCaido"'], "target_matchers": ['alertname!="ServicioCaido"'],
             "equal": ["servicio"]},
            # Un incidente P1 silencia los avisos menores del mismo servicio
            {"source_matchers": ['severidad="P1"'], "target_matchers": ['severidad=~"P2|P3|info"'], "equal": ["servicio"]},
            # La quema rápida hace redundantes las más lentas del mismo SLO
            {"source_matchers": ['alertname=~".*QuemaRapida"'], "target_matchers": ['alertname=~".*Quema(Media|Lenta)"'],
             "equal": ["slo"]},
            {"source_matchers": ['alertname=~".*QuemaMedia"'], "target_matchers": ['alertname=~".*QuemaLenta"'],
             "equal": ["slo"]},
            # El drift de entrada explica el cambio en las predicciones y vuelve redundante la advertencia
            {"source_matchers": ['alertname="DriftDatosEntrada"'],
             "target_matchers": ['alertname=~"DriftDatosAdvertencia|DistribucionPrediccionesCambio"'],
             "equal": ["servicio"]},
            # Un release malo (lo confirma la sonda) también explica el cambio en las predicciones
            {"source_matchers": ['alertname="CalidadModeloDegradada"'],
             "target_matchers": ['alertname="DistribucionPrediccionesCambio"'], "equal": ["servicio"]},
        ],
        "receivers": [receptor("pagina"), receptor("ticket"), receptor("registro")],
    }


def prometheus(escala):
    trabajo = lambda nombre, destino: {"job_name": nombre, "static_configs": [  # noqa: E731
        {"targets": [destino], "labels": {"servicio": SERVICIO}}]}
    return {"global": {"scrape_interval": escala["lectura"], "evaluation_interval": escala["evaluacion"],
                       "external_labels": {"entorno": "laboratorio" if escala["factor"] < 1 else "produccion"}},
            "rule_files": ["/etc/prometheus/reglas/*.yml"],
            "alerting": {"alertmanagers": [{"static_configs": [{"targets": ["alertmanager:9093"]}]}]},
            "scrape_configs": [trabajo("api", "api:8000"), trabajo("vigilante", "vigilante:9101"),
                               trabajo("operaciones", "operaciones:9102"), trabajo("prometheus", "localhost:9090"),
                               trabajo("alertmanager", "alertmanager:9093"), trabajo("loki", "loki:3100")]}


class _Yaml(yaml.SafeDumper):
    pass


_Yaml.add_representer(str, lambda d, s: d.represent_scalar("tag:yaml.org,2002:str", s,
                                                           style="|" if "\n" in s else None))


def escribir(ruta, datos, encabezado):
    ruta.parent.mkdir(parents=True, exist_ok=True)
    cuerpo = yaml.dump(datos, Dumper=_Yaml, allow_unicode=True, sort_keys=False, width=120)
    ruta.write_text(f"# GENERADO por monitoreo/slo.py ({encabezado}). No editar a mano.\n" + cuerpo,
                    encoding="utf-8", newline="\n")
    return ruta


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--escala", choices=ESCALAS, default="laboratorio")
    parser.add_argument("--salida", type=Path, default=AQUI)
    args = parser.parse_args()
    escala = ESCALAS[args.escala]
    registro, alertas = reglas_slo(escala)
    alertas += otras_alertas(escala)
    reglas = {"groups": [{"name": "slo_registro", "rules": registro}, {"name": "alertas", "rules": alertas}]}
    for ruta in (escribir(args.salida / "prometheus" / "prometheus.yml", prometheus(escala), args.escala),
                 escribir(args.salida / "prometheus" / "reglas" / "slo.yml", reglas, args.escala),
                 escribir(args.salida / "alertmanager" / "alertmanager.yml", alertmanager(escala), args.escala)):
        print(f"escrito {ruta.relative_to(args.salida.parent) if args.salida == AQUI else ruta}")
    print(f"{len(registro)} reglas de registro y {len(alertas)} alertas ({args.escala})")
    for a in alertas:
        print(f"  {a['labels']['severidad']:4s} {a['alert']:32s} for {a['for']:5s} -> {a['labels']['runbook']} "
              f"({a['labels']['accion']})")


if __name__ == "__main__":
    main()
