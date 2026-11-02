# Validación del modelo a través de la API

- API: http://localhost:8000 · 221 fotos de prueba de la Actividad 5 · umbral 0.2494
- ¿Las métricas de la API son iguales a las registradas? **sí**

## Fotos originales contra lo registrado

| Métrica | Registrada (Act. 5 y 6) | API |
|---|---|---|
| n_fotos | 221 | 221 |
| f1_macro | 0.7212 | 0.7212 |
| exactitud | 0.7511 | 0.7511 |
| enferma_como_sana | 0.1136 | 0.1136 |
| con_umbral_enferma_como_sana | 0.0795 | 0.0795 |
| con_umbral_sana_como_enferma | 0.5556 | 0.5556 |

## Fotos alteradas

| Variante | Qué se cambió | F1 macro | Exactitud | Enfermas como sanas (umbral) | Sanas como enfermas (umbral) | Mismo diagnóstico que la original | Cambio medio de P(enferma) |
|---|---|---|---|---|---|---|---|
| original | La foto tal cual, sin cambios | 0.72 | 0.75 | 8.0 % | 55.6 % | 100.0 % | 0.000 |
| oscura | Brillo al 40 % | 0.42 | 0.55 | 30.7 % | 24.4 % | 57.0 % | 0.306 |
| sobreexpuesta | Brillo al 180 % | 0.51 | 0.62 | 5.7 % | 62.2 % | 66.1 % | 0.185 |
| borrosa | Desenfoque gaussiano de radio 3 | 0.33 | 0.39 | 32.4 % | 62.2 % | 42.5 % | 0.389 |
| comprimida | JPEG con calidad 10 | 0.41 | 0.62 | 8.5 % | 62.2 % | 65.2 % | 0.189 |
| girada_90 | Girada 90 grados | 0.66 | 0.73 | 1.1 % | 55.6 % | 74.7 % | 0.134 |
| volteada | Espejo horizontal | 0.75 | 0.77 | 4.5 % | 55.6 % | 86.4 % | 0.066 |
| gris | Escala de grises | 0.58 | 0.68 | 9.1 % | 37.8 % | 75.1 % | 0.132 |
| recortada | Recorte del 60 % central (acercamiento) | 0.70 | 0.73 | 8.5 % | 40.0 % | 69.7 % | 0.156 |
| ruido | Ruido gaussiano (sigma 25) | 0.23 | 0.44 | 0.0 % | 100.0 % | 48.0 % | 0.212 |

## F1 por clase

| Variante | Mancha gris | Roya común | Sana | Tizón |
|---|---|---|---|---|
| original | 0.81 | 0.88 | 0.60 | 0.59 |
| oscura | 0.69 | 0.32 | 0.50 | 0.15 |
| sobreexpuesta | 0.73 | 0.54 | 0.48 | 0.30 |
| borrosa | 0.45 | 0.14 | 0.36 | 0.36 |
| comprimida | 0.76 | 0.38 | 0.51 | 0.00 |
| girada_90 | 0.81 | 0.56 | 0.61 | 0.64 |
| volteada | 0.83 | 0.92 | 0.61 | 0.64 |
| gris | 0.78 | 0.62 | 0.63 | 0.31 |
| recortada | 0.79 | 0.82 | 0.65 | 0.56 |
| ruido | 0.67 | 0.18 | 0.00 | 0.06 |
