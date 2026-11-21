| Extractor | Archivo (MiB) | Latencia p50 (ms) | Coseno mín. | Acuerdo v3 | F1 v3 | Enf. como sana v3 |
|---|---|---|---|---|---|---|
| pytorch | 8.48 | 23.98 | 1.0 | 1.0 | 0.6582 | 0.0568 |
| mobilenet_v2_fp32.onnx | 8.6 | 7.29 | 1.0 | 1.0 | 0.6582 | 0.0568 |
| mobilenet_v2_int8_dinamico.onnx | 2.34 | 22.94 | 0.248291 | 0.6833 | 0.2457 | 0.0114 |
| mobilenet_v2_int8_estatico.onnx | 2.53 | 4.64 | 0.978413 | 0.9502 | 0.6596 | 0.0568 |
| mobilenet_v2_int8_estatico_lluvia.onnx | 2.53 | 4.63 | 0.977909 | 0.9548 | 0.6725 | 0.0625 |
