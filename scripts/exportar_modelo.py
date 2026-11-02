"""Copia el modelo elegido en la Actividad 5 a modelo/ y comprueba que es el mismo que se evaluó.

El modelo vive en el MLflow local de la Actividad 5 (models:/clasificador-hoja-maiz@candidato), que no se
sube a GitHub. Este script toma sus archivos, vuelve a calcular las métricas de prueba con el mismo corte
de datos y las compara contra las que quedaron registradas, y escribe modelo/ficha_modelo.json con el
origen, la huella sha256 y el umbral de decisión de la Actividad 6.

Solo hace falta correrlo una vez en la computadora donde está la Actividad 5; la API y el Dockerfile
usan los archivos que deja en modelo/.

Uso:
    python scripts/exportar_modelo.py
    python scripts/exportar_modelo.py --act5 "C:/ruta/a/Actividad5"
"""
import argparse
import hashlib
import json
import shutil
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import skops.io as sio
from sklearn.metrics import accuracy_score, f1_score
from sklearn.model_selection import StratifiedGroupKFold

RAIZ = Path(__file__).resolve().parents[1]
DESTINO = RAIZ / "modelo"
ACT5_DEFECTO = RAIZ.parents[1] / "actividad5" / "Actividad5"

MODELO_REGISTRADO = "clasificador-hoja-maiz"
ALIAS = "candidato"
SEMILLA = 42                      # la misma semilla con la que la Actividad 5 separó la prueba
UMBRAL = 0.2494                   # Actividad 6, corrida "ajuste_umbral": P(enferma) >= umbral -> enferma
EXTRACTOR = {"repositorio": "linkanjarad/mobilenet_v2_1.0_224-plant-disease-identification",
             "revision": "c1861579a670fb6232258805b801cd4137cb7176"}


def sha256(ruta):
    return hashlib.sha256(Path(ruta).read_bytes()).hexdigest()


def version_registrada(act5):
    """Versión, corrida y ruta del modelo que tiene el alias en el Model Registry de la Actividad 5."""
    con = sqlite3.connect(act5 / "mlflow.db")
    version, run_id, fuente = con.execute(
        "SELECT v.version, v.run_id, v.source FROM model_versions v JOIN registered_model_aliases a "
        "ON a.name = v.name AND a.version = v.version WHERE a.name = ? AND a.alias = ?",
        (MODELO_REGISTRADO, ALIAS)).fetchone()
    etiquetas = dict(con.execute("SELECT key, value FROM model_version_tags WHERE name = ? AND version = ?",
                                 (MODELO_REGISTRADO, version)).fetchall())
    datos_sha = con.execute("SELECT value FROM tags WHERE run_uuid = ? AND key = 'datos_sha256'",
                            (run_id,)).fetchone()[0]
    commit = con.execute("SELECT value FROM tags WHERE run_uuid = ? AND key = 'git_commit'", (run_id,)).fetchone()[0]
    con.close()
    model_id = fuente.split("/")[-1]                      # models:/m-... -> m-...
    carpeta = act5 / "mlruns" / "maiz-logreg-vs-rf" / "models" / model_id / "artifacts"
    return {"version": version, "run_id": run_id, "model_id": model_id, "carpeta": carpeta,
            "etiquetas": etiquetas, "datos_sha256": datos_sha, "git_commit": commit}


def prueba_act5(act5):
    """Las mismas 221 fotos de prueba de la Actividad 5: primer fold de StratifiedGroupKFold(5) por grupos."""
    limp = act5 / "datos" / "datos_limp"
    datos = pd.read_csv(limp / "maiz_parcela.csv").merge(pd.read_parquet(limp / "caracteristicas.parquet"),
                                                         on="id", validate="one_to_one")
    cv = StratifiedGroupKFold(n_splits=5, shuffle=True, random_state=SEMILLA)
    _, idx = next(cv.split(datos, datos["clase"], datos["grupo"]))
    prueba = datos.iloc[idx]
    columnas = [c for c in prueba.columns if c.startswith("f") and c[1:].isdigit()]
    return prueba[columnas], prueba["clase"].to_numpy(), prueba[["id", "clase", "sesion", "ruta", "sha256"]]


def guardar_manifiesto_prueba(fotos):
    """Lista de las fotos de prueba (sin las fotos) para que pruebas/validar_modelo.py las mande a la API."""
    manifiesto = {
        "descripcion": "Las 221 fotos de prueba de la Actividad 5 (primer fold de StratifiedGroupKFold(5), semilla 42, "
                       "por grupo). Las fotos no se suben al repositorio porque el dataset no declara licencia; "
                       "se descargan del origen en esta revisión y se comprueban con su sha256.",
        "repositorio": "muAtarist/maize_foliar_diseases", "tipo": "dataset",
        "revision": "448bec6c7abe18fe87917e443368c63ed3786119",
        "fotos": [{"id": r.id, "clase": r.clase, "sesion": str(r.sesion), "ruta": r.ruta, "sha256": r.sha256}
                  for r in fotos.itertuples()],
    }
    (RAIZ / "pruebas" / "prueba_act5.json").write_text(json.dumps(manifiesto, ensure_ascii=False, indent=2) + "\n",
                                                       encoding="utf-8")


def decidir(proba, clases, umbral):
    """Enferma si P(enferma) >= umbral, y en ese caso la enfermedad más probable (misma regla que la Act. 6)."""
    i_sana = list(clases).index("Sana")
    sin_sana = proba.copy()
    sin_sana[:, i_sana] = -1
    pred = np.asarray(clases, dtype=object)[sin_sana.argmax(1)]
    pred[(1 - proba[:, i_sana]) < umbral] = "Sana"
    return pred


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--act5", type=Path, default=ACT5_DEFECTO, help="carpeta del repo de la Actividad 5")
    act5 = parser.parse_args().act5.resolve()

    reg = version_registrada(act5)
    DESTINO.mkdir(exist_ok=True)
    for archivo in ("model.skops", "MLmodel"):
        shutil.copy2(reg["carpeta"] / archivo, DESTINO / archivo)

    modelo = sio.load(DESTINO / "model.skops")             # sin tipos "no confiables": StandardScaler + LogReg
    X, y, fotos = prueba_act5(act5)
    guardar_manifiesto_prueba(fotos)
    proba = modelo.predict_proba(X)
    pred = modelo.classes_[proba.argmax(1)]
    pred_umbral = decidir(proba, modelo.classes_, UMBRAL)
    enferma = y != "Sana"
    metricas = {
        "n_fotos": int(len(y)),
        "f1_macro": f1_score(y, pred, average="macro"),
        "exactitud": accuracy_score(y, pred),
        "enferma_como_sana": float(np.mean(pred[enferma] == "Sana")),
        "sana_como_enferma": float(np.mean(pred[~enferma] != "Sana")),
        "con_umbral_enferma_como_sana": float(np.mean(pred_umbral[enferma] == "Sana")),
        "con_umbral_sana_como_enferma": float(np.mean(pred_umbral[~enferma] != "Sana")),
    }

    # Las métricas recalculadas deben coincidir con las que se registraron junto al modelo en la Actividad 5
    registrada = float(reg["etiquetas"]["prueba_f1_macro"])
    if abs(metricas["f1_macro"] - registrada) > 1e-4:
        raise SystemExit(f"El F1 recalculado ({metricas['f1_macro']:.4f}) no coincide con el registrado ({registrada})")

    ficha = {
        "nombre": MODELO_REGISTRADO,
        "version_registrada": reg["version"],
        "alias": ALIAS,
        "algoritmo": "StandardScaler + LogisticRegression(C=0.1)",
        "archivo": "model.skops",
        "sha256": sha256(DESTINO / "model.skops"),
        "clases": list(map(str, modelo.classes_)),
        "extractor": {**EXTRACTOR, "salida": "pooler_output (1,280 características)", "tamano_entrada": "224x224 RGB"},
        "umbral_enferma": {"valor": UMBRAL, "origen": "Actividad 6, corrida ajuste_umbral",
                           "criterio": "umbral más alto con 5 % o menos de enfermas reportadas como sanas "
                                       "en validación fuera de fold"},
        "origen": {"repositorio": "https://github.com/RabindranathMunozPacheco/Actividad-5",
                   "version_publicada": "v0.5.0", "git_commit_entrenamiento": reg["git_commit"],
                   "mlflow_run_id": reg["run_id"],
                   "mlflow_model_id": reg["model_id"], "datos_sha256": reg["datos_sha256"]},
        "metricas_prueba": {k: (round(v, 4) if isinstance(v, float) else v) for k, v in metricas.items()},
    }
    (DESTINO / "ficha_modelo.json").write_text(json.dumps(ficha, ensure_ascii=False, indent=2) + "\n",
                                               encoding="utf-8")

    print(f"Modelo {MODELO_REGISTRADO} v{reg['version']} (@{ALIAS}) copiado a {DESTINO.relative_to(RAIZ)}/")
    print(f"  sha256 {ficha['sha256'][:16]}… | clases {ficha['clases']}")
    print(f"  prueba ({metricas['n_fotos']} fotos): F1 macro {metricas['f1_macro']:.4f} (registrado {registrada}), "
          f"enferma como sana {metricas['enferma_como_sana']:.1%}, "
          f"con umbral {UMBRAL}: {metricas['con_umbral_enferma_como_sana']:.1%}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
