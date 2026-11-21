"""Experimento 3: tamaño correcto de la instancia (right-sizing) y costo por cada 1,000 diagnósticos.

Prueba las dos imágenes de la versión 0.6.0 (la actual con PyTorch y la ligera con ONNX int8) con tamaños de
instancia válidos en Cloud Run y con distintas concurrencias (solicitudes que atiende a la vez una instancia):

    0.5 vCPU, 1 GiB     concurrencia 1 (Cloud Run obliga a 1 con menos de 1 vCPU)
    1 vCPU, 1 GiB       concurrencia 1, 2, 4 y 8 (el tamaño del laboratorio de la Actividad 8)
    1 vCPU, 512 MiB     concurrencia 1, 2 y 4 (solo ONNX: usa menos memoria)
    2 vCPU, 2 GiB       concurrencia 2, 4 y 8

Cada punto es una prueba de carga en lazo cerrado de 30 s (después de 5 s de calentamiento) con las 181 fotos de
tráfico ya reducidas a 224x224, como las mandaría la app si reduce la foto en el teléfono. Además se mide el
tamaño de 1 vCPU con fotos de celular de 12 megapíxeles, para ver cuánto cuesta no reducirlas.

Para cada punto se guarda: diagnósticos por segundo, latencias, proporción arriba del SLO (0.5 s), memoria máxima
y el costo por 1,000 diagnósticos en Cloud Run (facturación por solicitud, instancia siempre ocupada). Se cumple
el SLO si el p99 queda por debajo de 0.5 s (el SLO pide 99 % por debajo).

También mide el arranque en frío de cada imagen (3 veces) y que las dos respondan la misma versión del modelo.

Uso:
    python escalamiento/dimensionar.py
Deja evidencias/escalamiento/dimensionamiento/resultados.json, resumen.md y latencia_costo.png.
"""
import sys
import time
from pathlib import Path

import httpx
import matplotlib
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from escalamiento import comun  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

SALIDA = comun.EVIDENCIAS / "dimensionamiento"
IMAGENES = {"pytorch": "diagnostico-hoja-maiz-api:0.6.0", "onnx": "diagnostico-hoja-maiz-api:0.6.0-onnx"}
PLANES = [  # (vcpu, memoria docker, GiB para el costo, concurrencias, motores)
    (0.5, "1g", 1.0, (1,), ("pytorch", "onnx")),
    (1, "1g", 1.0, (1, 2, 4, 8), ("pytorch", "onnx")),
    (1, "512m", 0.5, (1, 2, 4), ("onnx",)),
    (2, "2g", 2.0, (2, 4, 8), ("pytorch", "onnx")),
]
SEGUNDOS, CALENTAMIENTO = 30, 5
PUERTO = 8401


def medir(nombre, url, concurrencia, fotos):
    m = comun.Muestreador([nombre], cada=2)
    m.start()
    r = comun.carga(url, concurrencia, SEGUNDOS, fotos, calentamiento_s=CALENTAMIENTO)
    r["mem_max_mib"] = m.detener().get(nombre, {}).get("mem_max_mib")
    return r


def main():
    fotos = comun.fotos_trafico()
    celular = [comun.foto_celular(f) for f in fotos[:20]]
    resultados = {"fecha": time.strftime("%Y-%m-%d %H:%M:%S"), "segundos_por_punto": SEGUNDOS,
                  "imagenes": IMAGENES, "arranque_frio_s": {}, "modelo": {}, "puntos": []}

    for motor, imagen in IMAGENES.items():
        intentos = []
        for _ in range(3):
            url, segundos = comun.desechable("act9-dim", imagen, PUERTO, "1", "1g")
            intentos.append(segundos)
        resultados["arranque_frio_s"][motor] = {"intentos": intentos, "promedio": round(float(np.mean(intentos)), 2)}
        info = httpx.get(f"{url}/api/modelo", timeout=10).json()
        resultados["modelo"][motor] = {k: info.get(k) for k in ("version_en_servicio", "umbral_en_uso",
                                                             "extractor_en_uso")}
        comun.quitar("act9-dim")
        print(motor, "arranque", resultados["arranque_frio_s"][motor], resultados["modelo"][motor])

    for vcpu, memoria, gib, concurrencias, motores in PLANES:
        for motor in motores:
            url, _ = comun.desechable("act9-dim", IMAGENES[motor], PUERTO, str(vcpu), memoria)
            casos = [(c, "224px", fotos) for c in concurrencias]
            if vcpu == 1 and memoria == "1g":
                casos.append((1, "celular_12mp", celular))
            for concurrencia, tipo_foto, lote in casos:
                r = medir("act9-dim", url, concurrencia, lote)
                r.update(motor=motor, vcpu=vcpu, memoria=memoria, gib=gib, foto=tipo_foto,
                         cumple_slo=bool(r["p99_s"] is not None and r["p99_s"] < comun.SLO_LATENCIA_S
                                         and r["errores"] == 0),
                         usd_por_mil=comun.costo_por_mil(vcpu, gib, r["rps"]))
                resultados["puntos"].append(r)
                print(f"{motor:8s} {vcpu} vCPU {memoria:5s} c={concurrencia} {tipo_foto:12s} "
                      f"{r['rps']:6.1f}/s p95 {r['p95_s']} p99 {r['p99_s']} err {r['errores']} "
                      f"mem {r['mem_max_mib']} USD/1000 {r['usd_por_mil']}", flush=True)
            comun.quitar("act9-dim")

    comun.guardar_json(SALIDA / "resultados.json", resultados)
    escribir_resumen(resultados)
    grafica(resultados)


def escribir_resumen(resultados):
    lineas = ["| Motor | vCPU | Memoria | Concurrencia | Foto | Diag./s | p50 (s) | p95 (s) | p99 (s) | Memoria máx. "
              "(MiB) | Cumple SLO | USD por 1,000 |", "|" + "---|" * 12]
    for r in resultados["puntos"]:
        lineas.append(f"| {r['motor']} | {r['vcpu']} | {r['memoria']} | {r['concurrencia']} | {r['foto']} | "
                      f"{r['rps']} | {r['p50_s']} | {r['p95_s']} | {r['p99_s']} | {r['mem_max_mib']} | "
                      f"{'sí' if r['cumple_slo'] else 'no'} | {r['usd_por_mil']} |")
    validos = [r for r in resultados["puntos"] if r["cumple_slo"] and r["foto"] == "224px"]
    for motor in IMAGENES:
        mejores = sorted((r for r in validos if r["motor"] == motor), key=lambda r: r["usd_por_mil"])
        if mejores:
            m = mejores[0]
            lineas.append(f"\nMás barato que cumple el SLO con {motor}: {m['vcpu']} vCPU, {m['memoria']}, "
                          f"concurrencia {m['concurrencia']} -> {m['usd_por_mil']} USD por 1,000 ({m['rps']}/s)")
    (SALIDA / "resumen.md").write_text("\n".join(lineas) + "\n", encoding="utf-8")
    print("\n".join(lineas[-2:]))


def grafica(resultados):
    fig, ax = plt.subplots(figsize=(8, 5))
    for motor, color in (("pytorch", "#c0392b"), ("onnx", "#27ae60")):
        puntos = [r for r in resultados["puntos"] if r["motor"] == motor and r["foto"] == "224px"]
        ax.scatter([r["p99_s"] for r in puntos], [r["usd_por_mil"] for r in puntos], color=color, label=motor)
        for r in puntos:
            ax.annotate(f"{r['vcpu']}/{r['memoria']}/c{r['concurrencia']}", (r["p99_s"], r["usd_por_mil"]),
                        fontsize=7, xytext=(3, 3), textcoords="offset points")
    ax.axvline(comun.SLO_LATENCIA_S, color="gray", ls="--", lw=1)
    ax.text(comun.SLO_LATENCIA_S, ax.get_ylim()[1] * 0.95, " SLO 0.5 s", color="gray", fontsize=8)
    ax.set_xscale("log")
    ax.set_xlabel("Latencia p99 (s, escala log)")
    ax.set_ylabel("USD por 1,000 diagnósticos (Cloud Run)")
    ax.legend()
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(SALIDA / "latencia_costo.png", dpi=150)


if __name__ == "__main__":
    main()
