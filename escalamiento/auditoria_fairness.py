"""Experimento 5: auditoría de sesgos y equidad (fairness) del modelo, antes y después de optimizarlo.

En esta app los grupos afectados no son de género o edad: son productores que toman la foto en condiciones
distintas (con lluvia, con poca luz, con un teléfono de gama baja) o con dispositivos distintos. Si el modelo falla
más en un grupo, ese grupo recibe peores diagnósticos. Se evalúan tres modelos sobre las 221 fotos de prueba:
    v1        el de la Fase II (archivado)
    v3        el que está en producción (reentrenado con lluvia en la Actividad 8), extractor PyTorch
    v3 int8   el mismo v3 con el extractor ONNX int8 estático del rediseño (¿la optimización cambia la equidad?)

Grupos:
1. Condición de captura (las mismas 221 fotos transformadas, así que la prevalencia es igual en todos):
   normal; oscura (brillo al 40 %, como en la Fase II); borrosa (desenfoque gaussiano de radio 3, Fase II);
   lluvia (brillo 55 % y desenfoque 1.5, Actividad 8); gama baja (simulación de un teléfono barato: desenfoque de
   lente fija de radio 1, ruido de sensor con desviación 8 y compresión JPEG de calidad 35, semilla 42).
2. Dispositivo, sacado del nombre de archivo del dataset: cámara digital (DSC, CIMG), teléfono (IMG_aaaammdd,
   aaaammdd_hhmmss) y sin identificar (IMG_####). Son grupos naturales con prevalencias distintas.

Métricas por grupo ("positivo" = hoja enferma, con el umbral de cada versión):
    tasa_positivos     proporción que el modelo marca como enferma        (paridad demográfica)
    fnr                enfermas reportadas como sanas = 1 - TPR           (igualdad de oportunidades)
    fpr                sanas reportadas como enfermas                     (con fnr: equalized odds)
    ppv                de las marcadas enfermas, cuántas lo están         (predictive parity)
    f1_macro           F1 de la clase más probable (4 clases)
Con intervalo de Wilson al 95 % para fnr y fpr, porque los grupos son chicos.

Además, una prueba fuera de dominio: 5 imágenes que no son hojas (verde liso, café liso, gris, ruido y un tablero)
para ver qué responde la API completa y si avisa que la foto no parece una hoja.

Uso:
    python escalamiento/auditoria_fairness.py
Deja evidencias/escalamiento/fairness/resultados.json, resumen.md y fnr_por_grupo.png.
"""
import io
import json
import math
import re
import sys
import time
from pathlib import Path

import matplotlib
import numpy as np
import pandas as pd
import skops.io as sio
from PIL import Image, ImageEnhance, ImageFilter
from sklearn.metrics import f1_score

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "scripts"))
import datos  # noqa: E402
from app.extractor_onnx import ExtractorOnnx  # noqa: E402
from escalamiento import comun  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

SALIDA = comun.EVIDENCIAS / "fairness"
SEMILLA = 42
ONNX = RAIZ / "modelo" / "extractores" / "mobilenet_v2_int8_estatico.onnx"


def oscura(im):
    return ImageEnhance.Brightness(im).enhance(0.4)


def borrosa(im):
    return im.filter(ImageFilter.GaussianBlur(3))


def gama_baja(im, azar):
    im = im.filter(ImageFilter.GaussianBlur(1))
    x = np.asarray(im, dtype=np.float32) + azar.normal(0, 8, (*im.size[::-1], 3))
    im = Image.fromarray(np.clip(x, 0, 255).astype(np.uint8))
    b = io.BytesIO()
    im.save(b, "JPEG", quality=35)
    return Image.open(io.BytesIO(b.getvalue())).convert("RGB")


def dispositivo(archivo):
    if "DSC" in archivo or "CIMG" in archivo:
        return "cámara digital"
    if re.search(r"IMG_20\d{6}_", archivo) or re.match(r"^\d+_20\d{6}_\d{6}", archivo):
        return "teléfono"
    return "sin identificar"


def wilson(k, n, z=1.96):
    if n == 0:
        return None
    p = k / n
    centro = (p + z * z / (2 * n)) / (1 + z * z / n)
    margen = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return [round(centro - margen, 4), round(centro + margen, 4)]


def clasificador(version):
    carpeta = RAIZ / "modelo" / "versiones" / str(version)
    ficha = json.loads((carpeta / "ficha_modelo.json").read_text(encoding="utf-8"))
    return sio.load(carpeta / ficha["archivo"]), ficha["umbral_enferma"]["valor"]


def metricas_grupo(y, proba, clases, umbral):
    y = np.asarray(y)
    enferma_real = y != "Sana"
    marcada = (1 - proba[:, clases.index("Sana")]) >= umbral
    mas_probable = np.array(clases)[proba.argmax(1)]
    fn = int(np.sum(enferma_real & ~marcada))
    fp = int(np.sum(~enferma_real & marcada))
    tp = int(np.sum(enferma_real & marcada))
    n_enf, n_sana = int(enferma_real.sum()), int((~enferma_real).sum())
    return {"n": int(len(y)), "enfermas": n_enf, "sanas": n_sana, "prevalencia": round(n_enf / len(y), 4),
            "tasa_positivos": round(float(marcada.mean()), 4),
            "fnr": round(fn / n_enf, 4) if n_enf else None, "fnr_ic95": wilson(fn, n_enf),
            "fpr": round(fp / n_sana, 4) if n_sana else None, "fpr_ic95": wilson(fp, n_sana),
            "ppv": round(tp / (tp + fp), 4) if tp + fp else None,
            "f1_macro": round(float(f1_score(y, mas_probable, average="macro", labels=datos.CLASES,
                                             zero_division=0)), 4)}


def fuera_de_dominio():
    """La API completa (con el aviso de calidad) ante imágenes que no son hojas."""
    import os
    os.environ.setdefault("MOTOR_EXTRACTOR", "pytorch")
    from app.config import cargar_config, cargar_version
    from app.modelo import Diagnosticador
    config = cargar_config()
    version = cargar_version(config)
    d = Diagnosticador(version, config.umbral(version), config.alias)
    azar = np.random.default_rng(SEMILLA)
    tablero = np.indices((224, 224)).sum(0) // 28 % 2 * 255
    imagenes = {"verde liso": Image.new("RGB", (224, 224), (60, 140, 50)),
                "café liso (suelo)": Image.new("RGB", (224, 224), (110, 80, 50)),
                "gris liso": Image.new("RGB", (224, 224), (128, 128, 128)),
                "ruido": Image.fromarray(azar.integers(0, 256, (224, 224, 3), dtype=np.uint8)),
                "tablero": Image.fromarray(np.stack([tablero] * 3, -1).astype(np.uint8))}
    salida = {}
    for nombre, im in imagenes.items():
        r = d.diagnosticar(datos.png(im))
        salida[nombre] = {"diagnostico": r["diagnostico"], "probabilidad_enferma": r["probabilidad_enferma"],
                          "confianza": max(r["probabilidades"].values()), "advertencias": r["advertencias"]}
    return salida


def main():
    import torch
    from app.modelo import Extractor
    torch.set_num_threads(4)
    tabla = datos.tabla()
    prueba = tabla[tabla["parte"] == "prueba"].reset_index(drop=True)
    prueba["dispositivo"] = prueba["archivo"].map(dispositivo)
    print("Cargando y transformando las 221 fotos...")
    normales = [datos.imagen(f) for _, f in prueba.iterrows()]
    azar = np.random.default_rng(SEMILLA)
    condiciones = {"normal": normales, "oscura": [oscura(im) for im in normales],
                   "borrosa": [borrosa(im) for im in normales], "lluvia": [datos.lluvia(im) for im in normales],
                   "gama baja": [gama_baja(im, azar) for im in normales]}

    ficha = json.loads((RAIZ / "modelo" / "versiones" / "3" / "ficha_modelo.json").read_text(encoding="utf-8"))
    pytorch = Extractor(ficha["extractor"]["repositorio"], ficha["extractor"]["revision"])
    onnx = ExtractorOnnx(ONNX, hilos=4)

    def con_pytorch(imagenes):
        with torch.no_grad():
            return np.vstack([pytorch.modelo(**pytorch.procesador(imagenes[i:i + 32], return_tensors="pt"))
                              .pooler_output.numpy() for i in range(0, len(imagenes), 32)])

    def con_onnx(imagenes):
        return np.vstack([onnx.lote(imagenes[i:i + 32]) for i in range(0, len(imagenes), 32)])

    print("Calculando características...")
    car = {("pytorch", c): con_pytorch(ims) for c, ims in condiciones.items()}
    car.update({("onnx", c): con_onnx(ims) for c, ims in condiciones.items()})
    modelos = {"v1": (1, "pytorch"), "v3": (3, "pytorch"), "v3 int8": (3, "onnx")}
    clasificadores = {v: clasificador(v) for v in (1, 3)}

    resultados = {"fecha": time.strftime("%Y-%m-%d %H:%M:%S"), "n": len(prueba),
                  "grupos_dispositivo": prueba.groupby("dispositivo").size().to_dict(),
                  "condicion": {}, "dispositivo": {}}
    y = prueba["clase"].values
    for nombre, (version, motor) in modelos.items():
        modelo, umbral = clasificadores[version]
        clases = [str(c) for c in modelo.classes_]
        resultados["condicion"][nombre] = {
            c: metricas_grupo(y, modelo.predict_proba(car[(motor, c)].astype(np.float32)), clases, umbral)
            for c in condiciones}
        proba = modelo.predict_proba(car[(motor, "normal")].astype(np.float32))
        resultados["dispositivo"][nombre] = {
            g: metricas_grupo(y[idx], proba[idx], clases, umbral)
            for g, idx in prueba.groupby("dispositivo").indices.items()}

    for eje in ("condicion", "dispositivo"):
        for nombre, grupos in resultados[eje].items():
            fnr = {g: m["fnr"] for g, m in grupos.items()}
            resultados[eje][nombre]["_brecha_fnr"] = round(max(fnr.values()) - min(fnr.values()), 4)
    print("Prueba fuera de dominio...")
    resultados["fuera_de_dominio"] = fuera_de_dominio()

    comun.guardar_json(SALIDA / "resultados.json", resultados)
    escribir_resumen(resultados)
    grafica(resultados)


def escribir_resumen(r):
    lineas = []
    for eje, titulo in (("condicion", "Condición de captura"), ("dispositivo", "Dispositivo")):
        lineas += [f"## {titulo}", "", "| Modelo | Grupo | n | Prevalencia | Marcadas enfermas | FNR (IC 95 %) | "
                   "FPR | PPV | F1 macro |", "|" + "---|" * 9]
        for modelo, grupos in r[eje].items():
            for g, m in grupos.items():
                if g.startswith("_"):
                    continue
                ic = m["fnr_ic95"]
                lineas.append(f"| {modelo} | {g} | {m['n']} | {m['prevalencia']} | {m['tasa_positivos']} | "
                              f"{m['fnr']} ({ic[0]}-{ic[1]}) | {m['fpr']} | {m['ppv']} | {m['f1_macro']} |")
            lineas.append(f"| {modelo} | **brecha de FNR** | | | | {grupos['_brecha_fnr']} | | | |")
        lineas.append("")
    lineas += ["## Fuera de dominio (v3, API completa)", "", "| Imagen | Diagnóstico | P(enferma) | Confianza | "
               "Advertencias |", "|---|---|---|---|---|"]
    for nombre, f in r["fuera_de_dominio"].items():
        lineas.append(f"| {nombre} | {f['diagnostico']} | {f['probabilidad_enferma']} | {round(f['confianza'], 3)} "
                      f"| {'; '.join(f['advertencias']) or '(ninguna)'} |")
    (SALIDA / "resumen.md").write_text("\n".join(lineas) + "\n", encoding="utf-8")
    print("\n".join(lineas))


def grafica(r):
    fig, ejes = plt.subplots(1, 2, figsize=(11, 4), gridspec_kw={"width_ratios": [5, 3]})
    colores = {"v1": "#95a5a6", "v3": "#2980b9", "v3 int8": "#27ae60"}
    for ax, eje in zip(ejes, ("condicion", "dispositivo")):
        grupos = [g for g in r[eje]["v3"] if not g.startswith("_")]
        x = np.arange(len(grupos))
        for k, (modelo, color) in enumerate(colores.items()):
            fnr = [r[eje][modelo][g]["fnr"] for g in grupos]
            ic = np.array([r[eje][modelo][g]["fnr_ic95"] for g in grupos])
            ax.bar(x + (k - 1) * 0.27, fnr, 0.27, color=color, label=modelo,
                   yerr=[np.array(fnr) - ic[:, 0], ic[:, 1] - np.array(fnr)], capsize=2, error_kw={"lw": 0.8})
        ax.axhline(0.05, color="black", ls="--", lw=1)
        ax.text(len(grupos) - 0.5, 0.055, "meta 5 %", fontsize=8, ha="right")
        ax.set_xticks(x, grupos, fontsize=9)
        ax.set_title("Condición de captura" if eje == "condicion" else "Dispositivo")
        ax.set_ylabel("Enfermas reportadas como sanas (FNR)")
        ax.grid(axis="y", alpha=0.3)
    ejes[0].legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(SALIDA / "fnr_por_grupo.png", dpi=150)


if __name__ == "__main__":
    main()
