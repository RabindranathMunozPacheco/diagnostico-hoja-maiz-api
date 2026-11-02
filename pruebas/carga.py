"""Prueba de carga y de recursos del contenedor.

1. Carga: manda las 8 fotos de ejemplo con 1, 2, 4, 8 y 16 peticiones al mismo tiempo contra el contenedor
   principal y mide latencia, peticiones por segundo, errores y memoria máxima.
2. Planes chicos de nube: levanta contenedores desechables con límites de memoria y CPU parecidos a los de
   un PaaS (docker run --memory --cpus) y mide si arrancan, cuánto tardan en estar healthy, la latencia y la
   memoria que usan. Sirve para fijar los requerimientos técnicos del manual de despliegue.

Deja en evidencias/carga/: resultados.json, resumen.md y latencia.png.

Uso:
    python pruebas/carga.py                                   # contenedor principal en http://localhost:8000
    python pruebas/carga.py --sin-limites                     # solo la parte 1
"""
import argparse
import json
import re
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import matplotlib
import numpy as np

from probar_api import EJEMPLOS, RAIZ, foto

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

DOCKER = shutil.which("docker") or "docker"
CONCURRENCIAS = (1, 2, 4, 8, 16)
# (nombre, memoria, cpus, puerto, variables extra): de un plan muy chico a uno holgado. El de 0.1 CPU es como el
# plan gratuito de Render; se repite con OMP_NUM_THREADS=1 porque PyTorch abre un hilo por núcleo de la máquina
# y, con una fracción de CPU, esos hilos se estorban entre sí.
PLANES = (("256 MB, 1 CPU", "256m", "1", 8111, []),
          ("512 MB, 1 CPU", "512m", "1", 8112, []),
          ("512 MB, 0.5 CPU", "512m", "0.5", 8113, []),
          ("512 MB, 0.5 CPU, 1 hilo", "512m", "0.5", 8116, ["OMP_NUM_THREADS=1"]),
          ("512 MB, 0.1 CPU", "512m", "0.1", 8114, []),
          ("512 MB, 0.1 CPU, 1 hilo", "512m", "0.1", 8117, ["OMP_NUM_THREADS=1"]),
          ("1 GB, 1 CPU", "1g", "1", 8115, []))
LENTO_MS = 5000          # si un diagnóstico tarda más que esto, el plan solo se mide con 3 peticiones en serie


def docker(*args):
    return subprocess.run([DOCKER, *args], capture_output=True, text=True, encoding="utf-8", errors="replace")


def a_mb(texto):
    """'328.6MiB' -> 328.6; '0B' o '--' (contenedor detenido) -> 0."""
    m = re.match(r"([\d.]+)\s*([KMG]?i?B)", texto)
    if not m:
        return 0.0
    numero, unidad = m.groups()
    return float(numero) * {"B": 1 / 2**20, "KiB": 1 / 1024, "KB": 1 / 1000, "MiB": 1, "MB": 1, "GiB": 1024,
                            "GB": 1000}[unidad]


class Memoria(threading.Thread):
    """Muestrea la memoria del contenedor mientras corre la prueba y guarda el máximo."""

    def __init__(self, contenedor):
        super().__init__(daemon=True)
        self.contenedor, self.maximo, self.alto = contenedor, 0.0, threading.Event()

    def run(self):
        while not self.alto.is_set():
            r = docker("stats", "--no-stream", "--format", "{{.MemUsage}}", self.contenedor)
            if r.returncode == 0 and r.stdout.strip():
                self.maximo = max(self.maximo, a_mb(r.stdout.split("/")[0].strip()))

    def detener(self):
        self.alto.set()
        self.join()
        return round(self.maximo, 1)


def rafaga(url, fotos, concurrencia, n):
    http = httpx.Client(base_url=url, timeout=300, limits=httpx.Limits(max_connections=concurrencia))

    def una(i):
        nombre, contenido = fotos[i % len(fotos)]
        inicio = time.perf_counter()
        try:
            r = http.post("/api/diagnostico", files={"foto": (nombre, contenido, "image/jpeg")})
            ok = r.status_code == 200
        except httpx.HTTPError:
            ok = False
        return (time.perf_counter() - inicio) * 1000, ok

    inicio = time.perf_counter()
    with ThreadPoolExecutor(concurrencia) as ex:
        resultados = list(ex.map(una, range(n)))
    total = time.perf_counter() - inicio
    ms = np.array([r[0] for r in resultados])
    return {"concurrencia": concurrencia, "peticiones": n, "errores": int(sum(not r[1] for r in resultados)),
            "latencia_p50_ms": round(float(np.percentile(ms, 50)), 1),
            "latencia_p95_ms": round(float(np.percentile(ms, 95)), 1),
            "latencia_max_ms": round(float(ms.max()), 1), "peticiones_por_segundo": round(n / total, 1)}


def esperar_salud(nombre, limite):
    inicio = time.perf_counter()
    while time.perf_counter() - inicio < limite:
        r = docker("inspect", "-f", "{{.State.Status}} {{.State.OOMKilled}} {{if .State.Health}}{{.State.Health.Status}}{{end}}",
                   nombre)
        estado = r.stdout.split()
        if estado and estado[-1] == "healthy":
            return round(time.perf_counter() - inicio, 1), "healthy"
        if estado and estado[0] in ("exited", "dead"):
            return round(time.perf_counter() - inicio, 1), "se detuvo" + (" (sin memoria, OOMKilled)" if estado[1] == "true" else "")
        time.sleep(1)
    return None, "no llegó a healthy"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--contenedor", default="diagnostico-hoja-maiz")
    parser.add_argument("--imagen", default="diagnostico-hoja-maiz-api:0.3.0")
    parser.add_argument("--salida", type=Path, default=RAIZ / "evidencias" / "carga")
    parser.add_argument("--sin-limites", action="store_true")
    args = parser.parse_args()
    args.salida.mkdir(parents=True, exist_ok=True)
    fotos = [(Path(e["ruta"]).name, foto(e)) for e in EJEMPLOS["fotos"]]

    # 1. Carga contra el contenedor principal
    print(f"Carga contra {args.url} (contenedor {args.contenedor})")
    rafaga(args.url, fotos, 1, 8)                              # calentamiento
    reposo = a_mb(docker("stats", "--no-stream", "--format", "{{.MemUsage}}", args.contenedor).stdout.split("/")[0])
    carga = []
    for c in CONCURRENCIAS:
        mem = Memoria(args.contenedor)
        mem.start()
        r = rafaga(args.url, fotos, c, max(40, 10 * c))
        r["memoria_max_mb"] = mem.detener()
        carga.append(r)
        print(f"  {c:2} a la vez: p50 {r['latencia_p50_ms']:7.1f} ms  p95 {r['latencia_p95_ms']:7.1f} ms  "
              f"{r['peticiones_por_segundo']:5.1f} pet/s  errores {r['errores']}  memoria máx. {r['memoria_max_mb']} MB")

    # 2. Planes chicos de nube simulados con límites de Docker
    planes = []
    if not args.sin_limites:
        print("Planes simulados con --memory y --cpus")
        for nombre_plan, memoria, cpus, puerto, extra in PLANES:
            nombre = f"plan-{puerto}"
            docker("rm", "-f", nombre)
            entorno = [a for v in extra for a in ("-e", v)]
            docker("run", "-d", "--name", nombre, "--memory", memoria, "--memory-swap", memoria, "--cpus", cpus,
                   *entorno, "-p", f"{puerto}:8000", args.imagen)
            mem = Memoria(nombre)
            mem.start()
            segundos, estado = esperar_salud(nombre, limite=300)
            plan = {"plan": nombre_plan, "memoria": memoria, "cpus": cpus, "variables": extra, "arranque_s": segundos,
                    "estado": estado}
            if estado == "healthy":
                url = f"http://localhost:{puerto}"
                primera = rafaga(url, fotos, 1, 1)
                if primera["latencia_p50_ms"] > LENTO_MS:
                    plan["una_a_la_vez"] = rafaga(url, fotos, 1, 3)
                    plan["nota"] = "demasiado lento: solo 3 peticiones en serie, sin prueba en paralelo"
                else:
                    rafaga(url, fotos, 1, 3)
                    plan["una_a_la_vez"] = rafaga(url, fotos, 1, 16)
                    plan["cuatro_a_la_vez"] = rafaga(url, fotos, 4, 32)
                fin = docker("inspect", "-f", "{{.State.OOMKilled}} {{.State.Status}} {{.State.Health.Status}}",
                             nombre).stdout.split()
                plan["estado_final"] = "se detuvo por falta de memoria" if fin[0] == "true" else f"{fin[1]}, {fin[2]}"
            plan["memoria_max_mb"] = mem.detener()
            docker("rm", "-f", nombre)
            planes.append(plan)
            detalle = (f"p50 {plan['una_a_la_vez']['latencia_p50_ms']} ms (1 a la vez), "
                       f"{plan.get('cuatro_a_la_vez', {}).get('latencia_p50_ms', '—')} ms (4 a la vez), "
                       f"al final {plan['estado_final']}" if "una_a_la_vez" in plan else "")
            print(f"  {nombre_plan:24} {estado} en {segundos} s  memoria máx. {plan['memoria_max_mb']} MB  {detalle}",
                  flush=True)

    (args.salida / "resultados.json").write_text(json.dumps(
        {"memoria_en_reposo_mb": round(reposo, 1), "carga": carga, "planes": planes}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")

    fig, eje = plt.subplots(figsize=(7, 4))
    x = [r["concurrencia"] for r in carga]
    eje.plot(x, [r["latencia_p50_ms"] for r in carga], "o-", label="Mediana (p50)")
    eje.plot(x, [r["latencia_p95_ms"] for r in carga], "s--", label="p95")
    eje.set_xscale("log", base=2)
    eje.set_xticks(x, x)
    eje.set_xlabel("Peticiones al mismo tiempo")
    eje.set_ylabel("Latencia por petición (ms)")
    eje.set_title("Latencia del contenedor según la carga")
    eje.grid(alpha=0.3)
    eje.legend()
    fig.tight_layout()
    fig.savefig(args.salida / "latencia.png", dpi=150)
    plt.close(fig)

    md = ["# Carga y recursos", "", f"- Contenedor `{args.contenedor}` en {args.url}",
          f"- Memoria en reposo: {reposo:.0f} MB", "", "## Peticiones al mismo tiempo", "",
          "| A la vez | Peticiones | Errores | Latencia p50 (ms) | p95 (ms) | Máx. (ms) | Peticiones/s | Memoria máx. (MB) |",
          "|---|---|---|---|---|---|---|---|",
          *[f"| {r['concurrencia']} | {r['peticiones']} | {r['errores']} | {r['latencia_p50_ms']} | {r['latencia_p95_ms']} "
            f"| {r['latencia_max_ms']} | {r['peticiones_por_segundo']} | {r['memoria_max_mb']} |" for r in carga], ""]
    if planes:
        md += ["## Planes chicos simulados (docker run --memory --cpus)", "",
               "| Plan | Arranque | Estado | Latencia p50, 1 a la vez (ms) | p50, 4 a la vez (ms) | Pet/s con 4 "
               "| Memoria máx. (MB) | Estado al final | Nota |",
               "|---|---|---|---|---|---|---|---|---|",
               *[f"| {p['plan']} | {p['arranque_s']} s | {p['estado']} | {p.get('una_a_la_vez', {}).get('latencia_p50_ms', '—')} "
                 f"| {p.get('cuatro_a_la_vez', {}).get('latencia_p50_ms', '—')} "
                 f"| {p.get('cuatro_a_la_vez', {}).get('peticiones_por_segundo', '—')} | {p['memoria_max_mb']} "
                 f"| {p.get('estado_final', '—')} | {p.get('nota', '')} |" for p in planes], ""]
    (args.salida / "resumen.md").write_text("\n".join(md), encoding="utf-8")
    print(f"Evidencia en {args.salida}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
