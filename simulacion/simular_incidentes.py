"""Simulación de incidentes críticos (game day) contra el laboratorio de monitoreo.

Con tráfico de usuarios corriendo todo el tiempo (2 fotos por segundo), inyecta cinco incidentes, uno a la vez, y
mide cómo responde el sistema sin que nadie toque nada (salvo los pasos manuales que el runbook le deja a una
persona, que el script hace como lo haría quien está de guardia):

    INC-01  Caída del contenedor (docker stop)                          ServicioCaido           RB-01  automático
    INC-02  CPU estrangulada a 0.2 núcleos (docker update)              LatenciaQuemaRapida     RB-02  automático
    INC-03  Falla en la extracción de características (50 % con 500)    PresupuestoErroresQuemaRapida  RB-03  automático
    INC-04  Release malo: v2 con etiquetas cruzadas, sin compuerta      CalidadModeloDegradada  RB-04  automático
    INC-05  Temporada de lluvias: 50 % de fotos oscuras y borrosas      DriftDatosEntrada       RB-05  semiautomático

Por cada incidente registra la línea de tiempo (inyección, primera vez que Alertmanager tiene la alerta, cuándo
la recibe operaciones, sus acciones y verificaciones, y la resolución), todas las alertas que se vieron en
Alertmanager (activas o inhibidas), lo que vivió el usuario (solicitudes fallidas y latencia del lado del cliente)
y las métricas:
    MTTD  inyección -> alerta en Alertmanager            (tiempo para detectar)
    MTTA  inyección -> operaciones recibe la alerta      (tiempo para empezar a responder)
    MTTR  inyección -> servicio recuperado y alerta resuelta
Antes de cada incidente espera a que el sistema esté estable (sin alertas P1 a P3). Al final compara cuántas
alertas habría dado un monitoreo ingenuo (umbral sobre una métrica suelta) contra las notificaciones que de verdad
llegaron (reducción de ruido).

Uso (el laboratorio debe estar arriba y la v2 defectuosa registrada):
    python simulacion/simular_incidentes.py                  # los cinco, unos 45 minutos
    python simulacion/simular_incidentes.py --solo INC-02    # uno
"""
import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(RAIZ / "monitoreo" / "servicios"))
import comun  # noqa: E402
from trafico import Trafico  # noqa: E402

API, PROM, AM, OPS, VIG = ("http://localhost:8000", "http://localhost:9090", "http://localhost:9093",
                          "http://localhost:9102", "http://localhost:9101")
TOKEN = os.environ.get("TOKEN_ADMIN", "laboratorio-maiz")
CONTENEDOR = "diagnostico-hoja-maiz"
DOCKER = os.environ.get("DOCKER", r"C:\Users\rabin\AppData\Local\Programs\DockerDesktop\resources\bin\docker.exe")
SALIDA = RAIZ / "evidencias" / "monitoreo" / "simulacion"
DIR_MODELOS = RAIZ / "modelo"
SEVERIDADES = {"P1", "P2", "P3"}


def ahora():
    return time.time()


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat(timespec="seconds")


def segundos_iso(texto):
    return datetime.fromisoformat(texto.replace("Z", "+00:00")).timestamp()


def docker(*args):
    return subprocess.run([DOCKER, *args], capture_output=True, encoding="utf-8", check=True).stdout.strip()


def python(*args):
    r = subprocess.run([sys.executable, *args], capture_output=True, encoding="utf-8", cwd=RAIZ)
    salida = "\n".join(l for l in (r.stdout + r.stderr).splitlines()
                       if l.strip() and not any(x in l for x in ("Warning", "Loading", "LOAD", "Key ", "-----", "classifier",
                                                                 "Notes", "UNEXPECTED", "[transformers]")))
    return r.returncode, salida


def alertmanager():
    r = httpx.get(f"{AM}/api/v2/alerts", params={"active": "true", "silenced": "true", "inhibited": "true"}, timeout=10)
    return r.json()


def prom(expr):
    try:
        return comun.escalar(expr, PROM)
    except httpx.HTTPError:
        return None


def bitacora(desde):
    try:
        entradas = httpx.get(f"{OPS}/bitacora", params={"ultimas": 2000}, timeout=10).json()
    except httpx.HTTPError:
        return []
    return [e for e in entradas if segundos_iso(e["ts"]) >= desde]


def estable():
    activas = [a for a in alertmanager() if a["labels"].get("severidad") in SEVERIDADES]
    return not activas and prom('up{job="api"}') == 1 and prom('sonda_slo_cumplido{conjunto="golden"}') == 1


def esperar(condicion, limite, paso=2.0, al_esperar=None):
    inicio = ahora()
    while ahora() - inicio < limite:
        try:
            if condicion():
                return True
        except Exception:                               # noqa: BLE001 - el servicio puede estar caído a propósito
            pass
        if al_esperar:
            al_esperar()
        time.sleep(paso)
    return False


# ------------------------------------------------------------------------------------------------ incidentes
def inc01_inyectar(ctx):
    docker("stop", "-t", "5", CONTENEDOR)
    return "docker stop diagnostico-hoja-maiz"


def api_sana():
    return httpx.get(f"{API}/api/salud", timeout=5).json()["estado"] == "ok" and prom('up{job="api"}') == 1


def inc02_inyectar(ctx):
    docker("update", "--cpus", "0.2", CONTENEDOR)
    return "docker update --cpus 0.2 diagnostico-hoja-maiz (antes: 1.0)"


def inc02_resuelto(ctx):
    cpus = int(docker("inspect", "-f", "{{.HostConfig.NanoCpus}}", CONTENEDOR)) / 1e9
    p95 = prom('histogram_quantile(0.95, sum by (le) (rate(http_duracion_segundos_bucket{ruta="/api/diagnostico",'
               'origen="usuario",resultado="ok"}[30s])))')
    return cpus == 1.0 and p95 is not None and p95 < 0.5


def inc03_inyectar(ctx):
    r = httpx.post(f"{API}/api/admin/fallas", headers={"X-Token-Admin": TOKEN}, timeout=10,
                   json={"etapa": "extraer_caracteristicas", "tasa": 0.5, "duracion_s": 1800})
    r.raise_for_status()
    return "POST /api/admin/fallas: 50 % de errores en extraer_caracteristicas durante 30 min"


def inc03_resuelto(ctx):
    # Después de un reinicio el contador de 500 no existe hasta el siguiente error: sin serie cuenta como 0
    errores = prom('sum(rate(http_solicitudes_total{ruta="/api/diagnostico",origen="usuario",codigo=~"5.."}[30s]))') or 0.0
    return errores == 0 and httpx.get(f"{API}/api/salud", timeout=5).json()["falla_inyectada"] is None


def inc03_limpiar(ctx):
    httpx.delete(f"{API}/api/admin/fallas", headers={"X-Token-Admin": TOKEN}, timeout=10)


def inc04_inyectar(ctx):
    codigo, salida = python("scripts/desplegar.py", "--version", "2", "--sin-compuerta",
                            "--motivo", "Hotfix urgente: reentrenamiento con fotos nuevas")
    ctx["salida_despliegue"] = salida
    if codigo != 0:
        raise RuntimeError(salida)
    return "scripts/desplegar.py --version 2 --sin-compuerta (v2: etiquetas Sana y Mancha gris cruzadas)"


def inc04_resuelto(ctx):
    return (comun.leer_registro(DIR_MODELOS)["alias"]["champion"] == "1"
            and prom('sonda_slo_cumplido{conjunto="golden"}') == 1
            and prom('max(modelo_info{version="1"})') == 1)


def inc05_inyectar(ctx):
    ctx["trafico"].proporcion_lluvia = 0.5
    return "El 50 % de las fotos de los usuarios llega en condición de lluvia (oscuras y borrosas)"


def inc05_respuesta_manual(ctx):
    """Lo que hace la persona de guardia con la solicitud de reentrenamiento (RB-05, pasos 2 a 4)."""
    if ctx.get("manual_hecho") or not ctx.get("solicitud"):
        return
    ctx["manual_hecho"] = True
    pasos = ctx.setdefault("pasos_manuales", [])
    pasos.append({"ts": iso(ahora()), "paso": "Revisar que no es un error del pipeline: las fotos siguen llegando "
                                             "como PNG de 224x224 desde la misma app; el cambio es de iluminación y nitidez"})
    codigo, salida = python("scripts/reentrenar.py", "--solicitud", ctx["solicitud"])
    pasos.append({"ts": iso(ahora()), "paso": "python scripts/reentrenar.py", "codigo": codigo, "salida": salida[-1500:]})
    nueva = comun.leer_registro(DIR_MODELOS)
    nueva = max(nueva["versiones"], key=int)
    ctx["version_nueva"] = nueva
    codigo, salida = python("scripts/desplegar.py", "--version", nueva, "--motivo",
                            "Temporada de lluvias: respuesta a la solicitud de reentrenamiento (RB-05)",
                            "--aprobado-por", "Rabin Muñoz")
    pasos.append({"ts": iso(ahora()), "paso": f"python scripts/desplegar.py --version {nueva} --aprobado-por",
                  "codigo": codigo, "salida": salida[-2000:]})


def inc05_resuelto(ctx):
    nueva = ctx.get("version_nueva")
    return bool(nueva) and comun.leer_registro(DIR_MODELOS)["alias"]["champion"] == nueva and \
        (prom('max(drift_psi{variable=~"brillo|contraste|nitidez|verdor"})') or 9.0) < 0.25


INCIDENTES = [
    {"id": "INC-01", "nombre": "Caída del contenedor de la API", "alerta": "ServicioCaido", "runbook": "RB-01",
     "inyectar": inc01_inyectar, "resuelto": lambda c: api_sana(), "limite": 300},
    {"id": "INC-02", "nombre": "CPU estrangulada (vecino ruidoso o límite mal configurado)",
     "alerta": "LatenciaQuemaRapida", "runbook": "RB-02", "inyectar": inc02_inyectar, "resuelto": inc02_resuelto,
     "limite": 600},
    {"id": "INC-03", "nombre": "Falla interna en la extracción de características (50 % de errores 500)",
     "alerta": "PresupuestoErroresQuemaRapida", "runbook": "RB-03", "inyectar": inc03_inyectar,
     "resuelto": inc03_resuelto, "limpiar": inc03_limpiar, "limite": 600},
    {"id": "INC-04", "nombre": "Release malo del modelo (v2 sin compuerta de calidad)", "alerta": "CalidadModeloDegradada",
     "runbook": "RB-04", "inyectar": inc04_inyectar, "resuelto": inc04_resuelto, "limite": 600},
    {"id": "INC-05", "nombre": "Data drift: temporada de lluvias", "alerta": "DriftDatosEntrada", "runbook": "RB-05",
     "inyectar": inc05_inyectar, "resuelto": inc05_resuelto, "manual": inc05_respuesta_manual, "limite": 1500},
]


def correr(incidente, trafico, registro_global):
    ctx = {"trafico": trafico}
    print(f"\n== {incidente['id']} {incidente['nombre']}", flush=True)
    if not esperar(estable, 900, 5):
        print("   (el sistema no quedó estable en 15 min; se sigue de todos modos)")
    t0 = ahora()
    descripcion = incidente["inyectar"](ctx)
    print(f"   {iso(t0)} inyección: {descripcion}", flush=True)
    vistas, t_detectada, t_resuelta, inicio_alerta = {}, None, None, None

    def observar():
        nonlocal t_detectada, inicio_alerta
        for a in alertmanager():
            nombre = a["labels"]["alertname"]
            estado = a["status"]["state"]
            clave = (nombre, a["startsAt"][:19])
            if clave not in vistas:
                vistas[clave] = {"alerta": nombre, "severidad": a["labels"].get("severidad"), "inicio": a["startsAt"],
                                 "primera_vez_vista": iso(ahora()), "estados": set()}
            vistas[clave]["estados"].add(estado + (" (inhibida)" if a["status"].get("inhibitedBy") else ""))
            if nombre == incidente["alerta"] and t_detectada is None and segundos_iso(a["startsAt"]) >= t0 - 5:
                t_detectada, inicio_alerta = ahora(), a["startsAt"]
                print(f"   {iso(t_detectada)} alerta {nombre} en Alertmanager", flush=True)
        if incidente["id"] == "INC-05" and not ctx.get("solicitud"):
            for e in bitacora(t0):
                if e["evento"] == "accion" and e.get("archivo", "").startswith("reentrenamiento_"):
                    ctx["solicitud"] = str(RAIZ / "monitoreo" / "bitacora" / "solicitudes" / e["archivo"])
                    print(f"   {e['ts']} operaciones creó {e['archivo']}; respuesta manual del runbook…", flush=True)
        if incidente.get("manual"):
            incidente["manual"](ctx)

    def recuperado():
        observar()
        activa = any(a["labels"]["alertname"] == incidente["alerta"] for a in alertmanager())
        return t_detectada is not None and not activa and incidente["resuelto"](ctx)

    ok = esperar(recuperado, incidente["limite"], 2.0)
    t_resuelta = ahora() if ok else None
    print(f"   {iso(ahora())} {'resuelto' if ok else 'SIN RESOLVER en el límite'}", flush=True)
    if incidente.get("limpiar"):
        incidente["limpiar"](ctx)
    fin = t_resuelta or ahora()
    eventos = bitacora(t0 - 2)
    recibida = next((segundos_iso(e["ts"]) for e in eventos
                     if e["evento"] == "alerta_recibida" and e.get("alerta") == incidente["alerta"]), None)
    resultado = {
        "id": incidente["id"], "nombre": incidente["nombre"], "runbook": incidente["runbook"],
        "alerta_esperada": incidente["alerta"], "inyeccion": descripcion, "t_inyeccion": iso(t0),
        "t_alerta_alertmanager": iso(t_detectada) if t_detectada else None, "inicio_segun_prometheus": inicio_alerta,
        "t_recibida_operaciones": iso(recibida) if recibida else None, "t_resuelto": iso(t_resuelta) if t_resuelta else None,
        "mttd_s": round(t_detectada - t0, 1) if t_detectada else None,
        "mtta_s": round(recibida - t0, 1) if recibida else None,
        "mttr_s": round(t_resuelta - t0, 1) if t_resuelta else None,
        "resuelto": ok, "impacto_usuario": trafico.resumen(t0, fin),
        "alertas_vistas": [{**v, "estados": sorted(v["estados"])} for v in vistas.values()],
        "bitacora": eventos, "pasos_manuales": ctx.get("pasos_manuales", []),
        "salida_despliegue": ctx.get("salida_despliegue"), "ventana": {"desde": iso(t0 - 60), "hasta": iso(fin + 60)},
    }
    print(f"   MTTD {resultado['mttd_s']} s · MTTA {resultado['mtta_s']} s · MTTR {resultado['mttr_s']} s · "
          f"usuario: {resultado['impacto_usuario']}", flush=True)
    registro_global.append(resultado)
    SALIDA.mkdir(parents=True, exist_ok=True)
    (SALIDA / f"{incidente['id']}.json").write_text(json.dumps(resultado, ensure_ascii=False, indent=2, default=str) + "\n",
                                                    encoding="utf-8", newline="\n")
    return resultado


def ruido(desde, hasta):
    """Episodios de alerta de un monitoreo ingenuo contra lo que se notificó de verdad."""
    ingenuas = {
        "cualquier error 5xx en 30 s": 'sum(increase(http_solicitudes_total{ruta="/api/diagnostico",codigo=~"5.."}[30s])) > 0',
        "latencia p95 > 0.5 s (1 min)": 'histogram_quantile(0.95, sum by (le) (rate(http_duracion_segundos_bucket{ruta="/api/diagnostico",resultado="ok"}[1m]))) > 0.5',
        "CPU > 80 % del límite": 'rate(process_cpu_seconds_total{job="api"}[30s]) > 0.8',
        "algún PSI > 0.1": 'max(drift_psi) > 0.1',
        "la API no responde": 'up{job="api"} == 0',
        "rechazos 4xx en 30 s": 'sum(increase(diagnostico_rechazos_total[30s])) > 0',
    }
    episodios = {}
    for nombre, expr in ingenuas.items():
        r = httpx.get(f"{PROM}/api/v1/query_range", timeout=30,
                      params={"query": f"({expr}) > bool 0", "start": desde, "end": hasta, "step": "15s"}).json()
        valores = [float(v[1]) for s in r["data"]["result"] for v in s["values"]]
        episodios[nombre] = sum(1 for a, b in zip([0.0] + valores, valores) if a == 0 and b == 1)
    reales = httpx.get(f"{PROM}/api/v1/query", params={"query": "sum by (canal) (operaciones_notificaciones_total)"},
                       timeout=10).json()["data"]["result"]
    return {"monitoreo_ingenuo_episodios": episodios, "total_ingenuo": sum(episodios.values()),
            "notificaciones_reales_por_canal": {r["metric"]["canal"]: float(r["value"][1]) for r in reales}}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--solo", nargs="*", help="ids de los incidentes a correr (por defecto, todos)")
    args = parser.parse_args()
    incidentes = [i for i in INCIDENTES if not args.solo or i["id"] in args.solo]
    trafico = Trafico(API, tasa=2.0, salida=SALIDA / "trafico.csv").iniciar()
    inicio = ahora()
    print(f"{iso(inicio)} tráfico de usuarios iniciado (2 fotos/s); calentando 3 min…", flush=True)
    time.sleep(180)
    resultados = []
    try:
        for incidente in incidentes:
            correr(incidente, trafico, resultados)
        time.sleep(30)
    finally:
        trafico.detener()
    resumen = {"inicio": iso(inicio), "fin": iso(ahora()), "incidentes": [
        {k: r[k] for k in ("id", "nombre", "alerta_esperada", "runbook", "mttd_s", "mtta_s", "mttr_s", "resuelto",
                           "impacto_usuario")} for r in resultados],
               "ruido": ruido(inicio, ahora()), "trafico_total": trafico.resumen()}
    (SALIDA / "resumen.json").write_text(json.dumps(resumen, ensure_ascii=False, indent=2, default=str) + "\n",
                                         encoding="utf-8", newline="\n")
    print(json.dumps(resumen, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
