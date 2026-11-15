"""Prepara las fotos que usa el monitoreo: el golden set de la sonda y el conjunto del tráfico simulado.

Las 221 fotos de prueba (que ninguna versión vio al entrenar) se reparten en:
    golden set   10 fotos por clase (40), elegidas con semilla 42. La sonda las manda cada pocos segundos y,
                 como se conoce su clase, mide la calidad del modelo en vivo (F1 macro y enfermas como sanas).
    tráfico      las 181 restantes. El simulador de usuarios las manda una y otra vez.
De cada foto se guarda la original y una versión "lluvia":
    tráfico      brillo y desenfoque al azar por foto (brillo 35 a 80 %, radio 0 a 2.5, semilla 7), porque las fotos
                 reales de un día nublado no salen todas iguales. Es lo que se manda para simular el data drift.
    golden set   brillo al 55 % y radio 1.5 en todas: un conjunto adverso fijo (prueba de estrés reproducible) para
                 estimar el impacto del drift en la calidad. No dispara alertas.
La semilla del tráfico (7) es distinta de la del aumento de datos del reentrenamiento (42).

La lista del golden set (ids, rutas y sha256) se guarda en monitoreo/golden_set.json, que sí va al repositorio;
las fotos van a monitoreo/datos_locales/, que no se sube porque el dataset no declara licencia.

Uso:
    python scripts/preparar_datos.py
"""
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import datos  # noqa: E402

SEMILLA = 42
SEMILLA_LLUVIA_TRAFICO = 7
POR_CLASE = 10
MONITOREO = datos.RAIZ / "monitoreo"
LOCALES = MONITOREO / "datos_locales"


def guardar(fila, carpeta, parametros=None):
    im = datos.imagen(fila)
    lluvia = datos.lluvia(im, *parametros) if parametros else datos.lluvia(im)
    for condicion, imagen in (("original", im), ("lluvia", lluvia)):
        destino = LOCALES / carpeta / condicion / f"{fila['id']}.png"
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_bytes(datos.png(imagen))


def main():
    tabla = datos.tabla()
    prueba = tabla[tabla["parte"] == "prueba"]
    azar = np.random.default_rng(SEMILLA)
    golden = pd.concat([g.iloc[np.sort(azar.choice(len(g), POR_CLASE, replace=False))]
                        for _, g in prueba.groupby("clase")])
    trafico = prueba[~prueba["id"].isin(golden["id"])]

    lista = {"descripcion": f"Golden set de la sonda: {POR_CLASE} fotos por clase de la prueba de la Actividad 5, "
                            f"elegidas con semilla {SEMILLA}. Las fotos se generan con scripts/preparar_datos.py.",
             "repositorio": datos.REPO, "revision": datos.REVISION,
             "fotos": [{"id": f["id"], "clase": f["clase"], "ruta": f["ruta"], "sha256": f["sha256"]}
                       for _, f in golden.iterrows()]}
    (MONITOREO / "golden_set.json").write_text(json.dumps(lista, ensure_ascii=False, indent=2) + "\n",
                                               encoding="utf-8", newline="\n")
    azar_lluvia = np.random.default_rng(SEMILLA_LLUVIA_TRAFICO)
    parametros = {f["id"]: (float(azar_lluvia.uniform(0.35, 0.8)), float(azar_lluvia.uniform(0.0, 2.5)))
                  for _, f in trafico.iterrows()}
    for nombre, conjunto in (("golden", golden), ("trafico", trafico)):
        with ThreadPoolExecutor(8) as ex:
            list(ex.map(lambda f: guardar(f, nombre, parametros.get(f["id"]) if nombre == "trafico" else None),
                        [f for _, f in conjunto.iterrows()]))
        conjunto[["id", "clase"]].to_csv(LOCALES / nombre / "clases.csv", index=False, encoding="utf-8")
        print(f"{nombre}: {len(conjunto)} fotos x 2 condiciones -> {(LOCALES / nombre).relative_to(datos.RAIZ)}")
        print("   " + ", ".join(f"{c}: {n}" for c, n in conjunto["clase"].value_counts().items()))


if __name__ == "__main__":
    main()
