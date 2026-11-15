"""Valida el modelo completo a través de la API con las 221 fotos de prueba de la Actividad 5.

1. Manda cada foto tal cual y compara las métricas con las registradas en la ficha del modelo: si la API
   prepara las fotos igual que el entrenamiento, deben salir iguales.
2. Repite la prueba con las mismas fotos alteradas (oscura, sobreexpuesta, borrosa, comprimida, girada,
   volteada, en gris, recortada y con ruido) para medir qué tanto se degrada el diagnóstico cuando la foto
   no llega en las condiciones del entrenamiento.

Las variantes se mandan en PNG (sin pérdida) para que el único cambio sea la alteración; la de compresión
se manda en JPEG de calidad 10. Deja en evidencias/validacion/:
    predicciones.csv      una fila por foto y variante
    metricas.json         métricas por variante
    resumen.md            tablas para el reporte
    matriz_confusion.png  matrices de las fotos originales (4 clases y enferma/sana con el umbral)

Uso:
    python pruebas/validar_modelo.py                    # API en http://localhost:8000
    python pruebas/validar_modelo.py --sin-variantes    # solo las fotos originales
"""
import argparse
import csv
import io
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import matplotlib
import numpy as np
from PIL import Image, ImageEnhance, ImageFilter, ImageOps
from sklearn.metrics import accuracy_score, confusion_matrix, f1_score

from probar_api import RAIZ, foto

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

PRUEBA = json.loads((Path(__file__).parent / "prueba_act5.json").read_text(encoding="utf-8"))
_REGISTRO = json.loads((RAIZ / "modelo" / "registro.json").read_text(encoding="utf-8"))
_CARPETA = _REGISTRO["versiones"][_REGISTRO["alias"]["champion"]]["carpeta"]
FICHA = json.loads((RAIZ / "modelo" / _CARPETA / "ficha_modelo.json").read_text(encoding="utf-8"))
CLASES = FICHA["clases"]


def png(im):
    b = io.BytesIO()
    im.save(b, "PNG")
    return b.getvalue()


def jpeg(im, calidad):
    b = io.BytesIO()
    im.save(b, "JPEG", quality=calidad)
    return b.getvalue()


def ruido(im, sigma=25, semilla=42):
    a = np.asarray(im, dtype=np.float32)
    a += np.random.default_rng(semilla).normal(0, sigma, a.shape)
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))


def recorte_centro(im, fraccion=0.6):
    w, h = im.size
    dw, dh = int(w * (1 - fraccion) / 2), int(h * (1 - fraccion) / 2)
    return im.crop((dw, dh, w - dw, h - dh))


# nombre: (descripción para el reporte, función que recibe la foto RGB y regresa los bytes que se mandan)
VARIANTES = {
    "original": ("La foto tal cual, sin cambios", None),
    "oscura": ("Brillo al 40 %", lambda im: png(ImageEnhance.Brightness(im).enhance(0.4))),
    "sobreexpuesta": ("Brillo al 180 %", lambda im: png(ImageEnhance.Brightness(im).enhance(1.8))),
    "borrosa": ("Desenfoque gaussiano de radio 3", lambda im: png(im.filter(ImageFilter.GaussianBlur(3)))),
    "comprimida": ("JPEG con calidad 10", lambda im: jpeg(im, 10)),
    "girada_90": ("Girada 90 grados", lambda im: png(im.rotate(90, expand=True))),
    "volteada": ("Espejo horizontal", lambda im: png(ImageOps.mirror(im))),
    "gris": ("Escala de grises", lambda im: png(im.convert("L"))),
    "recortada": ("Recorte del 60 % central (acercamiento)", lambda im: png(recorte_centro(im))),
    "ruido": ("Ruido gaussiano (sigma 25)", lambda im: png(ruido(im))),
}


def metricas(filas):
    real = np.array([f["clase_real"] for f in filas])
    mas_probable = np.array([f["clase_mas_probable"] for f in filas])
    estado = np.array([f["estado"] for f in filas])
    enferma = real != "Sana"
    return {
        "n_fotos": len(filas),
        "f1_macro": f1_score(real, mas_probable, average="macro", labels=CLASES),
        "exactitud": accuracy_score(real, mas_probable),
        "f1_por_clase": dict(zip(CLASES, f1_score(real, mas_probable, average=None, labels=CLASES).round(4).tolist())),
        "enferma_como_sana": float(np.mean(mas_probable[enferma] == "Sana")),
        "con_umbral_enferma_como_sana": float(np.mean(estado[enferma] == "sana")),
        "con_umbral_sana_como_enferma": float(np.mean(estado[~enferma] == "enferma")),
        "tiempo_ms_mediana": float(np.median([f["tiempo_ms"] for f in filas])),
    }


def graficar(filas, ruta):
    real = [f["clase_real"] for f in filas]
    pred = [f["clase_mas_probable"] for f in filas]
    m4 = confusion_matrix(real, pred, labels=CLASES)
    enf_real = ["Enferma" if r != "Sana" else "Sana" for r in real]
    enf_pred = ["Enferma" if f["estado"] == "enferma" else "Sana" for f in filas]
    m2 = confusion_matrix(enf_real, enf_pred, labels=["Enferma", "Sana"])
    fig, ejes = plt.subplots(1, 2, figsize=(11, 4.6), gridspec_kw={"width_ratios": [1.45, 1]})
    for eje, m, etiquetas, titulo in ((ejes[0], m4, CLASES, "Cuatro clases (clase más probable)"),
                                      (ejes[1], m2, ["Enferma", "Sana"], f"Enferma / sana con umbral {FICHA['umbral_enferma']['valor']}")):
        eje.imshow(m, cmap="Greens")
        eje.set_xticks(range(len(etiquetas)), [n.replace(" ", "\n") for n in etiquetas])
        eje.set_yticks(range(len(etiquetas)), etiquetas)
        eje.set_xlabel("Diagnóstico de la API")
        eje.set_ylabel("Clase real")
        eje.set_title(titulo, fontsize=11)
        for i in range(len(etiquetas)):
            for j in range(len(etiquetas)):
                eje.text(j, i, m[i, j], ha="center", va="center", fontsize=12,
                         color="white" if m[i, j] > m.max() / 2 else "black")
    fig.suptitle("221 fotos de prueba enviadas al contenedor", fontsize=12)
    fig.tight_layout()
    fig.savefig(ruta, dpi=150)
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--salida", type=Path, default=RAIZ / "evidencias" / "validacion")
    parser.add_argument("--sin-variantes", action="store_true")
    args = parser.parse_args()
    args.salida.mkdir(parents=True, exist_ok=True)
    variantes = {"original": VARIANTES["original"]} if args.sin_variantes else VARIANTES

    print(f"Descargando {len(PRUEBA['fotos'])} fotos de prueba (se comprueba el sha256 de cada una)…")
    with ThreadPoolExecutor(8) as ex:
        originales = dict(zip([f["id"] for f in PRUEBA["fotos"]], ex.map(foto, PRUEBA["fotos"])))

    http = httpx.Client(base_url=args.url, timeout=120)

    def diagnosticar(tarea):
        f, variante, contenido = tarea
        r = http.post("/api/diagnostico", files={"foto": (f"{f['id']}.img", contenido, "application/octet-stream")})
        r.raise_for_status()
        d = r.json()
        return {"id": f["id"], "sesion": f["sesion"], "variante": variante, "clase_real": f["clase"],
                "diagnostico": d["diagnostico"], "estado": d["estado"], "clase_mas_probable": d["clase_mas_probable"],
                "probabilidad_enferma": d["probabilidad_enferma"], "decidido_por_umbral": d["decidido_por_umbral"],
                **{f"p_{c}": d["probabilidades"][c] for c in CLASES}, "tiempo_ms": d["tiempo_ms"]}

    filas = []
    for variante, (descripcion, transformar) in variantes.items():
        tareas = []
        for f in PRUEBA["fotos"]:
            contenido = originales[f["id"]]
            if transformar:
                contenido = transformar(Image.open(io.BytesIO(contenido)).convert("RGB"))
            tareas.append((f, variante, contenido))
        with ThreadPoolExecutor(4) as ex:
            resultado = list(ex.map(diagnosticar, tareas))
        filas += resultado
        m = metricas(resultado)
        print(f"  {variante:14} F1 macro {m['f1_macro']:.4f}  exactitud {m['exactitud']:.3f}  "
              f"enfermas como sanas (umbral) {m['con_umbral_enferma_como_sana']:.1%}")

    with open(args.salida / "predicciones.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(filas[0]))
        w.writeheader()
        w.writerows(filas)

    por_variante = {v: [f for f in filas if f["variante"] == v] for v in variantes}
    base = {f["id"]: f for f in por_variante["original"]}
    resultados = {}
    for v, fs in por_variante.items():
        m = metricas(fs)
        m["mismo_diagnostico_que_original"] = float(np.mean([f["diagnostico"] == base[f["id"]]["diagnostico"] for f in fs]))
        m["cambio_medio_p_enferma"] = float(np.mean([abs(f["probabilidad_enferma"] - base[f["id"]]["probabilidad_enferma"])
                                                     for f in fs]))
        resultados[v] = {k: (round(x, 4) if isinstance(x, float) else x) for k, x in m.items()}

    registradas = FICHA["metricas_prueba"]
    comparacion = {k: (registradas[k], resultados["original"][k]) for k in
                   ("n_fotos", "f1_macro", "exactitud", "enferma_como_sana", "con_umbral_enferma_como_sana",
                    "con_umbral_sana_como_enferma")}
    iguales = all(abs(a - b) < 1e-4 for a, b in comparacion.values())
    (args.salida / "metricas.json").write_text(json.dumps(
        {"registradas_vs_api": comparacion, "iguales_a_las_registradas": iguales, "por_variante": resultados},
        ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    graficar(por_variante["original"], args.salida / "matriz_confusion.png")

    pct = lambda x: f"{x * 100:.1f} %"  # noqa: E731
    md = ["# Validación del modelo a través de la API", "",
          f"- API: {args.url} · {len(PRUEBA['fotos'])} fotos de prueba de la Actividad 5 · umbral {FICHA['umbral_enferma']['valor']}",
          f"- ¿Las métricas de la API son iguales a las registradas? **{'sí' if iguales else 'no'}**", "",
          "## Fotos originales contra lo registrado", "", "| Métrica | Registrada (Act. 5 y 6) | API |", "|---|---|---|",
          *[f"| {k} | {a} | {b} |" for k, (a, b) in comparacion.items()], "",
          "## Fotos alteradas", "",
          "| Variante | Qué se cambió | F1 macro | Exactitud | Enfermas como sanas (umbral) | Sanas como enfermas (umbral) "
          "| Mismo diagnóstico que la original | Cambio medio de P(enferma) |", "|---|---|---|---|---|---|---|---|",
          *[f"| {v} | {VARIANTES[v][0]} | {r['f1_macro']:.2f} | {r['exactitud']:.2f} | {pct(r['con_umbral_enferma_como_sana'])} "
            f"| {pct(r['con_umbral_sana_como_enferma'])} | {pct(r['mismo_diagnostico_que_original'])} "
            f"| {r['cambio_medio_p_enferma']:.3f} |" for v, r in resultados.items()], "",
          "## F1 por clase", "", "| Variante | " + " | ".join(CLASES) + " |", "|---|" + "---|" * len(CLASES),
          *[f"| {v} | " + " | ".join(f"{r['f1_por_clase'][c]:.2f}" for c in CLASES) + " |" for v, r in resultados.items()], ""]
    (args.salida / "resumen.md").write_text("\n".join(md), encoding="utf-8")
    print(f"Métricas iguales a las registradas: {iguales}. Evidencia en {args.salida}")
    return 0 if iguales else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
