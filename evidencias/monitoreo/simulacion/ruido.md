# Reducción de ruido

| Regla ingenua | Episodios | Avisos (con recordatorio cada minuto) | Minutos activa |
|---|---|---|---|
| Algún error 5xx en el último minuto | 1 | 2 | 2.0 |
| Latencia p95 > 0.5 s en el último minuto | 1 | 3 | 2.2 |
| Algún diagnóstico tardó más de 1 s | 1 | 3 | 2.2 |
| CPU > 80 % del límite | 0 | 0 | 0.0 |
| Memoria > 60 % del límite | 2 | 2 | 1.0 |
| La API no responde | 2 | 2 | 0.8 |
| PSI de alguna variable de entrada > 0.1 | 6 | 33 | 30.2 |
| PSI de P(enferma) > 0.1 | 3 | 16 | 15.0 |
| La sonda no cumple | 1 | 11 | 11.0 |
| **Total** | 17 | 72 | |

Diseño basado en SLO: 6 páginas (incluye 1 escalamiento), 4 tickets y 1 de solo registro; alertas inhibidas en algún momento: DistribucionPrediccionesCambio, DriftDatosAdvertencia, LatenciaQuemaMedia.
