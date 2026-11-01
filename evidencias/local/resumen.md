# Evidencia de integración: http://127.0.0.1:8000

- Servicio: ok, modelo clasificador-hoja-maiz v1, API 0.1.0
- Umbral en uso: P(enferma) >= 0.2494

## Endpoints publicados (OpenAPI)

| Método | Ruta | Descripción |
|---|---|---|
| `GET` | `/api/salud` | Estado del servicio |
| `GET` | `/api/modelo` | Ficha del modelo en uso |
| `POST` | `/api/diagnostico` | Diagnosticar la foto de una hoja |

## Diagnósticos de las fotos de ejemplo

Primeras 2 fotos de cada clase de la prueba de la Actividad 5 (el modelo no las vio al entrenar). Sirven para comprobar que la API responde con fotos reales; no son una evaluación del modelo.

| Foto | Clase real | Diagnóstico | P(enferma) | ms | ¿Lo decidió el umbral? | ¿Acierta sana/enferma? | ¿Acierta la clase? |
|---|---|---|---|---|---|---|---|
| m0243 | Mancha gris | Mancha gris | 1.000 | 34 | no | sí | sí |
| m0247 | Mancha gris | Mancha gris | 0.995 | 40 | no | sí | sí |
| m0000 | Roya común | Mancha gris | 0.896 | 46 | no | sí | no |
| m0005 | Roya común | Roya común | 0.999 | 35 | no | sí | sí |
| m0855 | Sana | Mancha gris | 0.914 | 40 | no | no | no |
| m0859 | Sana | Mancha gris | 0.380 | 29 | sí | no | no |
| m0111 | Tizón | Mancha gris | 1.000 | 34 | no | sí | no |
| m0112 | Tizón | Mancha gris | 1.000 | 30 | no | sí | no |

## Todas las peticiones

| Petición | Método | Endpoint | HTTP | ms | Resultado |
|---|---|---|---|---|---|
| openapi | GET | `/openapi.json` | 200 | 16.4 | 3 endpoints |
| salud | GET | `/api/salud` | 200 | 2.5 | ok |
| modelo | GET | `/api/modelo` | 200 | 1.9 | clasificador-hoja-maiz v1 |
| diagnostico_m0243 | POST | `/api/diagnostico` | 200 | 37.5 | Mancha gris |
| diagnostico_m0247 | POST | `/api/diagnostico` | 200 | 44.3 | Mancha gris |
| diagnostico_m0000 | POST | `/api/diagnostico` | 200 | 49.9 | Mancha gris |
| diagnostico_m0005 | POST | `/api/diagnostico` | 200 | 39.3 | Roya común |
| diagnostico_m0855 | POST | `/api/diagnostico` | 200 | 43.4 | Mancha gris |
| diagnostico_m0859 | POST | `/api/diagnostico` | 200 | 33.1 | Mancha gris |
| diagnostico_m0111 | POST | `/api/diagnostico` | 200 | 37.7 | Mancha gris |
| diagnostico_m0112 | POST | `/api/diagnostico` | 200 | 33.3 | Mancha gris |
| error_no_es_imagen | POST | `/api/diagnostico` | 415 | 3.3 | el archivo no es una imagen válida |
| error_muy_grande | POST | `/api/diagnostico` | 413 | 58.2 | la foto pesa más de 10 MB |
