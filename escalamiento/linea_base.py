"""Experimento 1: línea base de la arquitectura actual (el laboratorio de la Actividad 8 tal como está).

La "arquitectura actual" de la Actividad 9 es ese laboratorio subido a la nube sin cambios (lift-and-shift): una
sola máquina virtual con Docker Compose que corre la API y todo el monitoreo. Este script mide lo que hace falta
para dimensionar esa máquina y para tener el punto de partida del rediseño:

1. Peso de la imagen de cada servicio.
2. CPU y memoria de cada contenedor en reposo (1 minuto) y con el tráfico de la línea base de la Actividad 8
   (2 diagnósticos por segundo con llegadas de Poisson, 3 minutos).
3. Arranque en frío de la API: un contenedor desechable con los mismos límites del laboratorio (1 CPU, 1 GB)
   hasta que /api/salud responde. Se repite 3 veces.
4. Tiempo de un diagnóstico en el servidor con la foto ya reducida (224x224 PNG) y con la foto completa de un
   celular (12 megapíxeles en JPEG), porque la página web hoy sube la foto sin reducirla.

Uso (con el laboratorio de monitoreo arriba):
    python escalamiento/linea_base.py
Deja evidencias/escalamiento/linea_base/resultados.json y trafico.csv.
"""
import sys
import time
from pathlib import Path

import httpx
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from escalamiento import comun  # noqa: E402
from simulacion.trafico import Trafico  # noqa: E402

SALIDA = comun.EVIDENCIAS / "linea_base"
API = "diagnostico-hoja-maiz"
IMAGEN_API = "diagnostico-hoja-maiz-api:0.4.0"


def contenedores_laboratorio():
    r = comun.docker("ps", "--filter", "label=com.docker.compose.project=monitoreo-maiz", "--format",
                     "{{.Names}}|{{.Image}}", revisar=True)
    return dict(linea.split("|") for linea in r.stdout.strip().splitlines())


def peso_imagenes(imagenes):
    salida = {}
    for imagen in sorted(set(imagenes)):
        r = comun.docker("image", "inspect", "--format", "{{.Size}}", imagen)
        salida[imagen] = round(int(r.stdout.strip()) / 2**20, 1) if r.returncode == 0 else None
    return salida


def tiempos_servidor(url, contenido, veces=20):
    """Latencia vista por el cliente y tiempo_ms que reporta la API, en serie (una solicitud a la vez)."""
    cliente, servidor = [], []
    with httpx.Client(base_url=url, timeout=120) as http:
        http.post("/api/diagnostico", files={"foto": ("hoja", contenido)})          # calentamiento
        for _ in range(veces):
            t0 = time.perf_counter()
            r = http.post("/api/diagnostico", files={"foto": ("hoja", contenido)})
            cliente.append(time.perf_counter() - t0)
            servidor.append(r.json()["tiempo_ms"] / 1000)
    return {"bytes": len(contenido), "veces": veces,
            "cliente_p50_s": comun.percentil(cliente, 50), "cliente_p95_s": comun.percentil(cliente, 95),
            "servidor_p50_s": comun.percentil(servidor, 50), "servidor_p95_s": comun.percentil(servidor, 95)}


def main():
    lab = contenedores_laboratorio()
    print("Contenedores:", ", ".join(lab))
    resultados = {"fecha": time.strftime("%Y-%m-%d %H:%M:%S"), "contenedores": lab,
                  "imagenes_mib": peso_imagenes(lab.values())}

    print("Reposo 60 s...")
    m = comun.Muestreador(list(lab), cada=5)
    m.start()
    time.sleep(60)
    resultados["reposo"] = m.detener()

    print("Tráfico de 2 diagnósticos/s por 3 min...")
    t = Trafico("http://localhost:8000", tasa=2.0, semilla=42)
    m = comun.Muestreador(list(lab), cada=5)
    m.start()
    t.iniciar()
    time.sleep(180)
    t.detener()
    resultados["con_trafico"] = m.detener()
    resultados["trafico"] = t.resumen()
    t.guardar(SALIDA / "trafico.csv")

    for momento in ("reposo", "con_trafico"):
        r = resultados[momento]
        resultados[f"total_{momento}"] = {
            "cpu_prom_nucleos": round(sum(v["cpu_prom"] for v in r.values()), 3),
            "cpu_max_suma_nucleos": round(sum(v["cpu_max"] for v in r.values()), 3),
            "mem_prom_mib": round(sum(v["mem_prom_mib"] for v in r.values()), 1),
            "mem_max_suma_mib": round(sum(v["mem_max_mib"] for v in r.values()), 1)}

    print("Arranque en frío (3 veces)...")
    arranques = []
    for i in range(3):
        _, segundos = comun.desechable("act9-frio", IMAGEN_API, 8301, "1", "1g")
        arranques.append(segundos)
        if i == 2:
            print("Tiempos de diagnóstico: foto reducida y foto de celular...")
            fotos = comun.fotos_trafico()
            resultados["diagnostico_en_serie"] = {
                "foto_224_png": tiempos_servidor("http://localhost:8301", fotos[0]),
                "foto_celular_12mp_jpeg": tiempos_servidor("http://localhost:8301", comun.foto_celular(fotos[0]))}
        comun.quitar("act9-frio")
    resultados["arranque_frio_s"] = {"intentos": arranques, "promedio": round(float(np.mean(arranques)), 2)}

    comun.guardar_json(SALIDA / "resultados.json", resultados)
    print("Listo:", SALIDA / "resultados.json")
    for clave in ("total_reposo", "total_con_trafico", "trafico", "arranque_frio_s", "diagnostico_en_serie"):
        print(clave, resultados[clave])


if __name__ == "__main__":
    main()
