# Verificación del contenedor

- Fecha: 2026-11-01 17:35
- Imagen: `diagnostico-hoja-maiz-api:0.3.0` (`sha256:fc422577bd02`)
- Motor de Docker: 29.9.2
- Generado con `python pruebas/verificar_contenedor.py`. Cada comprobación usa un contenedor desechable.

| # | Comprobación | Esperado | Obtenido | Resultado |
|---|---|---|---|---|
| 1 | Usuario del proceso | servicio (no root) | servicio | OK |
| 1 | Puerto expuesto | 8000/tcp | 8000/tcp | OK |
| 1 | HEALTHCHECK | llama a /api/salud | sí | OK |
| 1 | Configuración por defecto | UMBRAL_ENFERMA=0.2494, HF_HUB_OFFLINE=1 | UMBRAL_ENFERMA=0.2494, HF_HUB_OFFLINE=1 | OK |
| 2 | Versión de Python | 3.10.x | 3.10.22 | OK |
| 2 | Versiones de requirements.txt | las 14 iguales | todas iguales | OK |
| 2 | Conflictos entre dependencias (pip check) | ninguno | No broken requirements found. | OK |
| 3 | Llega a healthy | sí | en 5.3 s | OK |
| 3 | Página web (GET /) | 200 con HTML | 200, text/html; charset=utf-8 | OK |
| 3 | GET /api/salud | estado ok | {"estado": "ok", "modelo": "clasificador-hoja-maiz v1", "version_api": "0.3.0"} | OK |
| 3 | GET /api/modelo | clasificador-hoja-maiz v1, umbral 0.2494 | clasificador-hoja-maiz v1, umbral 0.2494 | OK |
| 3 | Diagnóstico de m0005 igual que en local | Roya común, mismas probabilidades | Roya común, P(enferma) 0.9992 | OK |
| 4 | El contenedor no tiene salida a internet | no puede conectarse a huggingface.co | no se conecta | OK |
| 4 | Llega a healthy sin internet | sí | en 5.3 s | OK |
| 4 | Diagnóstico de m0005 sin internet | igual que en local | Roya común, P(enferma) 0.9992 | OK |
| 5 | Umbral en uso con -e UMBRAL_ENFERMA=0.40 | 0.4 | 0.4 | OK |
| 5 | m0859 (P(enferma) 0.38) con umbral 0.40 | Sana (con 0.2494 sale Mancha gris) | Sana, P(enferma) 0.3801 | OK |
| 6 | Arranque con UMBRAL_ENFERMA=2 | se detiene y el log explica el error | exited (código 1): ValueError: UMBRAL_ENFERMA debe estar entre 0 y 1 (se recibió 2.0) | OK |
| 7 | docker restart | vuelve a healthy y responde | healthy en 5.3 s, estado ok | OK |

## Versiones dentro de la imagen

| Paquete | requirements.txt | Imagen |
|---|---|---|
| torch | 2.13.0+cpu | 2.13.0+cpu |
| transformers | 5.20.1 | 5.20.1 |
| huggingface-hub | 1.33.0 | 1.33.0 |
| scikit-learn | 1.7.2 | 1.7.2 |
| skops | 0.16.0 | 0.16.0 |
| numpy | 2.2.6 | 2.2.6 |
| pandas | 2.3.3 | 2.3.3 |
| scipy | 1.15.3 | 1.15.3 |
| Pillow | 12.3.0 | 12.3.0 |
| fastapi | 0.146.0 | 0.146.0 |
| starlette | 1.7.0 | 1.7.0 |
| pydantic | 2.13.4 | 2.13.4 |
| uvicorn | 0.54.0 | 0.54.0 |
| python-multipart | 0.0.32 | 0.0.32 |
