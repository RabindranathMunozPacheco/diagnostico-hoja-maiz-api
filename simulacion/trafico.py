"""Simulador de usuarios: manda fotos de hojas a la API y registra lo que ve cada cliente.

Cada solicitud es una foto al azar de las 181 fotos de tráfico (de la prueba de la Actividad 5, que el modelo no
vio). Las llegadas siguen un proceso de Poisson con la tasa indicada (en promedio `tasa` solicitudes por segundo,
pero con la irregularidad del tráfico real). Con `proporcion_lluvia` se controla qué fracción llega en condición de
lluvia (más oscura y desenfocada): así se simula el data drift, y se puede cambiar mientras corre.

Por cada solicitud guarda: momento, foto, condición, código HTTP (o el error de conexión si el servicio no
responde), latencia vista por el cliente, diagnóstico, versión del modelo y trace_id. Esto es la experiencia del
usuario, que no siempre coincide con lo que mide el servidor: si el servicio está caído, el servidor no cuenta
nada, pero el usuario sí ve los errores.

Uso (línea base sin incidentes):
    python simulacion/trafico.py --tasa 2 --minutos 5 --salida evidencias/monitoreo/linea_base/trafico.csv
"""
import argparse
import csv
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import numpy as np

RAIZ = Path(__file__).resolve().parents[1]
FOTOS = RAIZ / "monitoreo" / "datos_locales" / "trafico"
CAMPOS = ["ts", "foto", "clase_real", "condicion", "codigo", "error", "latencia_s", "diagnostico", "estado",
          "modelo_version", "trace_id"]


class Trafico:
    def __init__(self, url="http://localhost:8000", tasa=2.0, semilla=42, salida=None, hilos=6):
        self.url, self.tasa, self.hilos = url, tasa, hilos
        self.proporcion_lluvia = 0.0
        self._azar = random.Random(semilla)
        self._llegadas = np.random.default_rng(semilla)
        with open(FOTOS / "clases.csv", encoding="utf-8") as f:
            self.fotos = [(fila["id"], fila["clase"]) for fila in csv.DictReader(f)]
        self.cache = {(i, c): (FOTOS / c / f"{i}.png").read_bytes() for i, _ in self.fotos for c in ("original", "lluvia")}
        self.filas, self._candado = [], threading.Lock()
        self._salida = Path(salida) if salida else None
        self._detener = threading.Event()
        self._hilo = None

    def _una(self, http, id_foto, clase, condicion):
        fila = {"ts": time.time(), "foto": id_foto, "clase_real": clase, "condicion": condicion, "codigo": "",
                "error": "", "latencia_s": "", "diagnostico": "", "estado": "", "modelo_version": "", "trace_id": ""}
        inicio = time.perf_counter()
        try:
            r = http.post("/api/diagnostico", files={"foto": (f"{id_foto}.png", self.cache[(id_foto, condicion)],
                                                             "image/png")})
            fila["codigo"] = r.status_code
            fila["trace_id"] = r.headers.get("x-trace-id", "")
            if r.status_code == 200:
                d = r.json()
                fila.update(diagnostico=d["diagnostico"], estado=d["estado"], modelo_version=d["modelo_version"])
        except httpx.HTTPError as e:
            fila["error"] = type(e).__name__
        fila["latencia_s"] = round(time.perf_counter() - inicio, 4)
        with self._candado:
            self.filas.append(fila)

    def _bucle(self):
        with httpx.Client(base_url=self.url, timeout=30) as http, ThreadPoolExecutor(self.hilos) as ex:
            siguiente = time.time()
            while not self._detener.is_set():
                siguiente += self._llegadas.exponential(1 / self.tasa)
                espera = siguiente - time.time()
                if espera > 0:
                    self._detener.wait(espera)
                id_foto, clase = self._azar.choice(self.fotos)
                condicion = "lluvia" if self._azar.random() < self.proporcion_lluvia else "original"
                ex.submit(self._una, http, id_foto, clase, condicion)

    def iniciar(self):
        self._hilo = threading.Thread(target=self._bucle, daemon=True)
        self._hilo.start()
        return self

    def detener(self):
        self._detener.set()
        if self._hilo:
            self._hilo.join(timeout=60)
        if self._salida:
            self.guardar(self._salida)

    def guardar(self, ruta):
        ruta = Path(ruta)
        ruta.parent.mkdir(parents=True, exist_ok=True)
        with self._candado, open(ruta, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=CAMPOS)
            w.writeheader()
            w.writerows(sorted(self.filas, key=lambda x: x["ts"]))

    def resumen(self, desde=0.0, hasta=float("inf")):
        with self._candado:
            filas = [f for f in self.filas if desde <= f["ts"] < hasta]
        if not filas:
            return {"solicitudes": 0}
        fallidas = [f for f in filas if f["error"] or (f["codigo"] and int(f["codigo"]) >= 500)]
        latencias = sorted(f["latencia_s"] for f in filas if f["codigo"] == 200)
        p = lambda q: latencias[min(int(q * len(latencias)), len(latencias) - 1)] if latencias else None  # noqa: E731
        return {"solicitudes": len(filas), "fallidas": len(fallidas),
                "proporcion_fallidas": round(len(fallidas) / len(filas), 4),
                "latencia_p50_s": p(0.5), "latencia_p95_s": p(0.95), "latencia_p99_s": p(0.99),
                "lentas_mas_de_0_5_s": round(sum(x > 0.5 for x in latencias) / max(len(latencias), 1), 4)}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--tasa", type=float, default=2.0)
    parser.add_argument("--minutos", type=float, default=5)
    parser.add_argument("--lluvia", type=float, default=0.0, help="proporción de fotos en condición de lluvia")
    parser.add_argument("--salida", type=Path)
    args = parser.parse_args()
    t = Trafico(args.url, args.tasa, salida=args.salida)
    t.proporcion_lluvia = args.lluvia
    t.iniciar()
    inicio = time.time()
    try:
        while time.time() - inicio < args.minutos * 60:
            time.sleep(15)
            print(f"{(time.time() - inicio) / 60:4.1f} min  {t.resumen(time.time() - 60)}", flush=True)
    finally:
        t.detener()
    print("total:", t.resumen())


if __name__ == "__main__":
    main()
