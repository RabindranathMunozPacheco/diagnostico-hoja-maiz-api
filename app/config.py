"""Configuración de la API y registro de versiones del modelo.

El modelo se toma del registro de versiones (modelo/registro.json), que funciona como los alias del Model Registry
de MLflow: la API sirve la versión a la que apunta el alias "champion" (o el que diga ALIAS_MODELO). Promover una
versión o regresar a la anterior (rollback) es mover el alias y pedirle a la API que recargue; no hay que
reconstruir la imagen ni tocar el código. Cada versión vive en modelo/versiones/<n>/ con su ficha, su
clasificador y su línea base (la distribución de las fotos con las que se entrenó, que usa el monitor de drift).

Los demás valores se cambian con variables de entorno (ver .env.example). Una variable vacía cuenta como no puesta.
"""
import json
import os
from dataclasses import dataclass
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]


def _env(nombre, defecto=None):
    valor = os.environ.get(nombre, "").strip()
    return valor if valor else defecto


@dataclass(frozen=True)
class VersionModelo:
    version: str
    carpeta: Path
    ficha: dict
    linea_base: dict | None

    @property
    def ruta_clasificador(self):
        return self.carpeta / self.ficha["archivo"]

    @property
    def nombre(self):
        return f"{self.ficha['nombre']} v{self.version}"

    @property
    def extractor(self):
        return self.ficha["extractor"]["repositorio"], self.ficha["extractor"]["revision"]


@dataclass(frozen=True)
class Config:
    dir_modelos: Path
    alias: str
    umbral_forzado: float | None       # si se define UMBRAL_ENFERMA, manda sobre el umbral de la ficha
    tamano_max_mb: float
    dir_frontend: Path
    token_admin: str | None            # sin token, los endpoints de administración quedan apagados
    caos_habilitado: bool              # inyección de fallas: solo en el entorno de simulación de incidentes
    entorno: str

    def umbral(self, version):
        return self.umbral_forzado if self.umbral_forzado is not None else version.ficha["umbral_enferma"]["valor"]


def leer_registro(dir_modelos):
    return json.loads((Path(dir_modelos) / "registro.json").read_text(encoding="utf-8"))


def cargar_version(config, version=None):
    """Lee la versión indicada o, si no se indica, la que tiene el alias de la configuración."""
    registro = leer_registro(config.dir_modelos)
    if version is None:
        if config.alias not in registro["alias"]:
            raise ValueError(f"el registro no tiene el alias '{config.alias}'")
        version = registro["alias"][config.alias]
    version = str(version)
    if version not in registro["versiones"]:
        raise ValueError(f"el registro no tiene la versión {version}")
    carpeta = config.dir_modelos / registro["versiones"][version]["carpeta"]
    ficha = json.loads((carpeta / "ficha_modelo.json").read_text(encoding="utf-8"))
    ruta_linea = carpeta / "linea_base.json"
    linea_base = json.loads(ruta_linea.read_text(encoding="utf-8")) if ruta_linea.exists() else None
    return VersionModelo(version=version, carpeta=carpeta, ficha=ficha, linea_base=linea_base)


def cargar_config():
    umbral = _env("UMBRAL_ENFERMA")
    umbral = None if umbral is None else float(umbral)
    if umbral is not None and not 0 <= umbral <= 1:
        raise ValueError(f"UMBRAL_ENFERMA debe estar entre 0 y 1 (se recibió {umbral})")
    return Config(dir_modelos=Path(_env("DIR_MODELOS", RAIZ / "modelo")),
                  alias=_env("ALIAS_MODELO", "champion"),
                  umbral_forzado=umbral,
                  tamano_max_mb=float(_env("TAMANO_MAX_MB", 10)),
                  dir_frontend=Path(_env("DIR_FRONTEND", RAIZ / "frontend")),
                  token_admin=_env("TOKEN_ADMIN"),
                  caos_habilitado=_env("CAOS_HABILITADO", "0") == "1",
                  entorno=_env("ENTORNO", "local"))
