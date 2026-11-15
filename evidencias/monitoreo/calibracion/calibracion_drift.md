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
| 10% | 0.094 | 0.084 | 0.138 | 0.103 |
| 20% | 0.229 | 0.186 | 0.322 | 0.104 |
| 30% | 0.407 | 0.344 | 0.568 | 0.103 |
| 50% | 0.901 | 0.766 | 1.241 | 0.099 |
| 100% | 5.126 | 4.158 | 6.391 | 0.099 |
