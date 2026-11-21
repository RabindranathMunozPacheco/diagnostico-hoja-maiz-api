## Condición de captura

| Modelo | Grupo | n | Prevalencia | Marcadas enfermas | FNR (IC 95 %) | FPR | PPV | F1 macro |
|---|---|---|---|---|---|---|---|---|
| v1 | normal | 221 | 0.7964 | 0.8462 | 0.0795 (0.048-0.1291) | 0.5556 | 0.8663 | 0.7212 |
| v1 | oscura | 221 | 0.7964 | 0.6018 | 0.3068 (0.2434-0.3785) | 0.2444 | 0.9173 | 0.4152 |
| v1 | borrosa | 221 | 0.7964 | 0.6652 | 0.3239 (0.2591-0.3961) | 0.6222 | 0.8095 | 0.3303 |
| v1 | lluvia | 221 | 0.7964 | 0.6063 | 0.3182 (0.2539-0.3903) | 0.3111 | 0.8955 | 0.364 |
| v1 | gama baja | 221 | 0.7964 | 0.905 | 0.0455 (0.0232-0.0871) | 0.7111 | 0.84 | 0.4472 |
| v1 | **brecha de FNR** | | | | 0.2784 | | | |
| v3 | normal | 221 | 0.7964 | 0.8869 | 0.0568 (0.0312-0.1014) | 0.6667 | 0.8469 | 0.6582 |
| v3 | oscura | 221 | 0.7964 | 0.8688 | 0.0682 (0.0394-0.1154) | 0.6222 | 0.8542 | 0.6233 |
| v3 | borrosa | 221 | 0.7964 | 0.8552 | 0.0909 (0.0567-0.1426) | 0.6444 | 0.8466 | 0.4531 |
| v3 | lluvia | 221 | 0.7964 | 0.9095 | 0.0455 (0.0232-0.0871) | 0.7333 | 0.8358 | 0.539 |
| v3 | gama baja | 221 | 0.7964 | 0.9548 | 0.0057 (0.001-0.0315) | 0.8 | 0.8294 | 0.4996 |
| v3 | **brecha de FNR** | | | | 0.0852 | | | |
| v3 int8 | normal | 221 | 0.7964 | 0.8733 | 0.0568 (0.0312-0.1014) | 0.6 | 0.8601 | 0.6596 |
| v3 int8 | oscura | 221 | 0.7964 | 0.8778 | 0.0739 (0.0437-0.1223) | 0.6889 | 0.8402 | 0.5496 |
| v3 int8 | borrosa | 221 | 0.7964 | 0.8869 | 0.0739 (0.0437-0.1223) | 0.7333 | 0.8316 | 0.4285 |
| v3 int8 | lluvia | 221 | 0.7964 | 0.9095 | 0.0455 (0.0232-0.0871) | 0.7333 | 0.8358 | 0.5123 |
| v3 int8 | gama baja | 221 | 0.7964 | 0.9367 | 0.0227 (0.0089-0.057) | 0.7778 | 0.8309 | 0.5125 |
| v3 int8 | **brecha de FNR** | | | | 0.0512 | | | |

## Dispositivo

| Modelo | Grupo | n | Prevalencia | Marcadas enfermas | FNR (IC 95 %) | FPR | PPV | F1 macro |
|---|---|---|---|---|---|---|---|---|
| v1 | cámara digital | 80 | 0.85 | 0.925 | 0.0147 (0.0026-0.0787) | 0.5833 | 0.9054 | 0.6857 |
| v1 | sin identificar | 45 | 0.8444 | 0.9333 | 0.0526 (0.0146-0.1729) | 0.8571 | 0.8571 | 0.2755 |
| v1 | teléfono | 96 | 0.7292 | 0.7396 | 0.1571 (0.0901-0.2599) | 0.4615 | 0.831 | 0.745 |
| v1 | **brecha de FNR** | | | | 0.1424 | | | |
| v3 | cámara digital | 80 | 0.85 | 0.9 | 0.0441 (0.0151-0.1219) | 0.5833 | 0.9028 | 0.6856 |
| v3 | sin identificar | 45 | 0.8444 | 0.9778 | 0.0 (0.0-0.0918) | 0.8571 | 0.8636 | 0.3506 |
| v3 | teléfono | 96 | 0.7292 | 0.8333 | 0.1 (0.0493-0.1923) | 0.6538 | 0.7875 | 0.6363 |
| v3 | **brecha de FNR** | | | | 0.1 | | | |
| v3 int8 | cámara digital | 80 | 0.85 | 0.9 | 0.0294 (0.0081-0.101) | 0.5 | 0.9167 | 0.6629 |
| v3 int8 | sin identificar | 45 | 0.8444 | 0.9556 | 0.0 (0.0-0.0918) | 0.7143 | 0.8837 | 0.381 |
| v3 int8 | teléfono | 96 | 0.7292 | 0.8125 | 0.1143 (0.0591-0.2096) | 0.6154 | 0.7949 | 0.6493 |
| v3 int8 | **brecha de FNR** | | | | 0.1143 | | | |

## Fuera de dominio (v3, API completa)

| Imagen | Diagnóstico | P(enferma) | Confianza | Advertencias |
|---|---|---|---|---|
| verde liso | Roya común | 0.8977 | 0.543 | La foto tiene muy poco contraste (¿neblina o sombra?).; La foto está borrosa o movida; conviene tomarla otra vez. |
| café liso (suelo) | Mancha gris | 0.949 | 0.54 | La foto tiene muy poco contraste (¿neblina o sombra?).; La foto está borrosa o movida; conviene tomarla otra vez.; La foto no parece una hoja de maíz. |
| gris liso | Roya común | 0.9435 | 0.775 | La foto tiene muy poco contraste (¿neblina o sombra?).; La foto está borrosa o movida; conviene tomarla otra vez.; La foto no parece una hoja de maíz. |
| ruido | Mancha gris | 1.0 | 0.845 | (ninguna) |
| tablero | Sana | 0.0343 | 0.966 | La foto no parece una hoja de maíz. |
