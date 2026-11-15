# Reentrenamiento: v3 candidata contra v1 en servicio

Umbral recalibrado: 0.209 (fuera de fold: 4.9% enfermas como sanas, 62.4% sanas como enfermas)

| Versión | Conjunto | F1 macro | Exactitud | Enfermas como sanas | Sanas como enfermas |
|---|---|---|---|---|---|
| v1 (en servicio) | golden (40) | 0.730 | 0.725 | 6.7% | 60.0% |
| v1 (en servicio) | adverso (40) | 0.250 | 0.325 | 30.0% | 30.0% |
| v1 (en servicio) | prueba (221) | 0.721 | 0.751 | 8.0% | 55.6% |
| v1 (en servicio) | prueba_lluvia (221) | 0.364 | 0.453 | 31.8% | 31.1% |
| candidata | golden (40) | 0.623 | 0.625 | 3.3% | 50.0% |
| candidata | adverso (40) | 0.495 | 0.500 | 3.3% | 80.0% |
| candidata | prueba (221) | 0.658 | 0.710 | 5.7% | 66.7% |
| candidata | prueba_lluvia (221) | 0.539 | 0.642 | 4.5% | 73.3% |
