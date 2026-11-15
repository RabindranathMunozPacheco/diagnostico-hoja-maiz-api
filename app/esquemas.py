"""Forma de las respuestas de la API (también aparecen documentadas en /docs)."""
from pydantic import BaseModel, Field


class Salud(BaseModel):
    estado: str = Field(examples=["ok"])
    modelo: str = Field(examples=["clasificador-hoja-maiz v1"])
    modelo_version: str = Field(examples=["1"])
    alias: str = Field(description="Alias del registro que sirve la API", examples=["champion"])
    version_api: str = Field(examples=["0.4.0"])
    falla_inyectada: dict | None = Field(None, description="Falla activa (solo en el entorno de simulación)")


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
    calidad_imagen: dict[str, float] = Field(description="Brillo, contraste, nitidez y verdor de la foto",
                                             examples=[{"brillo": 114.2, "contraste": 46.0, "nitidez": 720.5,
                                                        "verdor": 0.73}])
    fuera_de_rango: list[str] = Field(description="Características fuera del 99 % central del entrenamiento",
                                      examples=[[]])
    advertencias: list[str] = Field(description="Avisos para el usuario sobre la calidad de la foto", examples=[[]])
    modelo_version: str = Field(examples=["1"])
    tiempo_ms: float = Field(description="Tiempo de preparación e inferencia", examples=[45.2])


class Error(BaseModel):
    detail: str = Field(examples=["el archivo no es una imagen válida"])


class FallaPedida(BaseModel):
    etapa: str = Field(examples=["extraer_caracteristicas"])
    tasa: float = Field(0.0, ge=0, le=1, description="Proporción de solicitudes que fallan con 500")
    latencia_ms: int = Field(0, ge=0, description="Espera agregada en la etapa")
    duracion_s: int = Field(300, gt=0, le=3600)
