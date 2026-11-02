# Casos extremos

- API: http://localhost:8000
- Foto base para comparar: m0005 (Roya común), diagnóstico Roya común, P(enferma) 0.9992
- Cumplen lo esperado: 31 de 32

| Caso | Entrada | Esperado | Obtenido | Imagen recibida | Cambio de P(enferma) | ¿Cumple? |
|---|---|---|---|---|---|---|
| **A. Archivos que no son una foto válida** | | | | | | |
| A1 | Archivo de texto con extensión .jpg | 415, no es una imagen | 415, el archivo no es una imagen válida |  |  | sí |
| A2 | Archivo vacío (0 bytes) | 415, no es una imagen | 415, el archivo no es una imagen válida |  |  | sí |
| A3 | JPEG cortado a la mitad (descarga incompleta) | 415, imagen dañada | 415, el archivo no es una imagen válida |  |  | sí |
| A4 | Documento PDF | 415, no es una imagen | 415, el archivo no es una imagen válida |  |  | sí |
| A5 | Imagen real de 11.5 MB (límite 10 MB) | 413, demasiado grande | 413, la foto pesa más de 10 MB |  |  | sí |
| A6 | PNG de 110 megapíxeles (0.3 MB comprimido) | 415, demasiados píxeles | 415, la imagen tiene demasiados píxeles |  |  | sí |
| A7 | PNG de 64 megapíxeles (0.2 MB comprimido) | 415, pasa el límite de 50 megapíxeles | 415, la imagen tiene demasiados píxeles |  |  | sí |
| A8 | Petición sin archivo | 422, falta el campo foto | 422, [{"type": "missing", "loc": ["body", "foto"], "msg": "Field required", "input": null}] |  |  | sí |
| A9 | Foto en un campo con otro nombre (imagen) | 422, falta el campo foto | 422, [{"type": "missing", "loc": ["body", "foto"], "msg": "Field required", "input": null}] |  |  | sí |
| A10 | GET en lugar de POST | 405, método no permitido | 405, Method Not Allowed |  |  | sí |
| A11 | Ruta que no existe | 404 | 404, Not Found |  |  | sí |
| **B. Fotos válidas en condiciones raras** | | | | | | |
| B1 | La misma foto en PNG | 200 y las mismas probabilidades que el JPG | 200, Roya común (P(enferma) 0.999, máx. 0.98) | 224x224 PNG RGB | 0.0 | sí |
| B2 | La misma foto en BMP | 200 y las mismas probabilidades que el JPG | 200, Roya común (P(enferma) 0.999, máx. 0.98) | 224x224 BMP RGB | 0.0 | sí |
| B3 | La misma foto en WebP sin pérdida | 200 y las mismas probabilidades que el JPG | 200, Roya común (P(enferma) 0.999, máx. 0.98) | 224x224 WEBP RGB | 0.0 | sí |
| B4 | La misma foto en GIF (solo 256 colores) | 200, diagnóstico igual o muy parecido | 200, Roya común (P(enferma) 0.999, máx. 0.96) | 224x224 GIF P | 0.0001 | sí |
| B5 | La misma foto en JPEG CMYK (modo de imprenta) | 200, diagnóstico igual | 200, Roya común (P(enferma) 0.999, máx. 0.98) | 224x224 JPEG CMYK | 0.0 | sí |
| B6 | PNG con transparencia (alfa al 50 %) | 200, la API ignora la transparencia | 200, Roya común (P(enferma) 0.999, máx. 0.98) | 224x224 PNG RGBA | 0.0 | sí |
| B7 | Foto de celular guardada de lado con etiqueta EXIF de orientación | 200 y el mismo diagnóstico que la foto derecha | 200, Roya común (P(enferma) 0.999, máx. 0.97) | 224x224 JPEG RGB | 0.0 | sí |
| B8 | La misma foto girada 90 grados (sin EXIF) | 200, diagnóstico igual | 200, Mancha gris (P(enferma) 0.991, máx. 0.56) | 224x224 PNG RGB | 0.0084 | no |
| B9 | La foto reducida a 16×16 píxeles | 200; con tan poca información el diagnóstico no es confiable | 200, Roya común (P(enferma) 0.259, máx. 0.74) | 16x16 PNG RGB | 0.7399 | sí |
| B10 | La foto ampliada a 4000×4000 píxeles | 200, diagnóstico igual (la API la reduce a 224×224) | 200, Roya común (P(enferma) 0.996, máx. 0.97) | 4000x4000 JPEG RGB | 0.0031 | sí |
| B11 | Recorte con proporción 4:3 de celular (224×168) | 200; la API la estira a 224×224 igual que en el entrenamiento | 200, Roya común (P(enferma) 0.999, máx. 0.99) | 224x168 JPEG RGB | 0.0004 | sí |
| **C. Imágenes que no son hojas de maíz** | | | | | | |
| C1 | Imagen verde lisa | 200 con alguna de las 4 clases: el modelo no tiene la opción 'no es una hoja' | 200, Roya común (P(enferma) 0.969, máx. 0.73) | 224x224 PNG RGB |  | sí |
| C2 | Imagen negra | 200 con alguna de las 4 clases: el modelo no tiene la opción 'no es una hoja' | 200, Mancha gris (P(enferma) 1.000, máx. 1.00) | 224x224 PNG RGB |  | sí |
| C3 | Imagen blanca | 200 con alguna de las 4 clases: el modelo no tiene la opción 'no es una hoja' | 200, Roya común (P(enferma) 0.441, máx. 0.56) | 224x224 PNG RGB |  | sí |
| C4 | Ruido aleatorio | 200 con alguna de las 4 clases: el modelo no tiene la opción 'no es una hoja' | 200, Roya común (P(enferma) 1.000, máx. 0.87) | 224x224 PNG RGB |  | sí |
| C5 | Degradado de colores | 200 con alguna de las 4 clases: el modelo no tiene la opción 'no es una hoja' | 200, Roya común (P(enferma) 0.952, máx. 0.92) | 224x224 PNG RGB |  | sí |
| C6 | Tablero de ajedrez | 200 con alguna de las 4 clases: el modelo no tiene la opción 'no es una hoja' | 200, Sana (P(enferma) 0.160, máx. 0.84) | 224x224 PNG RGB |  | sí |
| C7 | Captura de pantalla de la documentación /docs | 200 con alguna de las 4 clases: el modelo no tiene la opción 'no es una hoja' | 200, Roya común (P(enferma) 0.934, máx. 0.93) | 1400x1042 PNG RGB |  | sí |
| C8 | Captura de pantalla de la propia página web | 200 con alguna de las 4 clases: el modelo no tiene la opción 'no es una hoja' | 200, Mancha gris (P(enferma) 0.973, máx. 0.90) | 1400x1181 PNG RGB |  | sí |
| **D. Consistencia** | | | | | | |
| D1 | La misma foto 20 veces seguidas | 20 respuestas idénticas | 200, Roya común (P(enferma) 0.999, máx. 0.98) | 224x224 JPEG RGB |  | sí |
| D2 | Las 8 fotos de ejemplo, 32 peticiones con 8 en paralelo | Cada respuesta corresponde a su foto (no se mezclan) | 200, 32 de 32 iguales a la respuesta en serie |  |  | sí |
