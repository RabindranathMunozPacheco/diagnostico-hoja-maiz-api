# Escalabilidad, costos y gobernanza (Actividad 9)

Experimentos con los que se analizó la arquitectura actual (el laboratorio de la Actividad 8 subido tal cual a una
máquina virtual) y se sustentó el rediseño en Cloud Run. Los resultados quedan en `evidencias/escalamiento/`.

| Script | Qué mide | Evidencia |
|---|---|---|
| `linea_base.py` | Memoria y CPU de cada contenedor del laboratorio (en reposo y con 2 diagnósticos/s), peso de las imágenes, arranque en frío y costo de mandar la foto completa del celular | `linea_base/` |
| `exportar_onnx.py` | Exporta el MobileNetV2 a ONNX y lo cuantiza a int8 (dinámica, estática y estática calibrada con lluvia) | `modelo/extractores/extractores.json` |
| `comparar_extractores.py` | PyTorch contra ONNX en las 221 fotos de prueba: diferencia de características, acuerdo del diagnóstico, F1, FNR, tamaño y latencia | `extractor_onnx/` |
| `dimensionar.py` | Right-sizing: imagen PyTorch y ONNX con 0.5, 1 y 2 vCPU y varias concurrencias; costo por 1,000 diagnósticos en Cloud Run | `dimensionamiento/` |
| `horizontal.py` | 1, 2 y 3 réplicas detrás de nginx: capacidad, falla de una réplica y versiones mezcladas al hacer rollback | `horizontal/` |
| `recursos.py` | La imagen actual y la del rediseño con la misma carga (tráfico de Poisson a 2, 10 y 18 diagnósticos/s): CPU, memoria, latencia y ms de CPU por diagnóstico | `recursos/` |
| `auditoria_fairness.py` | FNR, FPR y PPV por condición de captura y por dispositivo para v1, v3 y v3 int8, con intervalos de Wilson; prueba fuera de dominio | `fairness/` |

`comun.py` tiene las herramientas compartidas (docker stats, contenedores desechables con límites, prueba de carga en
lazo cerrado y costo por 1,000 diagnósticos) y `precios_gcp.json` los precios de lista y SLA de Google Cloud que se
consultaron el 2026-11-20 en las páginas oficiales.

## Reproducir

```bash
python escalamiento/exportar_onnx.py           # genera modelo/extractores/*.onnx (no se suben al repositorio)
docker build -f Dockerfile.onnx -t diagnostico-hoja-maiz-api:0.6.0-onnx .
docker build -t diagnostico-hoja-maiz-api:0.6.0 .
python escalamiento/linea_base.py              # con el laboratorio de monitoreo arriba
python escalamiento/comparar_extractores.py
python escalamiento/dimensionar.py
python escalamiento/horizontal.py
python escalamiento/recursos.py
python escalamiento/auditoria_fairness.py
```

Los `.onnx` no se suben porque son pesos derivados de un modelo de Hugging Face que declara licencia "other" sin
publicar sus términos; la API comprueba su sha256 contra `extractores.json` antes de usarlos. La imagen ligera se
elige con `MOTOR_EXTRACTOR=onnx` (por defecto `pytorch`).
