"""Comprueba que la imagen Docker está bien construida y que el contenedor funciona en local.

Cada comprobación usa un contenedor nuevo y desechable (no toca el contenedor principal ni el puerto 8000):
    1. Imagen: usuario sin privilegios, puerto, HEALTHCHECK y configuración por defecto
    2. Dependencias dentro de la imagen: Python 3.10, las mismas versiones de requirements.txt y sin conflictos
    3. Arranque: llega a healthy y responde la página, /api/salud, /api/modelo y un diagnóstico igual al de local
    4. Sin internet (--network none): arranca y diagnostica igual, porque el extractor viene dentro de la imagen
    5. Configuración por variable de entorno: con UMBRAL_ENFERMA=0.40 cambia el diagnóstico de la foto límite
    6. Configuración inválida: con UMBRAL_ENFERMA=2 el contenedor no arranca y el log dice por qué
    7. Reinicio: después de docker restart vuelve a healthy y responde

Deja el resultado en evidencias/contenedor/verificacion.md y termina con código 1 si algo falla.
Requisitos: Docker corriendo, la imagen ya construida (docker build) y pip install -r requirements-dev.txt.

Uso:
    python pruebas/verificar_contenedor.py
    python pruebas/verificar_contenedor.py --imagen diagnostico-hoja-maiz-api:0.2.0
"""
import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

import httpx

from probar_api import EJEMPLOS, RAIZ, foto

FOTOS = {e["id"]: e for e in EJEMPLOS["fotos"]}
REFERENCIA_LOCAL = RAIZ / "evidencias" / "local" / "respuestas"   # respuestas de la API corriendo sin Docker

# Cliente mínimo que corre DENTRO del contenedor sin red (solo biblioteca estándar)
CLIENTE_INTERNO = """
import sys, urllib.request, uuid
datos = open(sys.argv[1], "rb").read()
b = uuid.uuid4().hex
cuerpo = (f'--{b}\\r\\nContent-Disposition: form-data; name="foto"; filename="foto.jpg"\\r\\n'
          f'Content-Type: image/jpeg\\r\\n\\r\\n').encode() + datos + f'\\r\\n--{b}--\\r\\n'.encode()
peticion = urllib.request.Request("http://localhost:8000/api/diagnostico", data=cuerpo,
                                  headers={"Content-Type": f"multipart/form-data; boundary={b}"})
print(urllib.request.urlopen(peticion, timeout=60).read().decode())
"""
SIN_RED = "import socket; socket.create_connection(('huggingface.co', 443), timeout=5)"


class Verificador:
    def __init__(self, docker, imagen):
        self.docker, self.imagen = docker, imagen
        self.filas, self.contenedores = [], []

    def cmd(self, *args, revisar=True):
        r = subprocess.run([self.docker, *args], capture_output=True, text=True, encoding="utf-8", errors="replace")
        if revisar and r.returncode != 0:
            raise RuntimeError(f"docker {' '.join(args[:2])} falló: {r.stderr.strip()}")
        return r

    def anotar(self, numero, que, esperado, obtenido, ok):
        self.filas.append((numero, que, esperado, obtenido, "OK" if ok else "FALLA"))
        print(f"  [{'OK' if ok else 'FALLA':5}] {numero}. {que}: {obtenido}")

    def arrancar(self, nombre, *opciones):
        self.cmd("rm", "-f", nombre, revisar=False)
        self.cmd("run", "-d", "--name", nombre, *opciones, self.imagen)
        self.contenedores.append(nombre)

    def esperar_salud(self, nombre, limite=180):
        """Segundos hasta healthy, o None si el contenedor se detiene o no llega a tiempo."""
        inicio = time.perf_counter()
        while time.perf_counter() - inicio < limite:
            estado = self.cmd("inspect", "-f", "{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{end}}",
                              nombre).stdout.split()
            if estado[-1] == "healthy":
                return round(time.perf_counter() - inicio, 1)
            if estado[0] in ("exited", "dead"):
                return None
            time.sleep(1)
        return None

    def limpiar(self):
        for nombre in self.contenedores:
            self.cmd("rm", "-f", nombre, revisar=False)


def igual_a_local(respuesta, id_foto):
    ref = json.loads((REFERENCIA_LOCAL / f"diagnostico_{id_foto}.json").read_text(encoding="utf-8"))
    return (respuesta["diagnostico"] == ref["diagnostico"]
            and all(abs(respuesta["probabilidades"][c] - p) < 1e-4 for c, p in ref["probabilidades"].items()))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--imagen", default="diagnostico-hoja-maiz-api:0.2.0")
    parser.add_argument("--docker", default=shutil.which("docker") or "docker")
    parser.add_argument("--salida", type=Path, default=RAIZ / "evidencias" / "contenedor" / "verificacion.md")
    args = parser.parse_args()
    v = Verificador(args.docker, args.imagen)
    roya, limite = foto(FOTOS["m0005"]), foto(FOTOS["m0859"])   # roya clara y la sana que el umbral marca enferma
    print(f"Verificando {args.imagen}")

    try:
        # 1. La imagen
        cfg = json.loads(v.cmd("image", "inspect", args.imagen, "--format", "{{json .Config}}").stdout)
        id_imagen = v.cmd("image", "inspect", args.imagen, "--format", "{{.Id}}").stdout.strip()
        entorno = dict(e.split("=", 1) for e in cfg["Env"])
        hc = " ".join(cfg.get("Healthcheck", {}).get("Test", []))
        v.anotar(1, "Usuario del proceso", "servicio (no root)", cfg["User"], cfg["User"] == "servicio")
        v.anotar(1, "Puerto expuesto", "8000/tcp", ", ".join(cfg["ExposedPorts"]), "8000/tcp" in cfg["ExposedPorts"])
        v.anotar(1, "HEALTHCHECK", "llama a /api/salud", "sí" if "/api/salud" in hc else "no", "/api/salud" in hc)
        v.anotar(1, "Configuración por defecto", "UMBRAL_ENFERMA=0.2494, HF_HUB_OFFLINE=1",
                 f"UMBRAL_ENFERMA={entorno.get('UMBRAL_ENFERMA')}, HF_HUB_OFFLINE={entorno.get('HF_HUB_OFFLINE')}",
                 entorno.get("UMBRAL_ENFERMA") == "0.2494" and entorno.get("HF_HUB_OFFLINE") == "1")

        # 2. Dependencias dentro de la imagen
        fijadas = dict(re.findall(r"^([A-Za-z0-9_.-]+)==(\S+)", (RAIZ / "requirements.txt").read_text(encoding="utf-8"),
                                  re.M))
        consulta = ("import json, sys, importlib.metadata as m; "
                    f"print(json.dumps({{'python': sys.version.split()[0], **{{p: m.version(p) for p in {list(fijadas)}}}}}))")
        dentro = json.loads(v.cmd("run", "--rm", args.imagen, "python", "-c", consulta).stdout)
        distintas = [f"{p} {fijadas[p]} → {dentro[p]}" for p in fijadas if dentro[p] != fijadas[p]]
        v.anotar(2, "Versión de Python", "3.10.x", dentro["python"], dentro["python"].startswith("3.10."))
        v.anotar(2, "Versiones de requirements.txt", f"las {len(fijadas)} iguales",
                 "todas iguales" if not distintas else "; ".join(distintas), not distintas)
        pip_check = v.cmd("run", "--rm", args.imagen, "pip", "check", revisar=False)
        v.anotar(2, "Conflictos entre dependencias (pip check)", "ninguno", pip_check.stdout.strip(),
                 pip_check.returncode == 0)

        # 3. Arranque normal
        v.arrancar("verif-normal", "-p", "8101:8000")
        segundos = v.esperar_salud("verif-normal")
        v.anotar(3, "Llega a healthy", "sí", f"en {segundos} s" if segundos else "no", segundos is not None)
        with httpx.Client(base_url="http://localhost:8101", timeout=60) as http:
            pagina = http.get("/")
            v.anotar(3, "Página web (GET /)", "200 con HTML", f"{pagina.status_code}, {pagina.headers['content-type']}",
                     pagina.status_code == 200 and "text/html" in pagina.headers["content-type"])
            salud = http.get("/api/salud").json()
            v.anotar(3, "GET /api/salud", "estado ok", json.dumps(salud, ensure_ascii=False), salud["estado"] == "ok")
            ficha = http.get("/api/modelo").json()
            v.anotar(3, "GET /api/modelo", "clasificador-hoja-maiz v1, umbral 0.2494",
                     f"{ficha['nombre']} v{ficha['version_registrada']}, umbral {ficha['umbral_en_uso']}",
                     ficha["umbral_en_uso"] == 0.2494)
            d = http.post("/api/diagnostico", files={"foto": ("m0005.jpg", roya, "image/jpeg")}).json()
            v.anotar(3, "Diagnóstico de m0005 igual que en local", "Roya común, mismas probabilidades",
                     f"{d['diagnostico']}, P(enferma) {d['probabilidad_enferma']}", igual_a_local(d, "m0005"))

        # 4. Sin internet
        v.arrancar("verif-sin-red", "--network", "none")
        segundos = v.esperar_salud("verif-sin-red")
        sin_red = v.cmd("exec", "verif-sin-red", "python", "-c", SIN_RED, revisar=False)
        v.anotar(4, "El contenedor no tiene salida a internet", "no puede conectarse a huggingface.co",
                 "no se conecta" if sin_red.returncode != 0 else "sí se conecta", sin_red.returncode != 0)
        v.anotar(4, "Llega a healthy sin internet", "sí", f"en {segundos} s" if segundos else "no", segundos is not None)
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "cliente.py").write_text(CLIENTE_INTERNO, encoding="utf-8")
            (Path(tmp) / "foto.jpg").write_bytes(roya)
            for archivo in ("cliente.py", "foto.jpg"):
                v.cmd("cp", str(Path(tmp) / archivo), f"verif-sin-red:/tmp/{archivo}")
        d = json.loads(v.cmd("exec", "verif-sin-red", "python", "/tmp/cliente.py", "/tmp/foto.jpg").stdout)
        v.anotar(4, "Diagnóstico de m0005 sin internet", "igual que en local",
                 f"{d['diagnostico']}, P(enferma) {d['probabilidad_enferma']}", igual_a_local(d, "m0005"))

        # 5. Configuración por variable de entorno, sin reconstruir la imagen
        v.arrancar("verif-umbral", "-p", "8103:8000", "-e", "UMBRAL_ENFERMA=0.40")
        v.esperar_salud("verif-umbral")
        with httpx.Client(base_url="http://localhost:8103", timeout=60) as http:
            ficha = http.get("/api/modelo").json()
            v.anotar(5, "Umbral en uso con -e UMBRAL_ENFERMA=0.40", "0.4", str(ficha["umbral_en_uso"]),
                     ficha["umbral_en_uso"] == 0.4)
            d = http.post("/api/diagnostico", files={"foto": ("m0859.jpg", limite, "image/jpeg")}).json()
            v.anotar(5, "m0859 (P(enferma) 0.38) con umbral 0.40", "Sana (con 0.2494 sale Mancha gris)",
                     f"{d['diagnostico']}, P(enferma) {d['probabilidad_enferma']}", d["diagnostico"] == "Sana")

        # 6. Configuración inválida: debe fallar al arrancar, no responder con un umbral absurdo
        v.arrancar("verif-invalido", "-e", "UMBRAL_ENFERMA=2")
        v.esperar_salud("verif-invalido", limite=60)
        estado = v.cmd("inspect", "-f", "{{.State.Status}} {{.State.ExitCode}}", "verif-invalido").stdout.split()
        r = v.cmd("logs", "verif-invalido", revisar=False)
        mensaje = next((l.strip() for l in (r.stdout + r.stderr).splitlines() if l.startswith("ValueError:")), "")
        v.anotar(6, "Arranque con UMBRAL_ENFERMA=2", "se detiene y el log explica el error",
                 f"{estado[0]} (código {estado[1]}): {mensaje}", estado[0] == "exited" and bool(mensaje))

        # 7. Reinicio
        v.cmd("restart", "verif-normal")
        segundos = v.esperar_salud("verif-normal")
        salud = httpx.get("http://localhost:8101/api/salud", timeout=30).json() if segundos else {}
        v.anotar(7, "docker restart", "vuelve a healthy y responde",
                 f"healthy en {segundos} s, estado {salud.get('estado')}" if segundos else "no vuelve",
                 salud.get("estado") == "ok")
    finally:
        v.limpiar()

    version = v.cmd("version", "--format", "{{.Server.Version}}", revisar=False).stdout.strip()
    md = ["# Verificación del contenedor", "",
          f"- Fecha: {datetime.now():%Y-%m-%d %H:%M}",
          f"- Imagen: `{args.imagen}` (`{id_imagen[:19]}`)",
          f"- Motor de Docker: {version}",
          "- Generado con `python pruebas/verificar_contenedor.py`. Cada comprobación usa un contenedor desechable.", "",
          "| # | Comprobación | Esperado | Obtenido | Resultado |", "|---|---|---|---|---|",
          *[f"| {n} | {q} | {e} | {o} | {r} |" for n, q, e, o, r in v.filas], "",
          "## Versiones dentro de la imagen", "", "| Paquete | requirements.txt | Imagen |", "|---|---|---|",
          *[f"| {p} | {fijadas[p]} | {dentro[p]} |" for p in fijadas], ""]
    args.salida.write_text("\n".join(md), encoding="utf-8")
    fallas = sum(f[-1] == "FALLA" for f in v.filas)
    print(f"{len(v.filas) - fallas} de {len(v.filas)} comprobaciones OK. Resultado en {args.salida}")
    return 1 if fallas else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
