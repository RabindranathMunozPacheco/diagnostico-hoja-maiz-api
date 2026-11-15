"""Descarga el MobileNetV2 que extrae las características, en la revisión fija de la ficha de cada versión del modelo.

El Dockerfile lo corre al construir la imagen, para que el contenedor ya traiga el extractor y funcione sin
internet (HF_HUB_OFFLINE=1). En local no hace falta: la API lo descarga la primera vez que arranca.

Uso:
    python scripts/descargar_extractor.py
"""
import json
from pathlib import Path

from transformers import MobileNetV2ImageProcessorPil, MobileNetV2Model

modelos = Path(__file__).resolve().parents[1] / "modelo" / "versiones"
extractores = {(f["extractor"]["repositorio"], f["extractor"]["revision"])
               for f in (json.loads(p.read_text(encoding="utf-8")) for p in modelos.glob("*/ficha_modelo.json"))}
for repo, revision in sorted(extractores):
    MobileNetV2ImageProcessorPil.from_pretrained(repo, revision=revision)
    MobileNetV2Model.from_pretrained(repo, revision=revision)
    print(f"Extractor listo: {repo}@{revision[:8]}")
