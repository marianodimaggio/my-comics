#!/usr/bin/env python3
"""Marca una colección como descartada ("no me interesa") o la reactiva.

No borra nada: cambia el estado a "descartada", guarda el motivo y deja
constancia en el historial. El catálogo la esconde de la vista por defecto y
la deja visible filtrando por estado "Descartada". Reactivarla devuelve cada
tomo al estado que tenía antes.

    python3 scripts/descartar.py "The Walking Dead Deluxe" --motivo "Colección discontinuada en Argentina"
    python3 scripts/descartar.py "The Walking Dead Deluxe" --reactivar
    python3 scripts/descartar.py --listar
"""

import argparse
import json
import sys
from datetime import date
from pathlib import Path

JSON = Path(__file__).resolve().parent.parent / "data" / "comics.json"
HOY = date.today().isoformat()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("coleccion", nargs="?", help="nombre exacto de la colección")
    ap.add_argument("--motivo", default="Sin motivo declarado")
    ap.add_argument("--reactivar", action="store_true")
    ap.add_argument("--listar", action="store_true", help="lista las colecciones y su estado")
    args = ap.parse_args()

    data = json.loads(JSON.read_text(encoding="utf-8"))
    items = data["items"]

    if args.listar:
        cols = {}
        for i in items:
            c = cols.setdefault(i["coleccion"], {"total": 0, "descartados": 0})
            c["total"] += 1
            if i["estado"] == "descartada":
                c["descartados"] += 1
        for nombre, c in sorted(cols.items()):
            marca = "  DESCARTADA" if c["descartados"] == c["total"] else ""
            print(f'{c["total"]:>4}  {nombre}{marca}')
        return 0

    if not args.coleccion:
        ap.error("falta el nombre de la colección (o usá --listar)")

    objetivo = [i for i in items if i["coleccion"] == args.coleccion]
    if not objetivo:
        nombres = sorted({i["coleccion"] for i in items})
        parecidos = [n for n in nombres if args.coleccion.lower() in n.lower()]
        print(f'No hay ninguna colección llamada "{args.coleccion}".', file=sys.stderr)
        if parecidos:
            print("¿Quisiste decir?: " + " / ".join(parecidos), file=sys.stderr)
        return 1

    for i in objetivo:
        if args.reactivar:
            if i["estado"] != "descartada":
                continue
            i["estado"] = i.pop("estado_previo", "pendiente")
            i.pop("motivo_descarte", None)
            i["historial"].append(f"{HOY}: reactivada")
        else:
            if i["estado"] == "descartada":
                continue
            i["estado_previo"] = i["estado"]
            i["estado"] = "descartada"
            i["motivo_descarte"] = args.motivo
            i["historial"].append(f'{HOY}: descartada — {args.motivo}')

    data["actualizado"] = HOY
    JSON.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    verbo = "reactivados" if args.reactivar else "descartados"
    print(f'{len(objetivo)} tomos de "{args.coleccion}" {verbo}.')
    return 0


if __name__ == "__main__":
    sys.exit(main())
