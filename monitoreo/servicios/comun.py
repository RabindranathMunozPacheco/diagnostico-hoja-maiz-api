"""Funciones compartidas por el vigilante, operaciones y los scripts de despliegue: Prometheus, registro y bitácora."""
import json
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path

import httpx

PROMETHEUS = os.environ.get("PROMETHEUS_URL", "http://prometheus:9090")
API = os.environ.get("API_URL", "http://api:8000")
DIR_MODELOS = Path(os.environ.get("DIR_MODELOS", "/datos/modelo"))
_candado_registro = threading.Lock()


def ahora():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


# --------------------------------------------------------------------------------------------- Prometheus
def consulta(expr, url=None):
    r = httpx.get(f"{url or PROMETHEUS}/api/v1/query", params={"query": expr}, timeout=10)
    r.raise_for_status()
    return r.json()["data"]["result"]


def escalar(expr, url=None):
    """Primer valor de una consulta, o None si no hay datos."""
    resultado = consulta(expr, url)
    if not resultado:
        return None
    valor = float(resultado[0]["value"][1])
    return None if valor != valor else valor           # NaN -> None


def histograma(metrica, filtro, ventana, url=None):
    """Conteos por bin de un histograma de Prometheus en la ventana: lista ordenada [(limite, conteo)]."""
    resultado = consulta(f'sum by (le) (increase({metrica}_bucket{{{filtro}}}[{ventana}]))', url)
    acumulados = sorted(((float(r["metric"]["le"]), float(r["value"][1])) for r in resultado), key=lambda x: x[0])
    conteos, anterior = [], 0.0
    for limite, acumulado in acumulados:
        conteos.append((limite, max(acumulado - anterior, 0.0)))
        anterior = acumulado
    return conteos


# ----------------------------------------------------------------------------------- registro de modelos
def leer_registro(dir_modelos=None):
    return json.loads((Path(dir_modelos or DIR_MODELOS) / "registro.json").read_text(encoding="utf-8"))


def escribir_registro(registro, dir_modelos=None):
    """Escritura atómica: se escribe un temporal y se reemplaza, así la API nunca lee un archivo a medias."""
    destino = Path(dir_modelos or DIR_MODELOS) / "registro.json"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="\n", dir=destino.parent, delete=False,
                                     suffix=".tmp") as tmp:
        json.dump(registro, tmp, ensure_ascii=False, indent=2)
        tmp.write("\n")
    os.replace(tmp.name, destino)


def linea_base(version, dir_modelos=None):
    dir_modelos = Path(dir_modelos or DIR_MODELOS)
    registro = leer_registro(dir_modelos)
    ruta = dir_modelos / registro["versiones"][str(version)]["carpeta"] / "linea_base.json"
    return json.loads(ruta.read_text(encoding="utf-8")) if ruta.exists() else None


def mover_alias(version, motivo, autor, evento, alias="champion", dir_modelos=None, estado_anterior=None):
    """Apunta el alias a otra versión y lo deja en el historial. Regresa (versión anterior, versión nueva)."""
    with _candado_registro:
        registro = leer_registro(dir_modelos)
        version = str(version)
        if version not in registro["versiones"]:
            raise ValueError(f"la versión {version} no está registrada")
        anterior = registro["alias"].get(alias)
        registro["alias"][alias] = version
        registro["versiones"][version]["estado"] = "produccion"
        if anterior and anterior != version:
            registro["versiones"][anterior]["estado"] = estado_anterior or "archivada"
        registro["historial"].append({"fecha": ahora(), "evento": evento, "alias": alias, "de": anterior,
                                      "a": version, "motivo": motivo, "autor": autor})
        escribir_registro(registro, dir_modelos)
    return anterior, version


def version_anterior(registro, alias="champion"):
    """La versión que tenía el alias antes de la promoción vigente (a la que regresa un rollback)."""
    actual = registro["alias"].get(alias)
    for entrada in reversed(registro["historial"]):
        if entrada["alias"] == alias and entrada["a"] == actual and entrada["evento"] == "promocion":
            return entrada["de"]
    return None


def recargar_api(token, url=None):
    r = httpx.post(f"{url or API}/api/admin/recargar", headers={"X-Token-Admin": token}, timeout=120)
    r.raise_for_status()
    return r.json()


def registrar_version(version, carpeta, descripcion, estado="candidata", autor="", dir_modelos=None):
    """Agrega una versión al registro sin moverle ningún alias (queda como candidata)."""
    with _candado_registro:
        registro = leer_registro(dir_modelos)
        registro["versiones"][str(version)] = {"carpeta": carpeta, "registrada": ahora()[:10],
                                               "descripcion": descripcion, "estado": estado}
        registro["historial"].append({"fecha": ahora(), "evento": "registro", "alias": None, "de": None,
                                      "a": str(version), "motivo": descripcion, "autor": autor})
        escribir_registro(registro, dir_modelos)


def siguiente_version(dir_modelos=None):
    return str(max(int(v) for v in leer_registro(dir_modelos)["versiones"]) + 1)
