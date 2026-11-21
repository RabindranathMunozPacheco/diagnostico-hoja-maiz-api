"""Exporta el MobileNetV2 congelado a ONNX y lo cuantiza a int8 (Actividad 9, optimización de la inferencia).

Genera en modelo/extractores/:
    mobilenet_v2_fp32.onnx            la misma red, en formato ONNX (32 bits)
    mobilenet_v2_int8_dinamico.onnx   pesos en 8 bits; las activaciones se cuantizan al vuelo (no pide datos)
    mobilenet_v2_int8_estatico.onnx   pesos y activaciones en 8 bits, con rangos calibrados en fotos de
                                      entrenamiento (100 fotos al azar con semilla 42; nunca fotos de prueba)
    mobilenet_v2_int8_estatico_lluvia.onnx
                                      igual, pero calibrada con esas 100 fotos y otras 100 copias con lluvia al azar
                                      (semilla 42), para que los rangos cubran también las fotos oscuras y borrosas
    extractores.json                  ficha de cada archivo: origen, método, sha256 y tamaño

Los .onnx no se suben al repositorio: son pesos derivados de un modelo de Hugging Face que declara licencia "other"
sin publicar sus términos, así que no se redistribuyen; se regeneran con este script y se verifican con su sha256.

Uso:
    python escalamiento/exportar_onnx.py                  # los tres
    python escalamiento/exportar_onnx.py --sin-estatico   # sin la versión calibrada (no necesita las fotos)
"""
import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

import numpy as np
import torch
from onnxruntime.quantization import (CalibrationDataReader, CalibrationMethod, QuantFormat, QuantType,
                                      quantize_dynamic, quantize_static)
from onnxruntime.quantization.shape_inference import quant_pre_process

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "scripts"))
from app.extractor_onnx import preparar  # noqa: E402
from app.modelo import Extractor  # noqa: E402

DESTINO = RAIZ / "modelo" / "extractores"
SEMILLA = 42
N_CALIBRACION = 100


class SoloCaracteristicas(torch.nn.Module):
    """MobileNetV2 de transformers regresa un objeto; para ONNX se deja solo el pooler_output (1,280)."""

    def __init__(self, modelo):
        super().__init__()
        self.modelo = modelo

    def forward(self, pixel_values):
        return self.modelo(pixel_values=pixel_values).pooler_output


class Calibracion(CalibrationDataReader):
    def __init__(self, imagenes):
        self.lotes = iter([{"pixel_values": preparar([im])} for im in imagenes])

    def get_next(self):
        return next(self.lotes, None)


def huella(ruta):
    return hashlib.sha256(ruta.read_bytes()).hexdigest()


def fotos_calibracion():
    import datos
    tabla = datos.tabla()
    elegidas = tabla[tabla["parte"] == "entrenamiento"].sample(N_CALIBRACION, random_state=SEMILLA)
    return [datos.imagen(f) for _, f in elegidas.iterrows()], elegidas["id"].tolist()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sin-estatico", action="store_true")
    args = parser.parse_args()

    ficha = json.loads((RAIZ / "modelo" / "versiones" / "3" / "ficha_modelo.json").read_text(encoding="utf-8"))
    repo, revision = ficha["extractor"]["repositorio"], ficha["extractor"]["revision"]
    DESTINO.mkdir(parents=True, exist_ok=True)
    fp32, pre = DESTINO / "mobilenet_v2_fp32.onnx", DESTINO / "mobilenet_v2_pre.onnx"
    dinamico, estatico = DESTINO / "mobilenet_v2_int8_dinamico.onnx", DESTINO / "mobilenet_v2_int8_estatico.onnx"

    modelo = SoloCaracteristicas(Extractor(repo, revision).modelo).eval()
    torch.onnx.export(modelo, (torch.zeros(1, 3, 224, 224),), str(fp32), input_names=["pixel_values"],
                      output_names=["caracteristicas"], opset_version=17, dynamo=False,
                      dynamic_axes={"pixel_values": {0: "lote"}, "caracteristicas": {0: "lote"}})
    quant_pre_process(str(fp32), str(pre))
    quantize_dynamic(str(pre), str(dinamico), weight_type=QuantType.QUInt8)
    fichas = {
        fp32.name: {"metodo": "exportación directa de PyTorch (opset 17)", "bits": 32},
        dinamico.name: {"metodo": "cuantización dinámica (quantize_dynamic, pesos QUInt8)", "bits": 8},
    }
    if not args.sin_estatico:
        import datos
        imagenes, ids = fotos_calibracion()
        azar = np.random.default_rng(SEMILLA)
        con_lluvia = imagenes + [datos.lluvia_azar(im, azar) for im in imagenes]
        metodo = "cuantización estática QDQ por canal (pesos QInt8, activaciones QUInt8, rangos MinMax)"
        for destino, calibracion, descripcion in (
                (estatico, imagenes, f"{N_CALIBRACION} fotos de entrenamiento"),
                (DESTINO / "mobilenet_v2_int8_estatico_lluvia.onnx", con_lluvia,
                 f"{N_CALIBRACION} fotos de entrenamiento y sus copias con lluvia al azar")):
            quantize_static(str(pre), str(destino), Calibracion(calibracion), quant_format=QuantFormat.QDQ,
                            per_channel=True, weight_type=QuantType.QInt8, activation_type=QuantType.QUInt8,
                            calibrate_method=CalibrationMethod.MinMax)
            fichas[destino.name] = {"metodo": metodo, "bits": 8,
                                    "calibracion": {"fotos": len(calibracion), "descripcion": descripcion,
                                                    "parte": "entrenamiento", "semilla": SEMILLA, "ids": ids}}
    pre.unlink(missing_ok=True)

    for nombre, f in fichas.items():
        ruta = DESTINO / nombre
        f.update(sha256=huella(ruta), bytes=ruta.stat().st_size)
    salida = {"origen": {"repositorio": repo, "revision": revision, "salida": "pooler_output (1,280)"},
              "generado": time.strftime("%Y-%m-%d %H:%M:%S"),
              "versiones": {"torch": torch.__version__, "onnxruntime": __import__("onnxruntime").__version__,
                            "onnx": __import__("onnx").__version__, "numpy": np.__version__},
              "archivos": fichas}
    (DESTINO / "extractores.json").write_text(json.dumps(salida, ensure_ascii=False, indent=2), encoding="utf-8")
    for nombre, f in fichas.items():
        print(f"{nombre}: {f['bytes'] / 2**20:.2f} MiB  {f['sha256'][:12]}")


if __name__ == "__main__":
    main()
