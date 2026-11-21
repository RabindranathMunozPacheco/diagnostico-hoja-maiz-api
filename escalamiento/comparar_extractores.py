"""Experimento 2: ¿el extractor en ONNX (y cuantizado a int8) da lo mismo que el original de PyTorch?

Compara el MobileNetV2 de PyTorch (el de la API actual) contra sus versiones de modelo/extractores/ en las 221 fotos
de prueba, normales y en condición de lluvia:

1. Preprocesado: el propio (numpy) contra el procesador de Hugging Face, en 20 fotos (diferencia máxima).
2. Características: diferencia máxima y similitud coseno de los 1,280 valores contra PyTorch.
3. Diagnóstico: con los clasificadores v1 y v3 y el umbral de su ficha, qué proporción de fotos recibe el mismo
   diagnóstico que con PyTorch (acuerdo), y las métricas de siempre (F1 macro, enfermas como sanas, sanas como
   enfermas).
4. Costo: tamaño del archivo y latencia de una foto con un solo hilo (preprocesado + red, 300 repeticiones).

Uso:
    python escalamiento/exportar_onnx.py
    python escalamiento/comparar_extractores.py
Deja evidencias/escalamiento/extractor_onnx/resultados.json y resumen.md.
"""
import json
import sys
import time
from pathlib import Path

import numpy as np
import skops.io as sio
import torch

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "scripts"))
import datos  # noqa: E402
import evaluacion  # noqa: E402
from app.extractor_onnx import ExtractorOnnx, preparar  # noqa: E402
from app.modelo import Extractor  # noqa: E402
from escalamiento import comun  # noqa: E402

EXTRACTORES = RAIZ / "modelo" / "extractores"
SALIDA = comun.EVIDENCIAS / "extractor_onnx"
REPETICIONES = 300


def clasificador(version):
    carpeta = RAIZ / "modelo" / "versiones" / str(version)
    ficha = json.loads((carpeta / "ficha_modelo.json").read_text(encoding="utf-8"))
    return sio.load(carpeta / ficha["archivo"]), ficha["umbral_enferma"]["valor"]


def diagnosticos(modelo, umbral, caracteristicas):
    """El diagnóstico final, con la misma regla de la API (umbral sobre P(enferma) y la enfermedad más probable)."""
    clases = [str(c) for c in modelo.classes_]
    proba = modelo.predict_proba(caracteristicas.astype(np.float32))
    sana = clases.index("Sana")
    enferma = (1 - proba[:, sana]) >= umbral
    sin_sana = proba.copy()
    sin_sana[:, sana] = -1
    enfermedad = np.array(clases)[sin_sana.argmax(1)]
    return proba, clases, np.where(enferma, enfermedad, "Sana")


def latencia(funcion, imagen):
    for _ in range(20):
        funcion(imagen)
    tiempos = []
    for _ in range(REPETICIONES):
        t0 = time.perf_counter()
        funcion(imagen)
        tiempos.append(time.perf_counter() - t0)
    return {"p50_ms": round(1000 * float(np.percentile(tiempos, 50)), 2),
            "p95_ms": round(1000 * float(np.percentile(tiempos, 95)), 2)}


def main():
    torch.set_num_threads(1)
    ficha = json.loads((EXTRACTORES / "extractores.json").read_text(encoding="utf-8"))
    tabla = datos.tabla()
    prueba = tabla[tabla["parte"] == "prueba"].reset_index(drop=True)
    print("Cargando 221 fotos de prueba...")
    normales = [datos.imagen(f) for _, f in prueba.iterrows()]
    conjuntos = {"prueba": normales, "prueba_lluvia": [datos.lluvia(im) for im in normales]}
    y = prueba["clase"].values

    pytorch = Extractor(ficha["origen"]["repositorio"], ficha["origen"]["revision"])
    resultados = {"fecha": time.strftime("%Y-%m-%d %H:%M:%S"), "n_fotos": len(prueba), "extractores": {}}

    propio = preparar(normales[:20])
    original = pytorch.procesador(normales[:20], return_tensors="np")["pixel_values"]
    resultados["preprocesado_dif_max"] = float(np.abs(propio - original).max())
    print("Preprocesado, diferencia máxima:", resultados["preprocesado_dif_max"])

    def caracteristicas_pytorch(imagenes):
        with torch.no_grad():
            return np.vstack([pytorch.modelo(**pytorch.procesador(imagenes[i:i + 32], return_tensors="pt"))
                              .pooler_output.numpy() for i in range(0, len(imagenes), 32)])

    referencia = {n: caracteristicas_pytorch(ims) for n, ims in conjuntos.items()}
    clasificadores = {v: clasificador(v) for v in (1, 3)}
    base = {(v, n): diagnosticos(m, u, referencia[n]) for v, (m, u) in clasificadores.items() for n in conjuntos}

    motores = {"pytorch": (None, pytorch)}
    for nombre in ficha["archivos"]:
        motores[nombre] = (EXTRACTORES / nombre, ExtractorOnnx(EXTRACTORES / nombre, hilos=1))

    for motor, (ruta, ext) in motores.items():
        print("Evaluando", motor)
        r = {"archivo_mib": round(ruta.stat().st_size / 2**20, 2) if ruta else
             round(sum(p.numel() * p.element_size() for p in pytorch.modelo.parameters()) / 2**20, 2),
             "latencia_1_hilo": latencia(ext, normales[0]), "conjuntos": {}}
        for n, imagenes in conjuntos.items():
            c = referencia[n] if motor == "pytorch" else np.vstack([ext.lote(imagenes[i:i + 32])
                                                                    for i in range(0, len(imagenes), 32)])
            ref = referencia[n]
            coseno = (c * ref).sum(1) / (np.linalg.norm(c, axis=1) * np.linalg.norm(ref, axis=1))
            fila = {"dif_max": round(float(np.abs(c - ref).max()), 6),
                    "coseno_prom": round(float(coseno.mean()), 6), "coseno_min": round(float(coseno.min()), 6)}
            for v, (m, u) in clasificadores.items():
                proba, clases, diag = diagnosticos(m, u, c)
                fila[f"v{v}"] = {**evaluacion.metricas(y, proba, clases, u),
                                 "acuerdo_con_pytorch": round(float(np.mean(diag == base[(v, n)][2])), 4),
                                 "dif_max_p_enferma": round(float(np.abs(
                                     (1 - proba[:, clases.index("Sana")])
                                     - (1 - base[(v, n)][0][:, clases.index("Sana")])).max()), 4)}
            r["conjuntos"][n] = fila
        resultados["extractores"][motor] = r

    comun.guardar_json(SALIDA / "resultados.json", resultados)
    lineas = ["| Extractor | Archivo (MiB) | Latencia p50 (ms) | Coseno mín. | Acuerdo v3 | F1 v3 | Enf. como sana v3 |",
              "|---|---|---|---|---|---|---|"]
    for motor, r in resultados["extractores"].items():
        p = r["conjuntos"]["prueba"]
        lineas.append(f"| {motor} | {r['archivo_mib']} | {r['latencia_1_hilo']['p50_ms']} | {p['coseno_min']} | "
                      f"{p['v3']['acuerdo_con_pytorch']} | {p['v3']['f1_macro']} | {p['v3']['enferma_como_sana']} |")
    (SALIDA / "resumen.md").write_text("\n".join(lineas) + "\n", encoding="utf-8")
    print("\n".join(lineas))


if __name__ == "__main__":
    main()
