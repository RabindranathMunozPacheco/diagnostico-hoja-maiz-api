# Imagen del servicio de diagnóstico de hoja de maíz: API (FastAPI) + página web + modelo.
# Construir:  docker build -t diagnostico-hoja-maiz-api:0.3.0 .
# Ejecutar:   docker run --rm -p 8000:8000 diagnostico-hoja-maiz-api:0.3.0
FROM python:3.10-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HF_HOME=/opt/hf

WORKDIR /app

# 1. Dependencias primero: esta capa solo se reconstruye si cambian requirements.txt o constraints.txt
COPY requirements.txt constraints.txt ./
RUN pip install -r requirements.txt -c constraints.txt

# 2. El servicio no corre como root. El usuario se crea antes de descargar el extractor para que la carpeta
#    ya sea suya (un chown -R después copiaría otra vez los archivos en una capa nueva)
RUN useradd --create-home --uid 1000 servicio && mkdir -p "$HF_HOME" && chown servicio "$HF_HOME"
USER servicio

# 3. El extractor (MobileNetV2) se descarga al construir, así el contenedor no necesita internet
COPY modelo/ficha_modelo.json modelo/
COPY scripts/descargar_extractor.py scripts/
RUN python scripts/descargar_extractor.py
ENV HF_HUB_OFFLINE=1

# 4. Código, página web y clasificador (de solo lectura para el servicio: los copia root)
COPY app/ app/
COPY frontend/ frontend/
COPY modelo/ modelo/

# Valores por defecto de la configuración (se pueden cambiar con -e o con docker-compose.yml)
ENV UMBRAL_ENFERMA=0.2494 \
    TAMANO_MAX_MB=10

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/salud', timeout=4)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
