"""Experimento 4: escalado horizontal (varias réplicas detrás de un balanceador) y lo que rompe del diseño actual.

Levanta N réplicas de la imagen ligera (1 vCPU, 1 GiB cada una) detrás de un balanceador nginx en round robin, que
es lo que hace Cloud Run por dentro cuando agrega instancias. Tres pruebas:

A. Capacidad: con 1, 2 y 3 réplicas y la misma concurrencia por réplica, ¿crece la capacidad en proporción?
B. Tolerancia a fallos: a mitad de una prueba de carga se mata una réplica (docker kill). Con 1 réplica el
   servicio se cae; con 3, el balanceador manda las solicitudes a las que quedan. Se cuentan los errores.
C. Consistencia del modelo al hacer rollback: el laboratorio de la Actividad 8 regresa de versión moviendo el alias
   del registro y llamando a /api/admin/recargar. Con 3 réplicas detrás del balanceador esa llamada llega solo a
   una, así que el servicio queda sirviendo dos versiones a la vez. Se cuenta qué versión responde en 90
   solicitudes, primero con una sola llamada y luego llamando a cada réplica.

El registro de modelos se monta desde una copia temporal (solo lectura para las réplicas), para no tocar el del
laboratorio.

Uso:
    python escalamiento/horizontal.py
Deja evidencias/escalamiento/horizontal/resultados.json, resumen.md y falla_replica.png.
"""
import json
import shutil
import sys
import tempfile
import threading
import time
from collections import Counter
from pathlib import Path

import httpx
import matplotlib
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from escalamiento import comun  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

SALIDA = comun.EVIDENCIAS / "horizontal"
IMAGEN = "diagnostico-hoja-maiz-api:0.6.0-onnx"
NGINX = "nginx:1.27.5-alpine"
RED = "act9-red"
PUERTO_LB = 8500
TOKEN = "token-experimento-act9"
CONCURRENCIA_POR_REPLICA = 2
URL = f"http://localhost:{PUERTO_LB}"


def configuracion_nginx(n):
    servidores = "\n".join(f"    server act9-rep-{i}:8000 max_fails=1 fail_timeout=30s;" for i in range(1, n + 1))
    # El diagnóstico no cambia nada en el servidor (es idempotente), así que se permite reintentar un POST en otra
    # réplica si la primera no responde (non_idempotent); por defecto nginx no reintenta los POST.
    return f"""events {{}}
http {{
  upstream api {{
{servidores}
  }}
  server {{
    listen 80;
    client_max_body_size 12m;
    location / {{
      proxy_pass http://api;
      proxy_next_upstream error timeout http_502 http_503 non_idempotent;
      proxy_connect_timeout 1s;
      proxy_read_timeout 60s;
    }}
  }}
}}
"""


class Grupo:
    """N réplicas + balanceador en una red de Docker."""

    def __init__(self, n, modelo, temporal):
        self.n, self.modelo, self.temporal = n, modelo, temporal

    def __enter__(self):
        comun.docker("network", "create", RED)
        for i in range(1, self.n + 1):
            comun.docker("rm", "-f", f"act9-rep-{i}")
            comun.docker("run", "-d", "--name", f"act9-rep-{i}", "--network", RED, "--cpus", "1", "--memory", "1g",
                         "-p", f"{8510 + i}:8000", "-e", f"TOKEN_ADMIN={TOKEN}",
                         "-v", f"{self.modelo}:/app/modelo:ro", IMAGEN, revisar=True)
        for i in range(1, self.n + 1):
            if comun.esperar_salud(f"http://localhost:{8510 + i}") is None:
                raise RuntimeError(f"la réplica {i} no arrancó")
        conf = self.temporal / f"nginx_{self.n}.conf"
        conf.write_text(configuracion_nginx(self.n), encoding="utf-8")
        comun.docker("rm", "-f", "act9-lb")
        comun.docker("run", "-d", "--name", "act9-lb", "--network", RED, "-p", f"{PUERTO_LB}:80",
                     "-v", f"{conf}:/etc/nginx/nginx.conf:ro", NGINX, revisar=True)
        if comun.esperar_salud(URL, 30) is None:
            raise RuntimeError("el balanceador no responde")
        return self

    def __exit__(self, *_):
        comun.quitar("act9-lb")
        for i in range(1, self.n + 1):
            comun.quitar(f"act9-rep-{i}")


def prueba_capacidad(fotos, modelo, temporal):
    salida = []
    for n in (1, 2, 3):
        with Grupo(n, modelo, temporal):
            r = comun.carga(URL, CONCURRENCIA_POR_REPLICA * n, 30, fotos)
        r["replicas"] = n
        salida.append(r)
        print(f"A. {n} réplica(s): {r['rps']}/s  p95 {r['p95_s']}  p99 {r['p99_s']}  errores {r['errores']}",
              flush=True)
    return salida


def prueba_falla(fotos, modelo, temporal):
    salida = {}
    for n in (1, 3):
        with Grupo(n, modelo, temporal):
            resultado = {}
            hilo = threading.Thread(target=lambda: resultado.update(
                comun.carga(URL, CONCURRENCIA_POR_REPLICA * n, 40, fotos, crudo=True, timeout_s=10)))
            hilo.start()
            time.sleep(5 + 15)                          # 5 s de calentamiento + 15 s de carga normal
            comun.docker("kill", "act9-rep-1")
            hilo.join()
        registros = resultado.pop("registros")
        despues = [r for r in registros if r[0] >= 15]
        resultado.update(replicas=n, muerta_en_s=15,
                         errores_despues=sum(1 for r in despues if r[2] != 200), solicitudes_despues=len(despues),
                         exitosas_despues=sum(1 for r in despues if r[2] == 200),
                         serie=[[s, sum(1 for r in registros if int(r[0]) == s and r[2] == 200),
                                 sum(1 for r in registros if int(r[0]) == s and r[2] != 200)] for s in range(40)])
        salida[str(n)] = resultado
        print(f"B. {n} réplica(s), una muerta a los 15 s: después {resultado['exitosas_despues']} bien y "
              f"{resultado['errores_despues']} con error", flush=True)
    return salida


def versiones_servidas(veces=90):
    with httpx.Client(base_url=URL, timeout=10) as http:
        return dict(Counter(http.get("/api/salud").json()["modelo_version"] for _ in range(veces)))


def prueba_rollback(modelo, temporal):
    registro = modelo / "registro.json"
    original = registro.read_text(encoding="utf-8")
    datos = json.loads(original)
    actual = datos["alias"]["champion"]
    anterior = "1"
    salida = {"version_en_servicio": actual, "rollback_a": anterior}
    try:
        with Grupo(3, modelo, temporal):
            salida["antes"] = versiones_servidas()
            datos["alias"]["champion"] = anterior
            registro.write_text(json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8")
            r = httpx.post(f"{URL}/api/admin/recargar", headers={"X-Token-Admin": TOKEN}, timeout=60)
            salida["una_llamada_al_balanceador"] = {"respuesta": r.json(), "versiones": versiones_servidas()}
            for i in (1, 2, 3):
                httpx.post(f"http://localhost:{8510 + i}/api/admin/recargar", headers={"X-Token-Admin": TOKEN},
                           timeout=60)
            salida["llamando_a_cada_replica"] = {"versiones": versiones_servidas()}
    finally:
        registro.write_text(original, encoding="utf-8")
    print("C.", salida["antes"], "->", salida["una_llamada_al_balanceador"]["versiones"], "->",
          salida["llamando_a_cada_replica"]["versiones"], flush=True)
    return salida


def grafica(falla):
    fig, ejes = plt.subplots(1, 2, figsize=(10, 3.6), sharey=True)
    for ax, n in zip(ejes, ("1", "3")):
        serie = np.array(falla[n]["serie"])
        ax.bar(serie[:, 0], serie[:, 1], color="#27ae60", label="respondidas")
        ax.bar(serie[:, 0], serie[:, 2], bottom=serie[:, 1], color="#c0392b", label="con error")
        ax.axvline(15, color="black", ls="--", lw=1)
        ax.text(15.3, ax.get_ylim()[1] * 0.9, "réplica muerta", fontsize=8)
        ax.set_title(f"{n} réplica(s)")
        ax.set_xlabel("segundo de la prueba")
    ejes[0].set_ylabel("solicitudes por segundo")
    ejes[1].legend(loc="lower right", fontsize=8)
    fig.tight_layout()
    fig.savefig(SALIDA / "falla_replica.png", dpi=150)


def main():
    comun.docker("pull", NGINX, revisar=True)
    fotos = comun.fotos_trafico()
    with tempfile.TemporaryDirectory(prefix="act9_") as t:
        temporal = Path(t)
        modelo = temporal / "modelo"
        shutil.copytree(comun.RAIZ / "modelo", modelo)
        resultados = {"fecha": time.strftime("%Y-%m-%d %H:%M:%S"), "imagen": IMAGEN, "balanceador": NGINX,
                      "replica": "1 vCPU, 1 GiB", "concurrencia_por_replica": CONCURRENCIA_POR_REPLICA}
        try:
            resultados["capacidad"] = prueba_capacidad(fotos, modelo, temporal)
            resultados["falla"] = prueba_falla(fotos, modelo, temporal)
            resultados["rollback"] = prueba_rollback(modelo, temporal)
        finally:
            comun.quitar("act9-lb")
            for i in (1, 2, 3):
                comun.quitar(f"act9-rep-{i}")
            comun.docker("network", "rm", RED)
    comun.guardar_json(SALIDA / "resultados.json", resultados)
    grafica(resultados["falla"])
    c = resultados["capacidad"]
    lineas = ["| Réplicas | Concurrencia | Diag./s | p95 (s) | p99 (s) | Errores |", "|---|---|---|---|---|---|"]
    lineas += [f"| {r['replicas']} | {r['concurrencia']} | {r['rps']} | {r['p95_s']} | {r['p99_s']} | "
               f"{r['errores']} |" for r in c]
    lineas += ["", "| Réplicas | Después de matar una: respondidas | con error |", "|---|---|---|"]
    lineas += [f"| {n} | {f['exitosas_despues']} | {f['errores_despues']} |" for n, f in resultados["falla"].items()]
    rb = resultados["rollback"]
    lineas += ["", f"Rollback v{rb['version_en_servicio']} -> v{rb['rollback_a']} con 3 réplicas: antes "
                   f"{rb['antes']}; una llamada al balanceador {rb['una_llamada_al_balanceador']['versiones']}; "
                   f"llamando a cada réplica {rb['llamando_a_cada_replica']['versiones']}"]
    (SALIDA / "resumen.md").write_text("\n".join(lineas) + "\n", encoding="utf-8")
    print("\n".join(lineas))


if __name__ == "__main__":
    main()
