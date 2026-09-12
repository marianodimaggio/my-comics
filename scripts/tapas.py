#!/usr/bin/env python3
"""Trae tapas, titulos e idioma desde las fichas de edicion de Whakoom.

Lee data/ediciones.json (coleccion -> URL de la ficha de la edicion), abre cada
ficha y completa en data/comics.json: cover_url en alta, titulo, editorial,
idioma y formato de cada tomo.

Limite conocido: la ficha publica lista los primeros 11 numeros. Para los
siguientes hace falta sesion, asi que los deja sin tapa y lo avisa.

Uso:
    python3 scripts/tapas.py
    python3 scripts/tapas.py --coleccion "Dora"
    python3 scripts/tapas.py --dry-run
"""

import argparse
import json
import re
import sys
import time
import urllib.request
from datetime import date
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
COMICS = RAIZ / "data" / "comics.json"
EDICIONES = RAIZ / "data" / "ediciones.json"
LOG = RAIZ / "data" / "ultima-tapas.json"
HOY = date.today().isoformat()

UA = "Mozilla/5.0 (compatible; catalogo-comics/1.0; uso personal)"
PAUSA = 2.0


def leer(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "es-AR,es"})
    with urllib.request.urlopen(req, timeout=25) as r:
        return r.read(900_000).decode("utf-8", errors="replace")


def meta(html_txt, clave):
    m = re.search(r'<meta[^>]+(?:property|name)=["\']' + re.escape(clave) +
                  r'["\'][^>]+content=["\']([^"\']*)["\']', html_txt, re.I)
    return m.group(1) if m else None


def tapas_por_numero(html_txt):
    """{numero: url de tapa en alta}.

    No depende del atributo: Whakoom carga las miniaturas en diferido, asi que
    la URL puede estar en src, data-src, data-original o srcset. Se parte el
    HTML por enlaces y en cada bloque se busca una imagen de Whakoom y un #N.
    """
    salida = {}
    for bloque in re.split(r'<a\b', html_txt, flags=re.I):
        img = re.search(r'(https://i1\.whakoom\.com/(?:small|medium|large)/'
                        r'[0-9a-f]{2}/[0-9a-f]{2}/[0-9a-f]+\.(?:jpg|jpeg|png|webp))',
                        bloque, re.I)
        if not img:
            continue
        num = re.search(r'#\s*([0-9]+(?:\.[0-9]+)?)', bloque)
        if not num:
            continue
        url = re.sub(r'/(?:small|medium)/', '/large/', img.group(1))
        salida.setdefault(num.group(1), url)
    return salida


def titulos_por_numero(html_txt):
    """{numero: titulo} a partir de los enlaces a cada comic."""
    salida = {}
    for num, txt in re.findall(
            r'/comics/[A-Za-z0-9]+/[^"\']+?/([0-9]+(?:\.[0-9]+)?)["\'][^>]*>(.{0,220}?)</a>',
            html_txt, re.S):
        limpio = re.sub(r'<[^>]+>', ' ', txt)
        limpio = re.sub(r'\s+', ' ', limpio).strip()
        m = re.search(r'#' + re.escape(num) + r'\s+(.+)$', limpio)
        if m:
            t = m.group(1).strip().strip('"').strip()
            if t and len(t) < 120:
                salida.setdefault(num, t)
    return salida


def ficha(url):
    html_txt = leer(url)
    cab = re.search(r'>\s*([0-9]+)\s*c[oó]mics\s*<', html_txt, re.I)
    idioma = None
    m = re.search(r'(Espa[nñ]ol|Ingl[eé]s|Franc[eé]s|Italiano|Portugu[eé]s|Catal[aá]n|Alem[aá]n)'
                  r'\s*\(([^)<]{3,40})\)', html_txt)
    if m:
        idioma = f"{m.group(1)} ({m.group(2)})"
    return {
        "editorial": (meta(html_txt, "og:description") or "").split(".")[0].strip() or None,
        "idioma": idioma,
        "tomos": int(cab.group(1)) if cab else None,
        "tapas": tapas_por_numero(html_txt),
        "titulos": titulos_por_numero(html_txt),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--coleccion")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    data = json.loads(COMICS.read_text(encoding="utf-8"))
    mapa = json.loads(EDICIONES.read_text(encoding="utf-8"))["ediciones"]
    if args.coleccion:
        mapa = [e for e in mapa if e["coleccion"] == args.coleccion]
    mapa = [e for e in mapa if e.get("url")]

    if not mapa:
        print("No hay ediciones con URL para procesar.", file=sys.stderr)
        return 1

    resumen = {"tapas": 0, "titulos": 0, "sin_ficha": 0, "fuera_de_alcance": 0}
    detalle = []

    for e in mapa:
        print(f'\n{e["coleccion"]}  ->  {e["url"]}')
        try:
            f = ficha(e["url"])
        except Exception as err:
            print(f"   no pude leer la ficha: {str(err)[:70]}")
            resumen["sin_ficha"] += 1
            continue
        time.sleep(PAUSA)
        print(f'   {len(f["tapas"])} tapas, {len(f["titulos"])} titulos, '
              f'idioma {f["idioma"] or "sin detectar"}, {f["tomos"] or "?"} tomos en la edicion')

        tocados = 0
        for item in data["items"]:
            if item["coleccion"] != e["coleccion"] or item["estado"] == "descartada":
                continue
            n = item["numero"]
            if n in f["tapas"] and item.get("cover_url") != f["tapas"][n]:
                item["cover_url"] = f["tapas"][n]
                resumen["tapas"] += 1
                tocados += 1
            if n in f["titulos"] and not item.get("titulo"):
                item["titulo"] = f["titulos"][n]
                resumen["titulos"] += 1
            if n not in f["tapas"]:
                resumen["fuera_de_alcance"] += 1
            if f["idioma"] and not item.get("idioma"):
                item["idioma"] = f["idioma"]
            if e.get("editorial") and not item.get("editorial"):
                item["editorial"] = e["editorial"]
            if e.get("formato") and not item.get("formato"):
                item["formato"] = e["formato"]
            item["edicion_whakoom"] = e["url"]
            if tocados:
                item.setdefault("historial", []).append(
                    f"{HOY}: tapa y datos desde la ficha de la edicion en Whakoom")
        detalle.append({"coleccion": e["coleccion"], "tapas": len(f["tapas"])})

    print("\n" + " · ".join(f"{k}: {v}" for k, v in resumen.items()))
    if resumen["fuera_de_alcance"]:
        print(f'{resumen["fuera_de_alcance"]} tomos quedaron sin tapa: la ficha publica de '
              f'Whakoom solo lista los primeros 11 numeros.')

    if args.dry_run:
        print("\n--dry-run: no escribi nada.")
        return 0

    data["actualizado"] = HOY
    COMICS.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    LOG.write_text(json.dumps({"corrida": HOY, "resumen": resumen, "detalle": detalle},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    print("\nEscribi data/comics.json y data/ultima-tapas.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
