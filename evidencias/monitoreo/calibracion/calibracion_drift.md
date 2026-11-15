# Calibración del monitor de drift

Ventanas de 570 fotos sacadas al azar de las 181 fotos de tráfico (semilla 42).

## Tráfico normal: PSI máximo por ventana (sin drift)

| Bins | p50 | p95 | p99 | Ventanas arriba de 0.25 |
|---|---|---|---|---|
| bins_finos | 0.168 | 0.229 | 0.261 | 2% |
| bins_agrupados_5pc | 0.111 | 0.15 | 0.176 | 0% |

## Fracción de fotos en lluvia: mediana del PSI (bins agrupados)

| Lluvia | brillo | contraste | nitidez | verdor |
|---|---|---|---|---|
| 10% | 0.106 | 0.092 | 0.147 | 0.103 |
| 20% | 0.273 | 0.213 | 0.355 | 0.102 |
| 30% | 0.484 | 0.397 | 0.619 | 0.101 |
| 50% | 1.087 | 0.892 | 1.362 | 0.097 |
| 100% | 8.006 | 5.49 | 9.432 | 0.093 |
