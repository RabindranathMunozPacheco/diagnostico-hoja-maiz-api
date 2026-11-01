"""Configuración de la API.

Los valores salen de modelo/ficha_modelo.json (lo que describe al modelo) y se pueden cambiar con variables
de entorno sin tocar el código (ver .env.example). En el contenedor se fijan en el Dockerfile.
"""
import json
import os
from dataclasses import dataclass
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Config:
    ruta_modelo: Path
    ficha: dict
    umbral_enferma: float
    tamano_max_mb: float
    dir_frontend: Path

    @property
    def extractor_repo(self):
        return self.ficha["extractor"]["repositorio"]

    @property
    def extractor_revision(self):
        return self.ficha["extractor"]["revision"]


def cargar_config():
    dir_modelo = Path(os.environ.get("DIR_MODELO", RAIZ / "modelo"))
    ficha = json.loads((dir_modelo / "ficha_modelo.json").read_text(encoding="utf-8"))
    umbral = float(os.environ.get("UMBRAL_ENFERMA", ficha["umbral_enferma"]["valor"]))
    if not 0 <= umbral <= 1:
        raise ValueError(f"UMBRAL_ENFERMA debe estar entre 0 y 1 (se recibió {umbral})")
    return Config(ruta_modelo=dir_modelo / ficha["archivo"],
                  ficha=ficha,
                  umbral_enferma=umbral,
                  tamano_max_mb=float(os.environ.get("TAMANO_MAX_MB", 10)),
                  dir_frontend=Path(os.environ.get("DIR_FRONTEND", RAIZ / "frontend")))
