#!/usr/bin/env python3
"""Trae las cotizaciones del dia y las guarda en data/cambio.json.

Sirve para comparar lo que pagas aca contra lo que costaria comprar el tomo en
su pais. Se usa el euro oficial y el dolar tarjeta, que es el que pagarias
comprando en el exterior: asi la comparacion es entre dos compras posibles.

La fuente es dolarapi.com, gratis y sin clave. Si no responde, no se escribe
nada y el catalogo simplemente deja de mostrar la comparacion.

Uso:
    python3 scripts/cambio.py
    python3 scripts/cambio.py --dry-run
"""

import argparse
import json
import sys
import time
import urllib.request
from datetime import date
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
CAMBIO = RAIZ / "data" / "cambio.json"
HOY = date.today().isoformat()
UA = "Mozilla/5.0 (compatible; catalogo-comics/1.0; uso personal)"

# clave que usa el catalogo, url, y para que sirve
FUENTES = [
    ("EUR", "https://dolarapi.com/v1/cotizaciones/eur",
     "para los tomos con precio de tapa en euros"),
    ("USD", "https://dolarapi.com/v1/dolares/tarjeta",
     "para los tomos con precio de tapa en dolares"),
    ("USD_oficial", "https://dolarapi.com/v1/dolares/oficial",
     "de referencia"),
]


def leer(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read(50_000).decode("utf-8", errors="replace"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    datos = {"fecha": HOY, "fuente": "dolarapi.com"}
    for clave, url, para_que in FUENTES:
        try:
            crudo = leer(url)
        except Exception as e:
            print(f"{clave}: no respondio ({str(e)[:50]})")
            continue
        valor = crudo.get("venta") or crudo.get("compra")
        if not isinstance(valor, (int, float)):
            print(f"{clave}: la respuesta no trae un valor usable")
            continue
        datos[clave] = {"valor": round(float(valor), 2),
                        "nombre": crudo.get("nombre") or clave,
                        "actualizado": crudo.get("fechaActualizacion") or HOY}
        print(f'{clave}: {datos[clave]["valor"]} ({datos[clave]["nombre"]}), {para_que}')
        time.sleep(0.5)

    if len(datos) <= 2:
        print("\nNinguna cotizacion pudo leerse: no escribo nada y dejo la anterior.",
              file=sys.stderr)
        return 1

    if args.dry_run:
        print("\n--dry-run: no escribi nada.")
        return 0

    CAMBIO.write_text(json.dumps(datos, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\nEscribi data/cambio.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
