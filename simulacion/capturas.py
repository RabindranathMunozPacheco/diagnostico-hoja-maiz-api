"""Capturas de pantalla de las herramientas con Edge sin ventana (headless), para la evidencia de implementación.

Uso:
    python simulacion/capturas.py URL salida.png [--ancho 1800] [--alto 2400] [--espera 20000]
Desde otro script: from capturas import capturar
"""
import argparse
import subprocess
import tempfile
from pathlib import Path

EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"


def capturar(url, salida, ancho=1800, alto=2400, espera_ms=20000):
    """Abre la página, deja correr el tiempo virtual (para que carguen los datos) y guarda la captura."""
    salida = Path(salida)
    salida.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as perfil:            # perfil limpio: sin sesiones ni extensiones
        subprocess.run([EDGE, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
                        f"--user-data-dir={perfil}", f"--window-size={ancho},{alto}",
                        f"--virtual-time-budget={espera_ms}", "--force-device-scale-factor=1",
                        f"--screenshot={salida.resolve()}", url], check=True, capture_output=True, timeout=180)
    return salida


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("url")
    p.add_argument("salida")
    p.add_argument("--ancho", type=int, default=1800)
    p.add_argument("--alto", type=int, default=2400)
    p.add_argument("--espera", type=int, default=20000)
    a = p.parse_args()
    print(capturar(a.url, a.salida, a.ancho, a.alto, a.espera))
