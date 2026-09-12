#!/usr/bin/env python3
"""Revalida precio, stock y tapa de cada cómic que ya tenga URL de producto.

Reescribe data/comics.json conservando trazabilidad: cada cambio de precio o de
disponibilidad queda registrado en el campo "historial" del item. Nunca borra
un item ni inventa datos: si no puede leer la página, marca el precio como
vencido y sigue.

Uso:
    python3 scripts/refresh.py                # revalida todo lo que pueda
    python3 scripts/refresh.py --dry-run      # muestra qué cambiaría, sin escribir
    python3 scripts/refresh.py --solo historieteca   # filtra por dominio
"""

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

RAIZ = Path(__file__).resolve().parent.parent
JSON = RAIZ / "data" / "comics.json"
LOG = RAIZ / "data" / "ultima-actualizacion.json"
HOY = date.today().isoformat()

UA = ("Mozilla/5.0 (compatible; catalogo-comics/1.0; "
      "revalidacion de precios de uso personal)")
PAUSA = 2.0  # segundos entre pedidos, para no golpear las tiendas

# Dominios que sabemos leer. Tiendanube publica precio y stock en meta tags,
# así que alcanza con parsear el <head>. MercadoLibre y las tiendas que
# renderizan con JS quedan afuera a propósito: hay que mirarlas a mano.
SOPORTADOS = ("mitiendanube.com", "reyesteban.com", "culturaguiso.com",
              "itsatrapcomicstore.ar", "quiosquitovirtual.com.ar",
              # su /search/ esta bloqueado por robots.txt, pero las paginas
              # de producto se pueden leer sin problema
              "hoteldelasideastienda.com.ar")


def meta(html_txt, clave):
    """Devuelve el content de un <meta property|name="clave">."""
    m = re.search(
        r'<meta[^>]+(?:property|name)=["\']' + re.escape(clave) +
        r'["\'][^>]+content=["\']([^"\']*)["\']', html_txt, re.I)
    if m:
        return m.group(1)
    m = re.search(
        r'<meta[^>]+content=["\']([^"\']*)["\'][^>]+(?:property|name)=["\']' +
        re.escape(clave) + r'["\']', html_txt, re.I)
    return m.group(1) if m else None


def leer(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept-Language": "es-AR,es"})
    with urllib.request.urlopen(req, timeout=25) as r:
        bruto = r.read(600_000)
    return bruto.decode("utf-8", errors="replace")


def isbn_en_pagina(html_txt):
    """Muchas tiendas publican el ISBN en la descripcion del producto.
    Se valida el digito de control para no guardar un numero mal copiado."""
    for bruto in re.findall(r"ISBN[^0-9]{0,12}((?:97[89][\-\s]?)?[0-9][0-9\-\s]{8,17}[0-9Xx])",
                            html_txt, re.I):
        n = re.sub(r"[^0-9Xx]", "", bruto).upper()
        if len(n) == 10:
            n = a_isbn13(n)
        if n and len(n) == 13 and valido13(n):
            return n
    return None


def a_isbn13(isbn10):
    cuerpo = "978" + isbn10[:9]
    suma = sum((1 if i % 2 == 0 else 3) * int(d) for i, d in enumerate(cuerpo))
    return cuerpo + str((10 - suma % 10) % 10)


def valido13(n):
    suma = sum((1 if i % 2 == 0 else 3) * int(d) for i, d in enumerate(n[:12]))
    return n[12] == str((10 - suma % 10) % 10)


def scrapear(url):
    """Devuelve dict con precio, stock y tapa, o None si la página no los expone."""
    html_txt = leer(url)
    precio = meta(html_txt, "tiendanube:price") or meta(html_txt, "product:price:amount")
    if not precio:
        return None
    stock = meta(html_txt, "tiendanube:stock")
    tapa = meta(html_txt, "og:image:secure_url") or meta(html_txt, "og:image")
    return {
        "precio": int(float(precio)),
        "stock": int(stock) if stock and stock.isdigit() else None,
        "cover_url": tapa.replace("http://", "https://") if tapa else None,
        "isbn": isbn_en_pagina(html_txt),
    }


def revalidar(item, dry):
    url = item.get("url_producto")
    host = urlparse(url).netloc
    if not any(d in host for d in SOPORTADOS):
        return "omitido", f"{host} no se puede leer automáticamente"

    try:
        nuevo = scrapear(url)
    except urllib.error.HTTPError as e:
        if e.code in (404, 410):
            item["observaciones"] = ("Publicación caída (HTTP %d) el %s. "
                                     "Buscar reemplazo." % (e.code, HOY))
            item["stock"] = "sin stock"
            item["historial"].append(
                f"{HOY}: publicación caída (HTTP {e.code}), precio anterior "
                f"ARS {item.get('precio')}")
            return "caido", f"HTTP {e.code}"
        return "error", f"HTTP {e.code}"
    except Exception as e:                      # red, timeout, encoding
        return "error", str(e)[:80]

    if not nuevo:
        return "sin_datos", "la página no expone precio en los meta tags"

    cambios = []
    viejo = item.get("precio")
    if viejo != nuevo["precio"]:
        cambios.append(f"precio ARS {viejo} → ARS {nuevo['precio']}")
        item["historial"].append(f"{HOY}: precio ARS {viejo} → ARS {nuevo['precio']}")
        item["precio"] = nuevo["precio"]
        if item.get("precio_transferencia"):
            item["precio_transferencia"] = None  # hay que reconfirmarlo a mano

    if nuevo["stock"] is not None:
        etiqueta = f"{nuevo['stock']} unidades" if nuevo["stock"] else "sin stock"
        if item.get("stock") != etiqueta:
            cambios.append(f"stock → {etiqueta}")
            if nuevo["stock"] == 0:
                item["historial"].append(f"{HOY}: quedó sin stock")
            item["stock"] = etiqueta

    if nuevo["cover_url"] and not item.get("cover_url"):
        cambios.append("tapa recuperada")
        item["cover_url"] = nuevo["cover_url"]

    if nuevo.get("isbn") and not item.get("isbn"):
        cambios.append("ISBN " + nuevo["isbn"])
        item["isbn"] = nuevo["isbn"]
        item["historial"].append(f'{HOY}: ISBN {nuevo["isbn"]} leido de la pagina del producto')

    item["fecha_verificacion"] = HOY
    return ("actualizado" if cambios else "sin_cambios"), ", ".join(cambios) or "todo igual"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--solo", default="", help="filtra por texto en el dominio")
    args = ap.parse_args()

    data = json.loads(JSON.read_text(encoding="utf-8"))
    objetivo = [i for i in data["items"]
                if i.get("url_producto") and args.solo in (i["url_producto"] or "")]
    print(f"{len(objetivo)} items con URL de producto\n")

    resumen, detalle = {}, []
    for n, item in enumerate(objetivo, 1):
        estado, msg = revalidar(item, args.dry_run)
        resumen[estado] = resumen.get(estado, 0) + 1
        etiqueta = f'{item["coleccion"]} #{item["numero"]}'
        print(f"[{n}/{len(objetivo)}] {etiqueta}: {estado} — {msg}")
        detalle.append({"item": item["id"], "estado": estado, "detalle": msg})
        if n < len(objetivo):
            time.sleep(PAUSA)

    print("\n" + " · ".join(f"{k}: {v}" for k, v in sorted(resumen.items())))

    if args.dry_run:
        print("\n--dry-run: no escribí nada.")
        return 0

    data["actualizado"] = HOY
    JSON.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    LOG.write_text(json.dumps({"corrida": HOY, "resumen": resumen, "detalle": detalle},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nEscribí {JSON.relative_to(RAIZ)} y {LOG.relative_to(RAIZ)}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
