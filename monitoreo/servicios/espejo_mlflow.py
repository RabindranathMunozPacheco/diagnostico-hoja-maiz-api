"""MLflow como herramienta de seguimiento y monitoreo del modelo.

La API sirve la versión que dice modelo/registro.json (sencillo y sin dependencias). MLflow lleva la misma historia
con sus propias herramientas, para poder consultarla, compararla y graficarla:
    Model Registry "clasificador-hoja-maiz"   cada versión con su clasificador (formato skops), ficha y línea base;
                                             el alias "champion" se mueve igual que en registro.json (despliegue
                                             o rollback) y cada versión lleva la etiqueta "estado"
    experimento "versiones-del-modelo"        una corrida por versión registrada (parámetros, métricas, artefactos)
    experimento "monitoreo-produccion"        una corrida por cada periodo en que una versión está en servicio; el
                                             vigilante registra en cada ronda el F1 de la sonda, las enfermas como
                                             sanas, el PSI y el KS de cada variable (la curva de calidad en el tiempo)
    experimento "reentrenamiento"             cada reentrenamiento con su comparación contra la versión en servicio
    experimento "despliegues"                 cada promoción con el resultado de la compuerta
    experimento "respuesta-a-incidentes"      cada acción automática de operaciones (rollback, reinicio...)
Si MLflow no responde se avisa y se sigue: el servicio no depende de MLflow para funcionar.
"""
import logging
import os
import sys
import time

# MLflow imprime emojis al cerrar una corrida; en una consola de Windows (cp1252) eso truena
for _flujo in (sys.stdout, sys.stderr):
    if hasattr(_flujo, "reconfigure") and (_flujo.encoding or "").lower() not in ("utf-8", "utf8"):
        _flujo.reconfigure(encoding="utf-8", errors="replace")

NOMBRE = "clasificador-hoja-maiz"
URI = os.environ.get("MLFLOW_TRACKING_URI", "http://localhost:5000")
log = logging.getLogger("espejo_mlflow")


def _cliente():
    import mlflow
    from mlflow import MlflowClient
    mlflow.set_tracking_uri(URI)
    return MlflowClient(URI)


def _experimento(cliente, nombre):
    """Id del experimento (lo crea si no existe). Se marca como desarrollo de modelos clásico: sin esa etiqueta,
    la interfaz de MLflow 3 lo abre en la vista GenAI (trazas de LLM) y no en la tabla de corridas."""
    exp = cliente.get_experiment_by_name(nombre)
    id_exp = exp.experiment_id if exp else cliente.create_experiment(nombre)
    cliente.set_experiment_tag(id_exp, "mlflow.experimentKind", "custom_model_development")
    return id_exp


def _seguro(funcion):
    def envoltura(*args, **kwargs):
        try:
            return funcion(*args, **kwargs)
        except Exception as e:                          # noqa: BLE001 - MLflow es un espejo, no una dependencia
            log.warning("MLflow no disponible para %s: %s", funcion.__name__, e)
            return None
    return envoltura


def version_mlflow(cliente, version_registro):
    """Número de versión en MLflow de una versión de registro.json (etiqueta version_registro)."""
    for mv in cliente.search_model_versions(f"name='{NOMBRE}'"):
        if mv.tags.get("version_registro") == str(version_registro):
            return mv.version
    return None


@_seguro
def registrar_version(version, carpeta, ficha, metricas, descripcion, estado="candidata", parametros=None):
    """Corrida en "versiones-del-modelo" + versión del Model Registry (solo desde la computadora: usa scikit-learn)."""
    import mlflow
    import mlflow.sklearn
    import skops.io as sio
    cliente = _cliente()
    if version_mlflow(cliente, version):
        return version_mlflow(cliente, version)
    mlflow.set_experiment(experiment_id=_experimento(cliente, "versiones-del-modelo"))
    with mlflow.start_run(run_name=f"v{version}") as corrida:
        mlflow.set_tags({"version_registro": str(version), "descripcion": descripcion})
        mlflow.log_params({"algoritmo": ficha.get("algoritmo"), "umbral_enferma": ficha["umbral_enferma"]["valor"],
                           "extractor": ficha["extractor"]["repositorio"], "sha256": ficha["sha256"][:12],
                           **(parametros or {})})
        mlflow.log_metrics({f"{conjunto}_{k}": v for conjunto, m in metricas.items() for k, v in m.items()
                            if isinstance(v, (int, float)) and k != "n"})
        for archivo in ("ficha_modelo.json", "linea_base.json"):
            if (carpeta / archivo).exists():
                mlflow.log_artifact(str(carpeta / archivo), artifact_path="registro")
        info = mlflow.sklearn.log_model(sio.load(carpeta / ficha["archivo"]), name="modelo",
                                        serialization_format="skops", registered_model_name=NOMBRE)
    mv = info.registered_model_version
    cliente.set_model_version_tag(NOMBRE, mv, "version_registro", str(version))
    cliente.set_model_version_tag(NOMBRE, mv, "estado", estado)
    cliente.update_model_version(NOMBRE, mv, description=descripcion)
    log.info("MLflow: v%s registrada como versión %s (corrida %s)", version, mv, corrida.info.run_id)
    return mv


@_seguro
def mover_alias(version, motivo, alias="champion", estado_anterior=None, anterior=None):
    cliente = _cliente()
    mv = version_mlflow(cliente, version)
    if mv is None:
        log.warning("MLflow: la v%s no está registrada; no se mueve el alias", version)
        return None
    cliente.set_registered_model_alias(NOMBRE, alias, mv)
    cliente.set_model_version_tag(NOMBRE, mv, "estado", "produccion")
    cliente.set_model_version_tag(NOMBRE, mv, "ultimo_cambio", motivo[:250])
    if anterior and estado_anterior:
        mv_anterior = version_mlflow(cliente, anterior)
        if mv_anterior:
            cliente.set_model_version_tag(NOMBRE, mv_anterior, "estado", estado_anterior)
    return mv


@_seguro
def registrar_evento(experimento, nombre, parametros=None, metricas=None, etiquetas=None, artefactos=()):
    """Una corrida corta que documenta un evento (despliegue, acción automática, reentrenamiento)."""
    import mlflow
    mlflow.set_experiment(experiment_id=_experimento(_cliente(), experimento))
    with mlflow.start_run(run_name=nombre) as corrida:
        if etiquetas:
            mlflow.set_tags(etiquetas)
        if parametros:
            mlflow.log_params({k: str(v)[:500] for k, v in parametros.items()})
        if metricas:
            mlflow.log_metrics({k: float(v) for k, v in metricas.items() if v is not None})
        for ruta in artefactos:
            mlflow.log_artifact(str(ruta))
    return corrida.info.run_id


class MonitoreoMLflow:
    """Una corrida de "monitoreo-produccion" por versión en servicio; cada ronda del vigilante es un paso."""

    def __init__(self):
        self.version, self.run_id, self.paso = None, None, 0

    @_seguro
    def registrar(self, version, metricas):
        from mlflow.entities import Metric
        cliente = _cliente()
        if version != self.version:
            if self.run_id:
                cliente.set_terminated(self.run_id)
            exp = _experimento(cliente, "monitoreo-produccion")
            corrida = cliente.create_run(exp, run_name=f"produccion-v{version}",
                                         tags={"version_registro": str(version), "origen": "vigilante"})
            self.version, self.run_id, self.paso = version, corrida.info.run_id, 0
        self.paso += 1
        ahora = int(time.time() * 1000)
        valores = [Metric(k, float(v), ahora, self.paso) for k, v in metricas.items()
                   if v is not None and v == v]           # sin NaN
        if valores:
            cliente.log_batch(self.run_id, metrics=valores)
