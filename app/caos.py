"""Inyección de fallas para simular incidentes (ingeniería del caos, como Gremlin o AWS FIS pero dentro del servicio).

Solo funciona si el contenedor arranca con CAOS_HABILITADO=1 (el docker-compose de monitoreo lo activa para
el laboratorio; en producción va apagado) y se controla con POST/DELETE /api/admin/fallas usando el token de
administración. Una falla afecta una etapa del diagnóstico durante cierto tiempo:
    tasa         proporción de solicitudes que fallan con error 500 en esa etapa (0 a 1)
    latencia_ms  espera agregada a cada solicitud en esa etapa

El generador aleatorio lleva semilla fija para que la misma simulación falle en las mismas solicitudes.
"""
import random
import threading
import time
from dataclasses import asdict, dataclass

ETAPAS = ("abrir_imagen", "calidad_imagen", "extraer_caracteristicas", "clasificar")


class FallaInyectada(RuntimeError):
    """Error provocado a propósito por la simulación."""


@dataclass
class Falla:
    etapa: str
    tasa: float
    latencia_ms: int
    duracion_s: int
    inicio: float

    @property
    def vigente(self):
        return time.time() < self.inicio + self.duracion_s

    def describir(self):
        return {**asdict(self), "termina": self.inicio + self.duracion_s}


class Caos:
    def __init__(self, habilitado, semilla=42):
        self.habilitado = habilitado
        self.falla = None
        self._azar = random.Random(semilla)
        self._candado = threading.Lock()

    def activar(self, etapa, tasa=0.0, latencia_ms=0, duracion_s=300):
        if not self.habilitado:
            raise PermissionError("la inyección de fallas está deshabilitada (CAOS_HABILITADO=0)")
        if etapa not in ETAPAS:
            raise ValueError(f"etapa desconocida: {etapa}; opciones: {', '.join(ETAPAS)}")
        if not 0 <= tasa <= 1 or latencia_ms < 0 or duracion_s <= 0:
            raise ValueError("tasa debe estar entre 0 y 1, latencia_ms >= 0 y duracion_s > 0")
        self.falla = Falla(etapa, float(tasa), int(latencia_ms), int(duracion_s), time.time())
        return self.falla

    def desactivar(self):
        anterior, self.falla = self.falla, None
        return anterior

    def activa(self):
        if self.falla is not None and not self.falla.vigente:
            self.falla = None
        return self.falla

    def aplicar(self, etapa):
        falla = self.activa()
        if falla is None or falla.etapa != etapa:
            return
        if falla.latencia_ms:
            time.sleep(falla.latencia_ms / 1000)
        with self._candado:
            sorteo = self._azar.random()
        if sorteo < falla.tasa:
            raise FallaInyectada(f"falla inyectada en {etapa} (tasa {falla.tasa:g})")
