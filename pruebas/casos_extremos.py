"""Lleva la API al límite con casos extremos y deja una tabla con lo esperado contra lo obtenido.

Grupos de casos:
    A. Archivos que no son una foto válida: la API debe rechazarlos con un código claro, sin caerse.
    B. Fotos válidas en condiciones raras (otros formatos, transparencia, orientación EXIF, tamaños extremos).
    C. Imágenes que no son hojas de maíz (fuera de dominio).
    D. Consistencia: la misma foto muchas veces y varias fotos al mismo tiempo.

Las imágenes sintéticas de los grupos A y C se guardan en evidencias/casos_extremos/entradas/; las que salen de
las fotos del dataset no se guardan (el dataset no declara licencia). Deja también resultados.csv y resumen.md.

Uso:
    python pruebas/casos_extremos.py                    # API en http://localhost:8000
"""
import argparse
import csv
import io
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import numpy as np
from PIL import Image

from probar_api import EJEMPLOS, RAIZ, foto

FOTOS = {e["id"]: e for e in EJEMPLOS["fotos"]}


def codificar(im, formato, **opciones):
    b = io.BytesIO()
    im.save(b, formato, **opciones)
    return b.getvalue()


def con_exif_girada(im):
    """Píxeles girados 90° con la etiqueta EXIF que le dice al visor que los enderece (como guarda un celular)."""
    girada = im.rotate(-90, expand=True)          # así quedan los píxeles si el celular estaba de lado
    exif = Image.Exif()
    exif[0x0112] = 8                              # Orientation = 8: el visor la gira 90° para mostrarla derecha
    return codificar(girada, "JPEG", quality=95, exif=exif.tobytes())


class Prueba:
    def __init__(self, url, salida):
        self.http = httpx.Client(base_url=url, timeout=120)
        self.salida = salida
        self.filas = []
        (salida / "entradas").mkdir(parents=True, exist_ok=True)

    def mandar(self, nombre, contenido, campo="foto"):
        return self.http.post("/api/diagnostico", files={campo: (nombre, contenido, "application/octet-stream")})

    def guardar_entrada(self, nombre, im):
        im.save(self.salida / "entradas" / nombre)

    def anotar(self, caso, grupo, entrada, esperado, r, cumple, comparada_con=None):
        cuerpo = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        fila = {"caso": caso, "grupo": grupo, "entrada": entrada, "esperado": esperado, "codigo_http": r.status_code,
                "diagnostico": cuerpo.get("diagnostico", ""), "detalle": cuerpo.get("detail", "") if isinstance(
                    cuerpo.get("detail"), str) else (json.dumps(cuerpo.get("detail"), ensure_ascii=False)[:120] if cuerpo.get("detail") else ""),
                "probabilidad_enferma": cuerpo.get("probabilidad_enferma", ""),
                "clase_mas_probable": cuerpo.get("clase_mas_probable", ""),
                "probabilidad_maxima": max(cuerpo["probabilidades"].values()) if "probabilidades" in cuerpo else "",
                "imagen_recibida": (f"{cuerpo['imagen']['ancho']}x{cuerpo['imagen']['alto']} {cuerpo['imagen']['formato']} "
                                    f"{cuerpo['imagen']['modo']}") if "imagen" in cuerpo else "",
                "tiempo_ms": cuerpo.get("tiempo_ms", ""), "cambio_p_enferma_vs_original": "",
                "cumple": "sí" if cumple else "no"}
        if comparada_con is not None and "probabilidades" in cuerpo:
            fila["cambio_p_enferma_vs_original"] = round(abs(cuerpo["probabilidad_enferma"] - comparada_con["probabilidad_enferma"]), 4)
        self.filas.append(fila)
        resumen = fila["diagnostico"] or fila["detalle"]
        print(f"  [{'OK' if cumple else '--'}] {caso:4} {r.status_code}  {resumen[:60]}")
        return cuerpo


def iguales(a, b, tolerancia=1e-4):
    return a["diagnostico"] == b["diagnostico"] and all(
        abs(a["probabilidades"][c] - b["probabilidades"][c]) <= tolerancia for c in a["probabilidades"])


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--salida", type=Path, default=RAIZ / "evidencias" / "casos_extremos")
    args = parser.parse_args()
    p = Prueba(args.url, args.salida)

    roya_bytes = foto(FOTOS["m0005"])                       # Roya común, la API la acierta con P(enferma) 0.999
    roya = Image.open(io.BytesIO(roya_bytes)).convert("RGB")
    base = p.mandar("m0005.jpg", roya_bytes).json()
    print(f"Foto base m0005: {base['diagnostico']}, P(enferma) {base['probabilidad_enferma']}")

    # ---------------- A. Archivos que no son una foto válida ----------------
    g = "A. Archivos que no son una foto válida"
    r = p.mandar("hoja.jpg", "esto no es una foto".encode())
    p.anotar("A1", g, "Archivo de texto con extensión .jpg", "415, no es una imagen", r, r.status_code == 415)
    r = p.mandar("vacia.jpg", b"")
    p.anotar("A2", g, "Archivo vacío (0 bytes)", "415, no es una imagen", r, r.status_code == 415)
    r = p.mandar("cortada.jpg", roya_bytes[: len(roya_bytes) // 2])
    p.anotar("A3", g, "JPEG cortado a la mitad (descarga incompleta)", "415, imagen dañada", r, r.status_code == 415)
    pdf = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF"
    r = p.mandar("reporte.pdf", pdf)
    p.anotar("A4", g, "Documento PDF", "415, no es una imagen", r, r.status_code == 415)
    pesada = Image.fromarray(np.random.default_rng(0).integers(0, 256, (2000, 2000, 3), dtype=np.uint8))
    contenido = codificar(pesada, "PNG")
    r = p.mandar("pesada.png", contenido)
    p.anotar("A5", g, f"Imagen real de {len(contenido) / 2**20:.1f} MB (límite 10 MB)", "413, demasiado grande", r,
             r.status_code == 413)
    enorme = Image.new("RGB", (11000, 10000), (60, 140, 60))
    contenido = codificar(enorme, "PNG")
    r = p.mandar("110mp.png", contenido)
    p.anotar("A6", g, f"PNG de 110 megapíxeles ({len(contenido) / 2**20:.1f} MB comprimido)", "415, demasiados píxeles",
             r, r.status_code == 415)
    grande = Image.new("RGB", (8000, 8000), (60, 140, 60))
    contenido = codificar(grande, "PNG")
    r = p.mandar("64mp.png", contenido)
    p.anotar("A7", g, f"PNG de 64 megapíxeles ({len(contenido) / 2**20:.1f} MB comprimido)",
             "415, pasa el límite de 50 megapíxeles", r, r.status_code == 415)
    r = p.http.post("/api/diagnostico")
    p.anotar("A8", g, "Petición sin archivo", "422, falta el campo foto", r, r.status_code == 422)
    r = p.mandar("m0005.jpg", roya_bytes, campo="imagen")
    p.anotar("A9", g, "Foto en un campo con otro nombre (imagen)", "422, falta el campo foto", r, r.status_code == 422)
    r = p.http.get("/api/diagnostico")
    p.anotar("A10", g, "GET en lugar de POST", "405, método no permitido", r, r.status_code == 405)
    r = p.http.get("/api/no-existe")
    p.anotar("A11", g, "Ruta que no existe", "404", r, r.status_code == 404)

    # ---------------- B. Fotos válidas en condiciones raras ----------------
    g = "B. Fotos válidas en condiciones raras"
    for caso, nombre, contenido in (("B1", "La misma foto en PNG", codificar(roya, "PNG")),
                                    ("B2", "La misma foto en BMP", codificar(roya, "BMP")),
                                    ("B3", "La misma foto en WebP sin pérdida", codificar(roya, "WEBP", lossless=True))):
        r = p.mandar(nombre, contenido)
        p.anotar(caso, g, nombre, "200 y las mismas probabilidades que el JPG", r,
                 r.status_code == 200 and iguales(r.json(), base), base)
    r = p.mandar("gif.gif", codificar(roya, "GIF"))
    p.anotar("B4", g, "La misma foto en GIF (solo 256 colores)", "200, diagnóstico igual o muy parecido", r,
             r.status_code == 200 and r.json()["diagnostico"] == base["diagnostico"], base)
    r = p.mandar("cmyk.jpg", codificar(roya.convert("CMYK"), "JPEG", quality=95))
    p.anotar("B5", g, "La misma foto en JPEG CMYK (modo de imprenta)", "200, diagnóstico igual", r,
             r.status_code == 200 and r.json()["diagnostico"] == base["diagnostico"], base)
    rgba = roya.convert("RGBA")
    rgba.putalpha(128)
    r = p.mandar("transparente.png", codificar(rgba, "PNG"))
    p.anotar("B6", g, "PNG con transparencia (alfa al 50 %)", "200, la API ignora la transparencia", r,
             r.status_code == 200 and iguales(r.json(), base), base)
    r = p.mandar("celular.jpg", con_exif_girada(roya))
    p.anotar("B7", g, "Foto de celular guardada de lado con etiqueta EXIF de orientación",
             "200 y el mismo diagnóstico que la foto derecha", r,
             r.status_code == 200 and r.json()["diagnostico"] == base["diagnostico"], base)
    r = p.mandar("girada.png", codificar(roya.rotate(90, expand=True), "PNG"))
    p.anotar("B8", g, "La misma foto girada 90 grados (sin EXIF)", "200, diagnóstico igual", r,
             r.status_code == 200 and r.json()["diagnostico"] == base["diagnostico"], base)
    diminuta = roya.resize((16, 16), Image.LANCZOS)
    r = p.mandar("diminuta.png", codificar(diminuta, "PNG"))
    p.anotar("B9", g, "La foto reducida a 16×16 píxeles", "200; con tan poca información el diagnóstico no es confiable",
             r, r.status_code == 200, base)
    ampliada = roya.resize((4000, 4000), Image.LANCZOS)
    r = p.mandar("ampliada.jpg", codificar(ampliada, "JPEG", quality=90))
    p.anotar("B10", g, "La foto ampliada a 4000×4000 píxeles", "200, diagnóstico igual (la API la reduce a 224×224)",
             r, r.status_code == 200 and r.json()["diagnostico"] == base["diagnostico"], base)
    r = p.mandar("4x3.jpg", codificar(roya.crop((0, 28, 224, 196)), "JPEG", quality=95))
    p.anotar("B11", g, "Recorte con proporción 4:3 de celular (224×168)",
             "200; la API la estira a 224×224 igual que en el entrenamiento", r, r.status_code == 200, base)

    # ---------------- C. Imágenes que no son hojas de maíz ----------------
    g = "C. Imágenes que no son hojas de maíz"
    esperado = "200 con alguna de las 4 clases: el modelo no tiene la opción 'no es una hoja'"
    rng = np.random.default_rng(42)
    yy, xx = np.mgrid[0:224, 0:224]
    sinteticas = {
        "verde_liso.png": ("Imagen verde lisa", Image.new("RGB", (224, 224), (60, 140, 60))),
        "negra.png": ("Imagen negra", Image.new("RGB", (224, 224), (0, 0, 0))),
        "blanca.png": ("Imagen blanca", Image.new("RGB", (224, 224), (255, 255, 255))),
        "ruido.png": ("Ruido aleatorio", Image.fromarray(rng.integers(0, 256, (224, 224, 3), dtype=np.uint8))),
        "degradado.png": ("Degradado de colores", Image.fromarray(np.stack([xx, yy, 255 - xx], -1).astype(np.uint8))),
        "ajedrez.png": ("Tablero de ajedrez", Image.fromarray(((((xx // 28) + (yy // 28)) % 2) * 255).astype(np.uint8)).convert("RGB")),
    }
    for i, (archivo, (descripcion, im)) in enumerate(sinteticas.items(), start=1):
        p.guardar_entrada(archivo, im)
        r = p.mandar(archivo, codificar(im, "PNG"))
        p.anotar(f"C{i}", g, descripcion, esperado, r, r.status_code == 200)
    for j, (archivo, descripcion) in enumerate((("01_docs_endpoints.png", "Captura de pantalla de la documentación /docs"),
                                                ("03_web_m0005_roya.png", "Captura de pantalla de la propia página web")),
                                               start=len(sinteticas) + 1):
        r = p.mandar(archivo, (RAIZ / "evidencias" / "local" / "capturas" / archivo).read_bytes())
        p.anotar(f"C{j}", g, descripcion, esperado, r, r.status_code == 200)

    # ---------------- D. Consistencia ----------------
    g = "D. Consistencia"
    respuestas = [p.mandar("m0005.jpg", roya_bytes).json() for _ in range(20)]
    distintas = sum(not iguales(x, base, tolerancia=0) for x in respuestas)
    r = p.mandar("m0005.jpg", roya_bytes)
    p.anotar("D1", g, "La misma foto 20 veces seguidas", "20 respuestas idénticas", r, distintas == 0)
    p.filas[-1]["detalle"] = f"{20 - distintas} de 20 idénticas"
    secuencial = {i: p.mandar(f"{i}.jpg", foto(e)).json() for i, e in FOTOS.items()}
    tareas = [(i, foto(e)) for i, e in FOTOS.items()] * 4
    with ThreadPoolExecutor(8) as ex:
        paralelo = list(ex.map(lambda t: (t[0], p.mandar(f"{t[0]}.jpg", t[1]).json()), tareas))
    revueltas = sum(not iguales(d, secuencial[i], tolerancia=0) for i, d in paralelo)
    p.anotar("D2", g, f"Las 8 fotos de ejemplo, {len(tareas)} peticiones con 8 en paralelo",
             "Cada respuesta corresponde a su foto (no se mezclan)", r, revueltas == 0)
    p.filas[-1].update(diagnostico="", detalle=f"{len(tareas) - revueltas} de {len(tareas)} iguales a la respuesta en serie",
                       probabilidad_enferma="", clase_mas_probable="", probabilidad_maxima="", imagen_recibida="", tiempo_ms="")
    p.filas[-2].update(diagnostico=base["diagnostico"])

    with open(args.salida / "resultados.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(p.filas[0]))
        w.writeheader()
        w.writerows(p.filas)
    obtenido = lambda f: (f"{f['codigo_http']}, {f['diagnostico']}" + (f" (P(enferma) {f['probabilidad_enferma']:.3f}, "  # noqa: E731
                          f"máx. {f['probabilidad_maxima']:.2f})" if f["probabilidad_enferma"] != "" else "")
                          if f["diagnostico"] else f"{f['codigo_http']}, {f['detalle']}")
    md = ["# Casos extremos", "", f"- API: {args.url}",
          f"- Foto base para comparar: m0005 (Roya común), diagnóstico {base['diagnostico']}, P(enferma) {base['probabilidad_enferma']}",
          f"- Cumplen lo esperado: {sum(f['cumple'] == 'sí' for f in p.filas)} de {len(p.filas)}", "",
          "| Caso | Entrada | Esperado | Obtenido | Imagen recibida | Cambio de P(enferma) | ¿Cumple? |", "|---|---|---|---|---|---|---|"]
    grupo = None
    for f in p.filas:
        if f["grupo"] != grupo:
            grupo = f["grupo"]
            md.append(f"| **{grupo}** | | | | | | |")
        md.append(f"| {f['caso']} | {f['entrada']} | {f['esperado']} | {obtenido(f)} | {f['imagen_recibida']} "
                  f"| {f['cambio_p_enferma_vs_original']} | {f['cumple']} |")
    (args.salida / "resumen.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"{sum(f['cumple'] == 'sí' for f in p.filas)} de {len(p.filas)} casos cumplen. Evidencia en {args.salida}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
