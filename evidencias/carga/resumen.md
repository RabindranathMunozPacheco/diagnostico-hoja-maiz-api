# Carga y recursos

- Contenedor `diagnostico-hoja-maiz` en http://localhost:8000
- Memoria en reposo: 587 MB

## Peticiones al mismo tiempo

| A la vez | Peticiones | Errores | Latencia p50 (ms) | p95 (ms) | Máx. (ms) | Peticiones/s | Memoria máx. (MB) |
|---|---|---|---|---|---|---|---|
| 1 | 40 | 0 | 65.6 | 189.6 | 224.3 | 13.6 | 587.5 |
| 2 | 40 | 0 | 64.8 | 213.8 | 224.0 | 26.9 | 623.2 |
| 4 | 40 | 0 | 134.0 | 169.1 | 181.0 | 28.5 | 692.7 |
| 8 | 80 | 0 | 240.8 | 263.6 | 282.4 | 32.8 | 836.3 |
| 16 | 160 | 0 | 511.2 | 572.3 | 621.4 | 30.8 | 1132.5 |

## Planes chicos simulados (docker run --memory --cpus)

| Plan | Arranque | Estado | Latencia p50, 1 a la vez (ms) | p50, 4 a la vez (ms) | Pet/s con 4 | Memoria máx. (MB) | Estado al final | Nota |
|---|---|---|---|---|---|---|---|---|
| 256 MB, 1 CPU | 4.3 s | se detuvo (sin memoria, OOMKilled) | — | — | — | 146.6 | — |  |
| 512 MB, 1 CPU | 5.4 s | healthy | 503.8 | 792.1 | 4.9 | 416.5 | running, healthy |  |
| 512 MB, 0.5 CPU | 12.8 s | healthy | 1578.3 | 1897.6 | 1.9 | 412.7 | running, healthy |  |
| 512 MB, 0.5 CPU, 1 hilo | 12.9 s | healthy | 96.9 | 700.4 | 4.9 | 397.6 | running, healthy |  |
| 512 MB, 0.1 CPU | 149.6 s | healthy | 74292.6 | — | — | 378.5 | running, unhealthy | demasiado lento: solo 3 peticiones en serie, sin prueba en paralelo |
| 512 MB, 0.1 CPU, 1 hilo | 182.3 s | healthy | 602.3 | 5796.0 | 0.7 | 414.9 | running, healthy |  |
| 1 GB, 1 CPU | 5.3 s | healthy | 458.6 | 1388.4 | 2.9 | 422.2 | running, healthy |  |
