"""Guarda una versión nueva del modelo en el registro: clasificador, ficha y línea base (queda como candidata).

La usan scripts/reentrenar.py y simulacion/crear_version_defectuosa.py. Ninguna versión nueva llega a producción
sola: para servirla hay que promoverla con scripts/desplegar.py, que pasa la compuerta de calidad.
"""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import skops.io as sio

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "monitoreo" / "servicios"))
import comun  # noqa: E402
import datos  # noqa: E402
import espejo_mlflow  # noqa: E402
import linea_base  # noqa: E402

DIR_MODELOS = datos.RAIZ / "modelo"


def autor_git():
    try:
        return subprocess.run(["git", "config", "user.name"], capture_output=True, encoding="utf-8", cwd=datos.RAIZ,
                              check=True).stdout.strip() or "desconocido"
    except (OSError, subprocess.CalledProcessError):
        return "desconocido"


def guardar_version(modelo, version, umbral, imagenes_entrenamiento, proba_validacion, descripcion, ficha_extra,
                    descripcion_linea_base, estado="candidata", metricas=None, parametros=None):
    carpeta = DIR_MODELOS / "versiones" / str(version)
    carpeta.mkdir(parents=True, exist_ok=False)
    sio.dump(modelo, carpeta / "model.skops")
    ficha_v1 = json.loads((DIR_MODELOS / "versiones" / "1" / "ficha_modelo.json").read_text(encoding="utf-8"))
    ficha = {"nombre": ficha_v1["nombre"], "version_registrada": int(version),
             "algoritmo": "StandardScaler + LogisticRegression(C=0.1)", "archivo": "model.skops",
             "sha256": hashlib.sha256((carpeta / "model.skops").read_bytes()).hexdigest(),
             "clases": [str(c) for c in modelo.classes_], "extractor": ficha_v1["extractor"],
             "umbral_enferma": umbral, **ficha_extra}
    (carpeta / "ficha_modelo.json").write_text(json.dumps(ficha, ensure_ascii=False, indent=2) + "\n",
                                               encoding="utf-8", newline="\n")
    linea = {"version": str(version), "creada": comun.ahora()[:19], "descripcion": descripcion_linea_base,
             "variables": linea_base.variables_de_entrada(imagenes_entrenamiento),
             "predicciones": linea_base.predicciones(proba_validacion, ficha["clases"], umbral["valor"])}
    (carpeta / "linea_base.json").write_text(json.dumps(linea, ensure_ascii=False, indent=2) + "\n",
                                             encoding="utf-8", newline="\n")
    comun.registrar_version(version, f"versiones/{version}", descripcion, estado, autor_git(), DIR_MODELOS)
    espejo_mlflow.registrar_version(version, carpeta, ficha, metricas or {}, descripcion, estado, parametros)
    return carpeta
