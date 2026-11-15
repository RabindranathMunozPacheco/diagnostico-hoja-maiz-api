"""Configuración de las pruebas: la API se importa con administración e inyección de fallas habilitadas."""
import os

os.environ.setdefault("TOKEN_ADMIN", "token-de-prueba")
os.environ.setdefault("CAOS_HABILITADO", "1")
os.environ.setdefault("ENTORNO", "pruebas")
