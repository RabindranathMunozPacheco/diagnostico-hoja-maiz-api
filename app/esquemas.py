"""Forma de las respuestas de la API (también aparecen documentadas en /docs)."""
from pydantic import BaseModel, Field


class Salud(BaseModel):
    estado: str = Field(examples=["ok"])
    modelo: str = Field(examples=["clasificador-hoja-maiz v1"])
    version_api: str = Field(examples=["0.3.0"])


class ImagenRecibida(BaseModel):
    formato: str | None = Field(examples=["JPEG"])
    modo: str = Field(examples=["RGB"])
    ancho: int = Field(examples=[224])
    alto: int = Field(examples=[224])


class Diagnostico(BaseModel):
    archivo: str | None = Field(examples=["hoja.jpg"])
    diagnostico: str = Field(description="Enfermedad detectada o 'Sana'", examples=["Mancha gris"])
    estado: str = Field(description="'enferma' o 'sana'", examples=["enferma"])
    probabilidad_enferma: float = Field(description="1 - P(Sana)", examples=[0.93])
    umbral_enferma: float = Field(description="Si probabilidad_enferma >= umbral, se reporta enferma",
                                  examples=[0.2494])
    clase_mas_probable: str = Field(examples=["Mancha gris"])
    decidido_por_umbral: bool = Field(description="True si el umbral cambió la clase más probable (Sana) a enferma")
    probabilidades: dict[str, float] = Field(examples=[{"Mancha gris": 0.81, "Tizón": 0.1,
                                                        "Sana": 0.07, "Roya común": 0.02}])
    imagen: ImagenRecibida
    tiempo_ms: float = Field(description="Tiempo de preparación e inferencia", examples=[45.2])


class Error(BaseModel):
    detail: str = Field(examples=["el archivo no es una imagen válida"])
