# Imagen de los servicios de monitoreo: el vigilante (drift y sonda) y operaciones (respuesta automatizada).
# La misma imagen corre uno u otro según el comando (ver monitoreo/docker-compose.yml).
FROM python:3.10-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /servicios
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY comun.py vigilante.py operaciones.py ./

# Corre como root solo porque operaciones usa el socket de Docker para reiniciar y reconfigurar el contenedor de
# la API (en producción eso lo haría el orquestador, p. ej. Kubernetes, con permisos acotados).
CMD ["python", "vigilante.py"]
