| Réplicas | Concurrencia | Diag./s | p95 (s) | p99 (s) | Errores |
|---|---|---|---|---|---|
| 1 | 2 | 58.24 | 0.0579 | 0.0845 | 0 |
| 2 | 4 | 102.5 | 0.0779 | 0.0975 | 0 |
| 3 | 6 | 128.99 | 0.1133 | 0.159 | 0 |

| Réplicas | Después de matar una: respondidas | con error |
|---|---|---|
| 1 | 3 | 114 |
| 3 | 2311 | 0 |

Rollback v3 -> v1 con 3 réplicas: antes {'3': 90}; una llamada al balanceador {'3': 60, '1': 30}; llamando a cada réplica {'1': 90}
