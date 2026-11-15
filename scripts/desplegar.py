"""Promueve una versión del registro a producción (alias champion) después de pasar la compuerta de calidad.

Compuerta (antes de mover el alias):
    1. SLO de calidad en el golden set: F1 macro >= 0.60 y enfermas reportadas como sanas <= 15 % (lo mismo que
       vigila la sonda en producción; si no lo pasa aquí, la sonda lo reprobaría en minutos).
    2. No empeorar en las condiciones que hoy ve producción. La mezcla de fotos de lluvia se estima con el monitor
       (proporción de fotos con nitidez fuera del rango del entrenamiento en la última hora, 5 min en el
       laboratorio) y se compara el F1 y las enfermas como sanas esperados de la candidata contra los de la versión
       en servicio con esa mezcla.
    3. Si la candidata pierde más de 0.05 de F1 en alguna condición (aunque gane en la mezcla), es un intercambio
       que decide una persona: hace falta --aprobado-por.
Con --sin-compuerta se salta todo (despliegue de emergencia): queda marcado en el historial del registro.

Después de promover pide a la API que recargue (sin reiniciar) y corre la sonda una vez. Si la versión sale mala,
la sonda la detecta y operaciones hace el rollback (RB-04). Cada despliegue queda en evidencias/monitoreo/despliegues/.

Uso:
    python scripts/desplegar.py --version 3 --motivo "Temporada de lluvias" --aprobado-por "Rabin Muñoz"
    python scripts/desplegar.py --version 2 --motivo "Hotfix" --sin-compuerta
"""
import argparse
import json
import os
import sys
from pathlib import Path

import httpx
import skops.io as sio

sys.path.insert(0, str(Path(__file__).resolve().parent))
import datos  # noqa: E402
import evaluacion  # noqa: E402
import versiones  # noqa: E402

comun = versiones.comun
F1_MIN, FNR_MAX, PERDIDA_MAX = 0.60, 0.15, 0.05
VENTANA = "5m"


def estimar_lluvia(prometheus):
    """Proporción de fotos de usuarios con nitidez fuera de rango, menos el 1 % que pasa con fotos normales."""
    try:
        fuera = comun.escalar(f'sum(increase(entrada_fuera_de_rango_total{{variable="nitidez",origen="usuario"}}[{VENTANA}]))',
                              prometheus) or 0.0
        total = comun.escalar(f'sum(increase(entrada_nitidez_count{{origen="usuario"}}[{VENTANA}]))', prometheus) or 0.0
    except httpx.HTTPError:
        return None
    return None if total < 50 else round(max(0.0, min(1.0, (fuera / total - 0.01) / 0.99)), 3)


def esperado(m, p):
    return {k: round((1 - p) * m["golden"][k] + p * m["adverso"][k], 4) for k in ("f1_macro", "enferma_como_sana")}


def compuerta(actual, candidata, mezcla, aprobado_por):
    razones, requiere_aprobacion = [], False
    g = candidata["golden"]
    if g["f1_macro"] < F1_MIN or g["enferma_como_sana"] > FNR_MAX:
        razones.append(f"no cumple el SLO en el golden set (F1 {g['f1_macro']}, enfermas como sanas {g['enferma_como_sana']:.1%})")
    e_act, e_cand = esperado(actual, mezcla), esperado(candidata, mezcla)
    if e_cand["f1_macro"] < e_act["f1_macro"] or e_cand["enferma_como_sana"] > e_act["enferma_como_sana"] + 0.02:
        razones.append(f"con {mezcla:.0%} de fotos de lluvia empeora (F1 esperado {e_cand['f1_macro']} contra "
                       f"{e_act['f1_macro']}; enfermas como sanas {e_cand['enferma_como_sana']:.1%} contra "
                       f"{e_act['enferma_como_sana']:.1%})")
    perdidas = {c: round(actual[c]["f1_macro"] - candidata[c]["f1_macro"], 4) for c in ("golden", "adverso")}
    if any(v > PERDIDA_MAX for v in perdidas.values()):
        requiere_aprobacion = True
        if not aprobado_por:
            razones.append(f"pierde más de {PERDIDA_MAX} de F1 en alguna condición {perdidas}: es un intercambio "
                           "que debe aprobar una persona (--aprobado-por)")
    return {"aprobada": not razones, "razones": razones, "mezcla_lluvia": mezcla, "esperado_en_servicio": e_act,
            "esperado_candidata": e_cand, "perdida_f1_por_condicion": perdidas,
            "requiere_aprobacion": requiere_aprobacion, "aprobado_por": aprobado_por}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", required=True)
    parser.add_argument("--motivo", required=True)
    parser.add_argument("--aprobado-por")
    parser.add_argument("--sin-compuerta", action="store_true", help="despliegue de emergencia (queda registrado)")
    parser.add_argument("--mezcla-lluvia", type=float, help="si no se da, se estima con Prometheus")
    parser.add_argument("--api", default="http://localhost:8000")
    parser.add_argument("--prometheus", default="http://localhost:9090")
    parser.add_argument("--vigilante", default="http://localhost:9101")
    parser.add_argument("--token", default=os.environ.get("TOKEN_ADMIN", "laboratorio-maiz"))
    args = parser.parse_args()

    registro = comun.leer_registro(versiones.DIR_MODELOS)
    actual, nueva = registro["alias"]["champion"], str(args.version)
    if nueva == actual:
        sys.exit(f"La versión {nueva} ya está en servicio")
    if registro["versiones"][nueva]["estado"] == "rechazada" and not args.sin_compuerta:
        sys.exit(f"La versión {nueva} fue rechazada por un rollback; corríjala y regístrela como nueva")

    def cargar(v):
        carpeta = versiones.DIR_MODELOS / registro["versiones"][v]["carpeta"]
        ficha = json.loads((carpeta / "ficha_modelo.json").read_text(encoding="utf-8"))
        return evaluacion.evaluar(sio.load(carpeta / "model.skops"), ficha["umbral_enferma"]["valor"], ("golden", "adverso"))

    registro_despliegue = {"fecha": comun.ahora(), "de": actual, "a": nueva, "motivo": args.motivo,
                           "sin_compuerta": args.sin_compuerta}
    if args.sin_compuerta:
        print(f"ATENCIÓN: se promueve la v{nueva} SIN compuerta de calidad (queda en el historial)")
        motivo = f"{args.motivo} [SIN COMPUERTA]"
    else:
        mezcla = args.mezcla_lluvia if args.mezcla_lluvia is not None else (estimar_lluvia(args.prometheus) or 0.0)
        m_actual, m_nueva = cargar(actual), cargar(nueva)
        resultado = compuerta(m_actual, m_nueva, mezcla, args.aprobado_por)
        registro_despliegue.update({"metricas": {f"v{actual}": m_actual, f"v{nueva}": m_nueva}, "compuerta": resultado})
        print(f"Compuerta de calidad (mezcla estimada de lluvia: {mezcla:.0%})")
        for v, m in ((actual, m_actual), (nueva, m_nueva)):
            print(f"  v{v}: golden F1 {m['golden']['f1_macro']:.3f} / enfermas como sanas {m['golden']['enferma_como_sana']:.1%}"
                  f"   adverso F1 {m['adverso']['f1_macro']:.3f} / {m['adverso']['enferma_como_sana']:.1%}")
        print(f"  esperado con la mezcla: v{actual} {resultado['esperado_en_servicio']}  v{nueva} {resultado['esperado_candidata']}")
        if not resultado["aprobada"]:
            guardar(registro_despliegue | {"resultado": "rechazado por la compuerta"})
            sys.exit("Compuerta NO aprobada:\n  - " + "\n  - ".join(resultado["razones"]))
        aprobacion = f"; aprobado por {args.aprobado_por}" if args.aprobado_por else ""
        motivo = f"{args.motivo} [compuerta aprobada, lluvia {mezcla:.0%}{aprobacion}]"

    comun.mover_alias(nueva, motivo=motivo, autor=versiones.autor_git(), evento="promocion",
                      dir_modelos=versiones.DIR_MODELOS)
    recarga = comun.recargar_api(args.token, args.api)
    print(f"Alias champion: v{actual} -> v{nueva}; la API recargó: {recarga}")
    try:
        sonda = httpx.post(f"{args.vigilante}/sondear", timeout=120).json().get("golden")
        print(f"Sonda después del despliegue: {sonda}")
    except httpx.HTTPError as e:
        sonda = f"no se pudo correr la sonda: {e}"
    guardar(registro_despliegue | {"resultado": "promovida", "recarga": recarga, "sonda_posterior": sonda})


def guardar(datos_despliegue):
    carpeta = datos.RAIZ / "evidencias" / "monitoreo" / "despliegues"
    carpeta.mkdir(parents=True, exist_ok=True)
    nombre = f"{datos_despliegue['fecha'][:19].replace(':', '').replace('-', '')}_v{datos_despliegue['a']}.json"
    (carpeta / nombre).write_text(json.dumps(datos_despliegue, ensure_ascii=False, indent=2) + "\n",
                                  encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
