"""Consume la API ya levantada (local o en el contenedor) y guarda las respuestas como evidencia.

Llama a cada endpoint como lo haría cualquier cliente: revisa la salud y la ficha del modelo, manda las fotos
de ejemplo (pruebas/fotos_ejemplo.json, de la prueba de la Actividad 5) y prueba dos errores esperados
(un archivo que no es imagen y uno demasiado grande). Deja en la carpeta de salida:
    peticiones.csv       una fila por petición: método, endpoint, código HTTP, tiempo y resultado
    respuestas/*.json    el cuerpo de cada respuesta
    resumen.md           tabla con los diagnósticos contra la clase real

Uso:
    python pruebas/probar_api.py                                   # http://localhost:8000
    python pruebas/probar_api.py --url http://localhost:8000 --salida evidencias/contenedor
"""
import argparse
import csv
import hashlib
import json
import sys
import time
from pathlib import Path

import httpx
from huggingface_hub import hf_hub_download

RAIZ = Path(__file__).resolve().parents[1]
EJEMPLOS = json.loads((Path(__file__).parent / "fotos_ejemplo.json").read_text(encoding="utf-8"))


def foto(ejemplo):
    """Descarga (o toma de la caché) la foto en la revisión fija y comprueba que es la misma por su sha256."""
    ruta = hf_hub_download(EJEMPLOS["repositorio"], ejemplo["ruta"], repo_type=EJEMPLOS["tipo"],
                           revision=EJEMPLOS["revision"])
    contenido = Path(ruta).read_bytes()
    if hashlib.sha256(contenido).hexdigest() != ejemplo["sha256"]:
        raise SystemExit(f"La foto {ejemplo['ruta']} no coincide con su sha256")
    return contenido


class Cliente:
    def __init__(self, url, salida):
        self.http = httpx.Client(base_url=url, timeout=60)
        self.salida = salida
        self.registro = []
        (salida / "respuestas").mkdir(parents=True, exist_ok=True)

    def pedir(self, nombre, metodo, endpoint, **kwargs):
        inicio = time.perf_counter()
        r = self.http.request(metodo, endpoint, **kwargs)
        ms = (time.perf_counter() - inicio) * 1000
        cuerpo = r.json()
        (self.salida / "respuestas" / f"{nombre}.json").write_text(
            json.dumps(cuerpo, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if "paths" in cuerpo:
            resumen = f"{len(cuerpo['paths'])} endpoints"
        elif "nombre" in cuerpo:
            resumen = f"{cuerpo['nombre']} v{cuerpo['version_registrada']}"
        else:
            resumen = cuerpo.get("diagnostico") or cuerpo.get("detail") or cuerpo.get("estado")
        self.registro.append({"peticion": nombre, "metodo": metodo, "endpoint": endpoint,
                              "codigo_http": r.status_code, "tiempo_ms": round(ms, 1), "resultado": resumen})
        print(f"  {metodo:4} {endpoint:18} {r.status_code}  {ms:7.1f} ms  {resumen}")
        return r.status_code, cuerpo


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--salida", type=Path, default=RAIZ / "evidencias" / "local")
    args = parser.parse_args()
    c = Cliente(args.url, args.salida)
    print(f"API en {args.url}")

    _, openapi = c.pedir("openapi", "GET", "/openapi.json")
    _, salud = c.pedir("salud", "GET", "/api/salud")
    _, ficha = c.pedir("modelo", "GET", "/api/modelo")

    filas = []
    for e in EJEMPLOS["fotos"]:
        nombre = Path(e["ruta"]).name
        codigo, d = c.pedir(f"diagnostico_{e['id']}", "POST", "/api/diagnostico",
                            files={"foto": (nombre, foto(e), "image/jpeg")})
        filas.append((e["id"], e["clase"], d["diagnostico"], d["probabilidad_enferma"], d["tiempo_ms"],
                      "sí" if d["decidido_por_umbral"] else "no",
                      "sí" if (d["diagnostico"] == "Sana") == (e["clase"] == "Sana") else "no",
                      "sí" if d["diagnostico"] == e["clase"] else "no"))

    # Errores esperados: el cliente debe recibir un código claro, no una caída del servicio
    c.pedir("error_no_es_imagen", "POST", "/api/diagnostico", files={"foto": ("notas.txt", b"no es una foto", "text/plain")})
    grande = b"\xff" * int((ficha.get("tamano_max_mb", 10) + 1) * 1024 * 1024)
    c.pedir("error_muy_grande", "POST", "/api/diagnostico", files={"foto": ("enorme.jpg", grande, "image/jpeg")})

    with open(args.salida / "peticiones.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(c.registro[0]))
        w.writeheader()
        w.writerows(c.registro)

    rutas = [f"| `{m.upper()}` | `{p}` | {d.get('summary', '')} |"
             for p, ops in openapi["paths"].items() for m, d in ops.items()]
    tabla = [f"| {i} | {real} | {diag} | {p:.3f} | {ms:.0f} | {umbral} | {sano} | {exacto} |"
             for i, real, diag, p, ms, umbral, sano, exacto in filas]
    md = [f"# Evidencia de integración: {args.url}", "",
          f"- Servicio: {salud['estado']}, modelo {salud['modelo']}, API {salud['version_api']}",
          f"- Umbral en uso: P(enferma) >= {ficha['umbral_en_uso']}", "",
          "## Endpoints publicados (OpenAPI)", "", "| Método | Ruta | Descripción |", "|---|---|---|", *rutas, "",
          "## Diagnósticos de las fotos de ejemplo", "",
          "Primeras 2 fotos de cada clase de la prueba de la Actividad 5 (el modelo no las vio al entrenar). "
          "Sirven para comprobar que la API responde con fotos reales; no son una evaluación del modelo.", "",
          "| Foto | Clase real | Diagnóstico | P(enferma) | ms | ¿Lo decidió el umbral? | ¿Acierta sana/enferma? "
          "| ¿Acierta la clase? |",
          "|---|---|---|---|---|---|---|---|", *tabla, "",
          "## Todas las peticiones", "", "| Petición | Método | Endpoint | HTTP | ms | Resultado |",
          "|---|---|---|---|---|---|",
          *[f"| {r['peticion']} | {r['metodo']} | `{r['endpoint']}` | {r['codigo_http']} | {r['tiempo_ms']} | "
            f"{r['resultado']} |" for r in c.registro], ""]
    (args.salida / "resumen.md").write_text("\n".join(md), encoding="utf-8")
    print(f"Evidencia guardada en {args.salida}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
