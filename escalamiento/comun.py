"""Herramientas compartidas por los experimentos de escalabilidad y costos (Actividad 9).

- docker(): corre un comando de Docker y regresa su salida.
- Muestreador: hilo que toma `docker stats` cada pocos segundos (CPU en núcleos y memoria en MiB por contenedor).
- desechable(): levanta un contenedor de la API con límites de CPU y memoria en otro puerto, para medir sin tocar
  el laboratorio de monitoreo.
- carga(): prueba de carga en lazo cerrado (N clientes que mandan una foto en cuanto reciben la respuesta anterior).
- costo_por_mil(): costo de 1,000 diagnósticos en Cloud Run con los precios de precios_gcp.json.

Las fotos de carga son las 181 fotos de tráfico de la Actividad 8 (224x224 PNG, de la prueba de la Actividad 5) y,
para medir el costo de una foto tal como sale de un celular, una versión de 12 megapíxeles en JPEG.
"""
import io
import json
import random
import re
import shutil
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import numpy as np
from PIL import Image

RAIZ = Path(__file__).resolve().parents[1]
EVIDENCIAS = RAIZ / "evidencias" / "escalamiento"
FOTOS_TRAFICO = RAIZ / "monitoreo" / "datos_locales" / "trafico" / "original"
PRECIOS = json.loads((Path(__file__).with_name("precios_gcp.json")).read_text(encoding="utf-8"))
DOCKER = (shutil.which("docker")
          or r"C:\Users\rabin\AppData\Local\Programs\DockerDesktop\resources\bin\docker.exe")
SLO_LATENCIA_S = 0.5            # el SLO de la Actividad 8: 99 % de los diagnósticos en menos de 0.5 s


def docker(*args, revisar=False):
    r = subprocess.run([DOCKER, *map(str, args)], capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    if revisar and r.returncode != 0:
        raise RuntimeError(f"docker {' '.join(map(str, args))}: {r.stderr.strip()}")
    return r


def a_mib(texto):
    """'328.6MiB' -> 328.6; '1.2GiB' -> 1228.8; '0B' o '--' -> 0."""
    m = re.match(r"([\d.]+)\s*([KMG]?i?B)", texto.strip())
    if not m:
        return 0.0
    numero, unidad = m.groups()
    return float(numero) * {"B": 1 / 2**20, "KiB": 1 / 1024, "KB": 1 / 1000, "MiB": 1, "MB": 1, "GiB": 1024,
                            "GB": 1000}[unidad]


def stats(nombres=None):
    """{contenedor: (cpu_nucleos, memoria_mib)} de una sola lectura de docker stats."""
    r = docker("stats", "--no-stream", "--format", "{{.Name}}|{{.CPUPerc}}|{{.MemUsage}}", *(nombres or []))
    salida = {}
    for linea in r.stdout.strip().splitlines():
        nombre, cpu, mem = linea.split("|")
        salida[nombre] = (float(cpu.strip("%") or 0) / 100, a_mib(mem.split("/")[0]))
    return salida


class Muestreador(threading.Thread):
    """Toma docker stats cada `cada` segundos hasta que se detiene; guarda todas las lecturas."""

    def __init__(self, nombres=None, cada=5.0):
        super().__init__(daemon=True)
        self.nombres, self.cada, self.lecturas, self.alto = nombres, cada, [], threading.Event()

    def run(self):
        while not self.alto.is_set():
            inicio = time.time()
            try:
                self.lecturas.append((inicio, stats(self.nombres)))
            except Exception:                    # noqa: BLE001 - una lectura fallida no debe tumbar el experimento
                pass
            self.alto.wait(max(0.0, self.cada - (time.time() - inicio)))

    def detener(self):
        self.alto.set()
        self.join()
        return self.resumen()

    def resumen(self):
        """Por contenedor: CPU promedio y máxima (núcleos) y memoria promedio y máxima (MiB)."""
        por = {}
        for _, lectura in self.lecturas:
            for nombre, (cpu, mem) in lectura.items():
                por.setdefault(nombre, []).append((cpu, mem))
        return {n: {"lecturas": len(v),
                    "cpu_prom": round(float(np.mean([c for c, _ in v])), 3),
                    "cpu_max": round(float(np.max([c for c, _ in v])), 3),
                    "mem_prom_mib": round(float(np.mean([m for _, m in v])), 1),
                    "mem_max_mib": round(float(np.max([m for _, m in v])), 1)} for n, v in sorted(por.items())}


def esperar_salud(url, limite_s=180):
    """Segundos hasta que /api/salud responde 200 (None si no responde dentro del límite)."""
    inicio = time.perf_counter()
    while time.perf_counter() - inicio < limite_s:
        try:
            if httpx.get(f"{url}/api/salud", timeout=2).status_code == 200:
                return round(time.perf_counter() - inicio, 2)
        except httpx.HTTPError:
            pass
        time.sleep(0.25)
    return None


def desechable(nombre, imagen, puerto, cpus, memoria, entorno=None, red=None):
    """Arranca un contenedor de la API con límites; regresa (url, segundos hasta estar sano)."""
    docker("rm", "-f", nombre)
    args = ["run", "-d", "--name", nombre, "-p", f"{puerto}:8000", "--cpus", cpus, "--memory", memoria,
            "--memory-swap", memoria]
    for clave, valor in (entorno or {}).items():
        args += ["-e", f"{clave}={valor}"]
    if red:
        args += ["--network", red]
    docker(*args, imagen, revisar=True)
    url = f"http://localhost:{puerto}"
    return url, esperar_salud(url)


def quitar(nombre):
    docker("rm", "-f", nombre)


def fotos_trafico():
    """Las 181 fotos de tráfico como bytes PNG (224x224)."""
    return [p.read_bytes() for p in sorted(FOTOS_TRAFICO.glob("*.png"))]


def foto_celular(contenido, ancho=4000, alto=3000, calidad=90):
    """La misma hoja ampliada a 12 megapíxeles en JPEG, como llegaría de la cámara de un celular sin reducir."""
    with Image.open(io.BytesIO(contenido)) as im:
        salida = io.BytesIO()
        im.convert("RGB").resize((ancho, alto), Image.BICUBIC).save(salida, "JPEG", quality=calidad)
        return salida.getvalue()


def percentil(valores, p):
    return round(float(np.percentile(valores, p)), 4) if len(valores) else None


def carga(url, concurrencia, segundos, fotos, calentamiento_s=5.0, semilla=42, timeout_s=60.0, crudo=False):
    """Prueba en lazo cerrado. Ignora los primeros `calentamiento_s` segundos al resumir.

    Regresa n, errores, solicitudes por segundo y latencias (p50, p95, p99, promedio, % arriba del SLO). Con
    crudo=True agrega "registros": (segundo desde el inicio de la medición, latencia, código) de cada solicitud.
    """
    registros, candado = [], threading.Lock()
    fin = time.perf_counter() + calentamiento_s + segundos
    inicio_medicion = time.perf_counter() + calentamiento_s

    def cliente(i):
        azar = random.Random(semilla + i)
        with httpx.Client(base_url=url, timeout=timeout_s) as http:
            while time.perf_counter() < fin:
                contenido = azar.choice(fotos)
                t0 = time.perf_counter()
                try:
                    codigo = http.post("/api/diagnostico", files={"foto": ("hoja.png", contenido)}).status_code
                except httpx.HTTPError as e:
                    codigo = type(e).__name__
                t1 = time.perf_counter()
                with candado:
                    registros.append((t0, t1, codigo))

    with ThreadPoolExecutor(concurrencia) as ex:
        list(ex.map(cliente, range(concurrencia)))
    medidos = [(t0, t1, c) for t0, t1, c in registros if t0 >= inicio_medicion and t1 <= fin]
    ok = [t1 - t0 for t0, t1, c in medidos if c == 200]
    duracion = (max(t1 for _, t1, _ in medidos) - inicio_medicion) if medidos else segundos
    salida = {"concurrencia": concurrencia, "segundos": round(duracion, 1), "n": len(medidos),
              "errores": sum(1 for *_, c in medidos if c != 200),
              "rps": round(len(ok) / duracion, 2) if duracion else 0,
              "p50_s": percentil(ok, 50), "p95_s": percentil(ok, 95), "p99_s": percentil(ok, 99),
              "prom_s": round(float(np.mean(ok)), 4) if ok else None,
              "sobre_slo": round(float(np.mean(np.array(ok) > SLO_LATENCIA_S)), 4) if ok else None}
    if crudo:
        salida["registros"] = [(round(t0 - inicio_medicion, 3), round(t1 - t0, 4), c) for t0, t1, c in medidos]
    return salida


def costo_por_mil(vcpu, gib, rps, facturacion="solicitud"):
    """USD por 1,000 diagnósticos en Cloud Run con la instancia ocupada todo el tiempo a `rps` diagnósticos/s.

    Con la instancia siempre ocupada, cada segundo de instancia atiende `rps` diagnósticos, así que el costo de uno
    es (vCPU x precio + GiB x precio) / rps, más el cargo por solicitud. Es el costo marginal sin la capa gratuita.
    """
    cr = PRECIOS["cloud_run"]
    if facturacion == "solicitud":
        p = cr["facturacion_por_solicitud"]
        por_segundo = vcpu * p["cpu_vcpu_segundo_activo"] + gib * p["memoria_gib_segundo_activo"]
        por_solicitud = p["solicitudes_por_millon"] / 1e6
    else:
        p = cr["facturacion_por_instancia"]
        por_segundo = vcpu * p["cpu_vcpu_segundo"] + gib * p["memoria_gib_segundo"]
        por_solicitud = 0.0
    return round(1000 * (por_segundo / rps + por_solicitud), 5) if rps else None


def guardar_json(ruta, datos):
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8")
