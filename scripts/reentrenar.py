"""Reentrena el clasificador para las condiciones nuevas que detectó el monitor de drift (paso manual de RB-05).

Pasos:
    1. Toma las 857 fotos de entrenamiento de la versión 1 y genera una copia de cada una en condición de lluvia
       con brillo (35 a 80 %) y desenfoque (radio 0 a 2.5) al azar, con semilla 42. Imita lo que detectó el
       monitor sin usar ninguna foto de prueba ni del golden set.
    2. Calcula las características de las copias con el mismo MobileNetV2 de la API y entrena la misma
       configuración que la v1 (StandardScaler + LogisticRegression C=0.1) con originales y copias.
    3. Recalibra el umbral con el criterio de la Actividad 6: el más alto con 5 % o menos de enfermas reportadas
       como sanas en validación fuera de fold (StratifiedGroupKFold de 5, por grupo de fotos parecidas; cada
       copia va en el grupo de su original para que no haya fuga entre folds).
    4. Compara la versión en servicio contra la candidata en el golden set, el conjunto adverso y la prueba
       completa (normal y con lluvia).
    5. La registra como candidata con su línea base (originales y copias): si se promueve, las fotos de lluvia
       dejan de contar como drift porque ya son parte de lo "normal" para esa versión.
No la promueve: eso lo hace scripts/desplegar.py, que pasa la compuerta de calidad.

Uso:
    python scripts/reentrenar.py --solicitud monitoreo/bitacora/solicitudes/reentrenamiento_AAAAMMDD_HHMMSS.json
"""
import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd
import skops.io as sio
from sklearn.base import clone
from sklearn.model_selection import StratifiedGroupKFold, cross_val_predict

sys.path.insert(0, str(Path(__file__).resolve().parent))
import datos  # noqa: E402
import evaluacion  # noqa: E402
import versiones  # noqa: E402

SEMILLA = 42
FNR_OBJETIVO = 0.05


def umbral_fuera_de_fold(modelo, X, y, grupos):
    cv = StratifiedGroupKFold(n_splits=5, shuffle=True, random_state=SEMILLA)
    proba = cross_val_predict(clone(modelo), X, y, groups=grupos, cv=cv, method="predict_proba")
    clases = sorted(set(y))
    p_enferma = 1 - proba[:, clases.index("Sana")]
    enfermas = p_enferma[np.asarray(y) != "Sana"]
    candidatos = np.unique(np.round(enfermas, 4))
    validos = [t for t in candidatos if np.mean(enfermas < t) <= FNR_OBJETIVO]
    umbral = float(max(validos))
    sanas = p_enferma[np.asarray(y) == "Sana"]
    return umbral, {"enferma_como_sana_oof": round(float(np.mean(enfermas < umbral)), 4),
                    "sana_como_enferma_oof": round(float(np.mean(sanas >= umbral)), 4)}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--solicitud", type=Path, help="solicitud de reentrenamiento creada por operaciones")
    parser.add_argument("--salida", type=Path, default=datos.RAIZ / "evidencias" / "monitoreo" / "reentrenamiento")
    args = parser.parse_args()
    solicitud = json.loads(args.solicitud.read_text(encoding="utf-8")) if args.solicitud else {}

    registro = versiones.comun.leer_registro(versiones.DIR_MODELOS)
    actual = registro["alias"]["champion"]
    carpeta_actual = versiones.DIR_MODELOS / registro["versiones"][actual]["carpeta"]
    ficha_actual = json.loads((carpeta_actual / "ficha_modelo.json").read_text(encoding="utf-8"))
    modelo_actual = sio.load(carpeta_actual / "model.skops")

    tabla = datos.tabla()
    ent = tabla[tabla["parte"] == "entrenamiento"].reset_index(drop=True)
    print(f"1. Generando {len(ent)} copias en condición de lluvia (semilla {SEMILLA})…")
    with ThreadPoolExecutor(8) as ex:
        originales = list(ex.map(datos.imagen, [f for _, f in ent.iterrows()]))
    azar = np.random.default_rng(SEMILLA)
    copias = [datos.lluvia_azar(im, azar) for im in originales]

    print("2. Características de las copias y entrenamiento…")
    X = pd.concat([datos.caracteristicas(ent["id"]).reset_index(drop=True).astype(np.float32),
                   evaluacion.caracteristicas_imagenes(copias)], ignore_index=True)
    y = np.concatenate([ent["clase"].values, ent["clase"].values])
    grupos = np.concatenate([ent["grupo"].values, ent["grupo"].values])
    candidata = clone(modelo_actual).fit(X, y)

    print("3. Umbral fuera de fold…")
    valor, oof = umbral_fuera_de_fold(modelo_actual, X, y, grupos)
    umbral = {"valor": round(valor, 4), "origen": "reentrenamiento con aumento de lluvia",
              "criterio": "umbral más alto con 5 % o menos de enfermas reportadas como sanas en validación fuera de "
                          "fold (StratifiedGroupKFold 5 por grupo)", **oof}

    print("4. Comparación contra la versión en servicio…")
    comparacion = {f"v{actual} (en servicio)": evaluacion.evaluar(modelo_actual, ficha_actual["umbral_enferma"]["valor"]),
                   "candidata": evaluacion.evaluar(candidata, umbral["valor"])}
    for nombre, m in comparacion.items():
        print(f"   {nombre:18s}", {k: (v["f1_macro"], v["enferma_como_sana"]) for k, v in m.items()})

    prueba = tabla[tabla["parte"] == "prueba"]
    todos = evaluacion.conjuntos()
    proba = candidata.predict_proba(pd.concat([todos["prueba"][0], todos["prueba_lluvia"][0]]).astype(np.float32))
    nueva = versiones.comun.siguiente_version(versiones.DIR_MODELOS)
    carpeta = versiones.guardar_version(
        candidata, nueva, umbral, originales + copias, proba,
        f"Reentrenada con aumento de lluvia (respuesta a drift; solicitud {solicitud.get('incidente', 'manual')})",
        {"origen": {"tipo": "reentrenamiento con aumento de datos", "version_base": actual, "semilla": SEMILLA,
                    "datos": f"{len(ent)} fotos de entrenamiento + {len(copias)} copias con lluvia al azar",
                    "solicitud": solicitud.get("incidente")},
         "metricas_prueba": comparacion["candidata"]["prueba"],
         "metricas_prueba_lluvia": comparacion["candidata"]["prueba_lluvia"]},
        f"Entrada: {len(ent)} fotos de entrenamiento y sus {len(copias)} copias con lluvia. Predicciones: "
        f"{len(prueba)} fotos de prueba normales y {len(prueba)} con lluvia.",
        metricas=comparacion["candidata"], parametros={"aumento": "lluvia_azar (brillo 0.35-0.8, radio 0-2.5)",
                                                       "copias": len(copias), "semilla": SEMILLA,
                                                       "version_base": actual})

    args.salida.mkdir(parents=True, exist_ok=True)
    resumen = {"version_candidata": nueva, "version_en_servicio": actual, "umbral": umbral,
               "comparacion": comparacion, "solicitud": solicitud}
    (args.salida / f"reentrenamiento_v{nueva}.json").write_text(json.dumps(resumen, ensure_ascii=False, indent=2) + "\n",
                                                               encoding="utf-8", newline="\n")
    lineas = [f"# Reentrenamiento: v{nueva} candidata contra v{actual} en servicio", "",
              f"Umbral recalibrado: {umbral['valor']} (fuera de fold: {oof['enferma_como_sana_oof']:.1%} enfermas como "
              f"sanas, {oof['sana_como_enferma_oof']:.1%} sanas como enfermas)", "",
              "| Versión | Conjunto | F1 macro | Exactitud | Enfermas como sanas | Sanas como enfermas |",
              "|---|---|---|---|---|---|"]
    for nombre, m in comparacion.items():
        for conjunto, v in m.items():
            lineas.append(f"| {nombre} | {conjunto} ({v['n']}) | {v['f1_macro']:.3f} | {v['exactitud']:.3f} | "
                          f"{v['enferma_como_sana']:.1%} | {v['sana_como_enferma']:.1%} |")
    (args.salida / f"reentrenamiento_v{nueva}.md").write_text("\n".join(lineas) + "\n", encoding="utf-8", newline="\n")
    versiones.espejo_mlflow.registrar_evento(
        "reentrenamiento", f"v{actual} -> candidata v{nueva}",
        parametros={"version_base": actual, "version_candidata": nueva, "umbral": umbral["valor"],
                    "solicitud": solicitud.get("incidente", "manual"), "semilla": SEMILLA},
        metricas={f"{quien}_{conjunto}_{k}": v for quien, m in (("actual", comparacion[f"v{actual} (en servicio)"]),
                                                                 ("candidata", comparacion["candidata"]))
                  for conjunto, met in m.items() for k, v in met.items() if k in ("f1_macro", "enferma_como_sana")},
        artefactos=[args.salida / f"reentrenamiento_v{nueva}.md", args.salida / f"reentrenamiento_v{nueva}.json"])
    print(f"5. Candidata registrada: v{nueva} en {carpeta.relative_to(datos.RAIZ)} (también en MLflow)")


if __name__ == "__main__":
    main()
