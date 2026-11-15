"""Características sencillas de la foto para vigilar si lo que llega se parece a lo que vio el modelo al entrenar.

Se calculan sobre la misma imagen RGB de 224x224 que entra al modelo, así que no dependen del tamaño original:
    brillo     luminancia media (0 a 255). Baja con fotos oscuras (nublado, atardecer, sombra).
    contraste  desviación estándar de la luminancia. Baja con fotos oscuras, con neblina o lavadas.
    nitidez    varianza del laplaciano. Baja mucho con fotos movidas o desenfocadas.
    verdor     proporción de píxeles donde el verde domina. Casi no cambia con la luz; sirve para notar fotos
               que no son de una hoja (una pared, el suelo, una imagen en blanco).

Los BINS son fijos (no dependen de la versión del modelo) para que los histogramas de Prometheus no cambien
al cambiar de versión: la línea base de cada versión guarda qué proporción de sus fotos de entrenamiento
cae en cada bin, y el monitor de drift compara contra esas proporciones (PSI y KS).
"""
import numpy as np

# Límites superiores de cada bin (el último bin va hasta +infinito), elegidos con la distribución de las
# 857 fotos de entrenamiento: más bins donde se concentran los datos.
BINS = {
    "brillo": (40, 60, 80, 90, 100, 110, 120, 130, 140, 160),
    "contraste": (15, 20, 25, 30, 35, 40, 45, 50, 55, 60, 70),
    "nitidez": (10, 30, 100, 200, 300, 450, 600, 800, 1100, 1600, 2500, 5000),
    "verdor": (0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95),
}
VARIABLES = tuple(BINS)


def calidad(imagen):
    """Diccionario con las cuatro características de una imagen PIL RGB."""
    rgb = np.asarray(imagen, dtype=np.float32)
    luz = np.asarray(imagen.convert("L"), dtype=np.float32)
    laplaciano = luz[:-2, 1:-1] + luz[2:, 1:-1] + luz[1:-1, :-2] + luz[1:-1, 2:] - 4 * luz[1:-1, 1:-1]
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    return {
        "brillo": float(luz.mean()),
        "contraste": float(luz.std()),
        "nitidez": float(laplaciano.var()),
        "verdor": float(np.mean((g > r) & (g > b))),
    }


def proporciones_por_bin(valores, variable):
    """Proporción de valores en cada bin (len(BINS[variable]) + 1 bins). Mismo criterio que Prometheus: x <= límite."""
    limites = np.asarray(BINS[variable], dtype=float)
    indices = np.searchsorted(limites, np.asarray(valores, dtype=float), side="left")
    conteos = np.bincount(indices, minlength=len(limites) + 1)
    return conteos / max(conteos.sum(), 1)


def fuera_de_rango(valores, linea_base):
    """Variables cuyo valor queda fuera del rango del 99 % central de la línea base (percentiles 0.5 y 99.5)."""
    if not linea_base:
        return []
    fuera = []
    for variable, valor in valores.items():
        rango = linea_base["variables"].get(variable, {}).get("rango_99")
        if rango and not rango[0] <= valor <= rango[1]:
            fuera.append(variable)
    return fuera
