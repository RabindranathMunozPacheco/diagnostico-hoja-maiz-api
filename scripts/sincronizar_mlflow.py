"""Copia a MLflow las versiones de modelo/registro.json que todavía no estén ahí y pone el alias champion.

Se corre una vez al levantar el laboratorio (o si se borra el volumen de MLflow). Las versiones se registran en
orden, así la versión N del registro es la versión N de MLflow; cada una lleva sus métricas en los cuatro
conjuntos de evaluación (golden, adverso, prueba y prueba con lluvia), su ficha y su línea base. Después, los
scripts de despliegue y reentrenamiento y el servicio de operaciones mantienen el espejo al día.

Uso:
    python scripts/sincronizar_mlflow.py          # MLflow en http://localhost:5000 (MLFLOW_TRACKING_URI)
"""
import json
import sys
from pathlib import Path

import skops.io as sio

sys.path.insert(0, str(Path(__file__).resolve().parent))
import evaluacion  # noqa: E402
import versiones  # noqa: E402

espejo, comun = versiones.espejo_mlflow, versiones.comun


def main():
    registro = comun.leer_registro(versiones.DIR_MODELOS)
    for version in sorted(registro["versiones"], key=int):
        datos = registro["versiones"][version]
        carpeta = versiones.DIR_MODELOS / datos["carpeta"]
        ficha = json.loads((carpeta / "ficha_modelo.json").read_text(encoding="utf-8"))
        metricas = evaluacion.evaluar(sio.load(carpeta / ficha["archivo"]), ficha["umbral_enferma"]["valor"])
        parametros = {"origen": json.dumps(ficha.get("origen", {}), ensure_ascii=False)[:480]}
        mv = espejo.registrar_version(version, carpeta, ficha, metricas, datos["descripcion"], datos["estado"], parametros)
        print(f"v{version} -> MLflow versión {mv} ({datos['estado']}); golden F1 {metricas['golden']['f1_macro']}")
    actual = registro["alias"]["champion"]
    espejo.mover_alias(actual, "Sincronización inicial con modelo/registro.json")
    print(f"Alias champion en MLflow -> v{actual}")


if __name__ == "__main__":
    main()
