"""Experimento 6: uso de recursos de la imagen actual y la del rediseño con la misma carga.

El dimensionamiento (dimensionar.py) lleva cada instancia a su máximo para saber su capacidad. Aquí se hace lo
contrario: se les manda la misma carga a las dos y se mide cuánto les cuesta atenderla. Se usa el simulador de
usuarios de la Actividad 8 (llegadas de Poisson, como el tráfico real), en contenedores desechables:

    actual     imagen PyTorch, 1 vCPU y 1 GiB (los límites del laboratorio)
    rediseño   imagen ONNX int8, 1 vCPU y 0.5 GiB (el tamaño elegido)

Para cada una: 30 s en reposo y 60 s con 2, 10 y 18 diagnósticos por segundo (el último cerca de la capacidad de la
imagen actual). Se guarda la CPU promedio y máxima (núcleos), la memoria, la latencia (p50, p95, p99), los errores y
los milisegundos de CPU por diagnóstico (CPU promedio entre diagnósticos por segundo).

Uso:
    python escalamiento/recursos.py
Deja evidencias/escalamiento/recursos/resultados.json y resumen.md.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from escalamiento import comun  # noqa: E402
from simulacion.trafico import Trafico  # noqa: E402

SALIDA = comun.EVIDENCIAS / "recursos"
CONFIGURACIONES = [
    ("actual", "diagnostico-hoja-maiz-api:0.6.0", "1", "1g"),
    ("rediseño", "diagnostico-hoja-maiz-api:0.6.0-onnx", "1", "512m"),
]
TASAS = (2, 10, 18)
SEGUNDOS = 60
PUERTO = 8601


def main():
    resultados = {"fecha": time.strftime("%Y-%m-%d %H:%M:%S"), "segundos_por_carga": SEGUNDOS, "configuraciones": []}
    for nombre, imagen, cpus, memoria in CONFIGURACIONES:
        url, arranque = comun.desechable("act9-rec", imagen, PUERTO, cpus, memoria)
        config = {"nombre": nombre, "imagen": imagen, "vcpu": float(cpus), "memoria": memoria, "arranque_s": arranque,
                  "cargas": []}
        m = comun.Muestreador(["act9-rec"], cada=2)
        m.start()
        time.sleep(30)
        config["reposo"] = m.detener().get("act9-rec")
        for tasa in TASAS:
            t = Trafico(url, tasa=tasa, semilla=42, hilos=12)
            t.iniciar()
            time.sleep(5)                                    # calentamiento
            desde = time.time()
            m = comun.Muestreador(["act9-rec"], cada=2)
            m.start()
            time.sleep(SEGUNDOS)
            uso = m.detener().get("act9-rec", {})
            hasta = time.time()
            t.detener()
            r = t.resumen(desde, hasta)
            lograda = r["solicitudes"] / (hasta - desde)
            carga = {"tasa_pedida": tasa, "tasa_lograda": round(lograda, 2), "solicitudes": r["solicitudes"],
                     "fallidas": r["fallidas"], "p50_s": r["latencia_p50_s"], "p95_s": r["latencia_p95_s"],
                     "p99_s": r["latencia_p99_s"], "sobre_slo": r["lentas_mas_de_0_5_s"],
                     "cpu_prom": uso.get("cpu_prom"), "cpu_max": uso.get("cpu_max"),
                     "mem_prom_mib": uso.get("mem_prom_mib"), "mem_max_mib": uso.get("mem_max_mib"),
                     "cpu_ms_por_diagnostico": round(1000 * uso.get("cpu_prom", 0) / lograda, 1) if lograda else None}
            config["cargas"].append(carga)
            print(f"{nombre:9s} {tasa:2d}/s -> {carga['tasa_lograda']}/s  CPU {carga['cpu_prom']} (máx {carga['cpu_max']})"
                  f"  mem {carga['mem_max_mib']} MiB  p95 {carga['p95_s']}  p99 {carga['p99_s']}  "
                  f"fallidas {carga['fallidas']}  {carga['cpu_ms_por_diagnostico']} ms CPU/diag", flush=True)
        comun.quitar("act9-rec")
        resultados["configuraciones"].append(config)

    comun.guardar_json(SALIDA / "resultados.json", resultados)
    lineas = ["| Configuración | Carga (diag./s) | CPU prom. (núcleos) | CPU máx. | Memoria máx. (MiB) | p50 (s) | p95 (s) | "
              "p99 (s) | Fallidas | ms de CPU por diagnóstico |", "|" + "---|" * 10]
    for c in resultados["configuraciones"]:
        lineas.append(f"| {c['nombre']} | reposo | {c['reposo']['cpu_prom']} | {c['reposo']['cpu_max']} | "
                      f"{c['reposo']['mem_max_mib']} | | | | | |")
        for k in c["cargas"]:
            lineas.append(f"| {c['nombre']} | {k['tasa_lograda']} | {k['cpu_prom']} | {k['cpu_max']} | {k['mem_max_mib']} | "
                          f"{k['p50_s']} | {k['p95_s']} | {k['p99_s']} | {k['fallidas']} | {k['cpu_ms_por_diagnostico']} |")
    (SALIDA / "resumen.md").write_text("\n".join(lineas) + "\n", encoding="utf-8")
    print("\n".join(lineas))


if __name__ == "__main__":
    main()
