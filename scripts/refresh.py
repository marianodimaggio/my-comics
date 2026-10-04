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
              "hoteldelasideastienda.com.ar",
              # WooCommerce y similares: el precio viene en datos estructurados
              "nuevonueve.com", "lagaleracomics.com.ar",
              "buscalibre.com.ar", "penguinlibros.com", "astiberri.com",
              "instocktrades.com", "maeva.es", "eccediciones.com")


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
    """ISBN publicado en la descripcion del producto.

    El texto que sigue al numero suele traer mas digitos (paginas, medidas),
    asi que se corta a lo que corresponde y se valida el digito de control.
    """
    patron = r"ISBN[^0-9]{0,12}([0-9][0-9" + "\\" + "-" + r"\s]{7,24}[0-9Xx])"
    for bruto in re.findall(patron, html_txt, re.I):
        digitos = re.sub(r"[^0-9Xx]", "", bruto).upper()
        # Si arranca con 978 o 979 es un ISBN-13: solo se prueban esos 13.
        # Recortar 10 digitos de ahi fabricaria un numero valido pero falso.
        if digitos[:3] in ("978", "979"):
            if len(digitos) >= 13 and valido13(digitos[:13]):
                return digitos[:13]
            continue
        if len(digitos) >= 10:
            trece = a_isbn13(digitos[:10])
            if valido13(trece):
                return trece
    return None

def a_isbn13(isbn10):
    cuerpo = "978" + isbn10[:9]
    suma = sum((1 if i % 2 == 0 else 3) * int(d) for i, d in enumerate(cuerpo))
    return cuerpo + str((10 - suma % 10) % 10)


def valido13(n):
    suma = sum((1 if i % 2 == 0 else 3) * int(d) for i, d in enumerate(n[:12]))
    return n[12] == str((10 - suma % 10) % 10)


def precio_instocktrades(html_txt):
    """InStockTrades muestra el precio como texto: IST Price: $10.53."""
    m = re.search(r"IST Price:\s*\$\s*([0-9]+(?:\.[0-9]{2})?)", html_txt, re.I)
    return m.group(1) if m else None


def precio_en_json_ld(html_txt):
    """Muchas tiendas publican el producto como datos estructurados."""
    for bloque in re.findall(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>',
                             html_txt, re.S):
        m = re.search(r'"price"\s*:\s*"?([0-9]+(?:[.,][0-9]+)?)"?', bloque)
        if m:
            return m.group(1).replace(",", ".")
    return None


# Si la tienda no declara la moneda, se deduce del dominio. Sin esto, 10.53
# dolares se guardan como 10 pesos.
MONEDA_POR_DOMINIO = [
    ("instocktrades.com", "USD"), ("dcbservice.com", "USD"), ("mycomicshop.com", "USD"),
    ("amazon.com", "USD"), ("ebay.com", "USD"),
    ("nuevonueve.com", "EUR"), ("astiberri.com", "EUR"), ("eccediciones.com", "EUR"),
    ("maeva.es", "EUR"), ("casadellibro.com", "EUR"),
    ("buscalibre.com.ar", "ARS"), ("mitiendanube.com", "ARS"), ("penguinlibros.com/ar", "ARS"),
]


def moneda_por_url(url):
    u = (url or "").lower()
    for dominio, moneda in MONEDA_POR_DOMINIO:
        if dominio in u:
            return moneda
    return None


def moneda_de(html_txt):
    """En que moneda esta el precio. Sin esto, 45 euros se guardan como 45 pesos."""
    for clave in ("tiendanube:currency", "product:price:currency", "og:price:currency"):
        v = meta(html_txt, clave)
        if v:
            return v.strip().upper()[:3]
    m = re.search(r'"priceCurrency"\s*:\s*"([A-Za-z]{3})"', html_txt)
    if m:
        return m.group(1).upper()
    return None


def disponible_en_json_ld(html_txt):
    if re.search(r'"availability"\s*:\s*"[^"]*OutOfStock"', html_txt, re.I):
        return "sin stock"
    if re.search(r'"availability"\s*:\s*"[^"]*InStock"', html_txt, re.I):
        return "en stock"
    return None


def es_imagen(url):
    return bool(url) and bool(re.search(r"\.(jpe?g|png|webp|gif|avif)(\?|#|$)", url, re.I))


def tapa_de_pagina(url):
    """Saca la imagen principal de cualquier pagina. Sirve para que puedas
    pegar la direccion de la pagina del producto en vez de buscar la imagen."""
    try:
        html_txt = leer(url, 400_000)
    except Exception:
        return None
    img = (meta(html_txt, "og:image:secure_url") or meta(html_txt, "og:image")
           or meta(html_txt, "twitter:image") or meta(html_txt, "twitter:image:src"))
    if not img:
        return None
    img = img.replace("http://", "https://")
    return img if es_imagen(img) else None


def tapa_open_library(isbn):
    """Open Library no bloquea a los servidores, a diferencia de las tiendas.
    Con default=false devuelve 404 si no tiene tapa, asi que no hay riesgo de
    guardar la imagen en blanco que sirve por defecto."""
    if not isbn:
        return None
    url = f"https://covers.openlibrary.org/b/isbn/{isbn}-L.jpg?default=false"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA}, method="HEAD")
        with urllib.request.urlopen(req, timeout=15) as r:
            if r.status == 200 and int(r.headers.get("Content-Length") or 1000) > 600:
                return url.split("?")[0]
    except Exception:
        pass
    return None


def resolver_tapa(item):
    """Consigue la tapa mirando, en orden: lo que haya en el campo si es una
    pagina, la pagina del producto y la de la editorial."""
    candidatas = []
    if item.get("cover_url") and not es_imagen(item["cover_url"]):
        candidatas.append(("lo que estaba en el campo de tapa", item["cover_url"]))
    for clave, nombre in (("url_producto", "la pagina del producto"),
                          ("url_editorial", "la web de la editorial")):
        if item.get(clave):
            candidatas.append((nombre, item[clave]))
    for nombre, url in candidatas:
        img = tapa_de_pagina(url)
        time.sleep(1.0)
        if img:
            return img, nombre

    # Ultimo recurso y el mas confiable para lo importado: las tiendas de
    # afuera bloquean a los servidores, Open Library no.
    img = tapa_open_library(item.get("isbn"))
    if img:
        return img, "Open Library, por ISBN"
    return None, None


def scrapear(url):
    """Precio, stock, tapa e ISBN de una pagina de producto, o None.

    Tiendanube primero, y si no, los formatos comunes de WooCommerce y de
    cualquier tienda que publique datos estructurados.
    """
    html_txt = leer(url)
    precio = (meta(html_txt, "tiendanube:price")
              or meta(html_txt, "product:price:amount")
              or meta(html_txt, "og:price:amount")
              or precio_en_json_ld(html_txt)
              or precio_instocktrades(html_txt))
    # Antes, sin precio se abandonaba la pagina entera y se perdian la tapa y
    # el ISBN, que suelen estar igual. Ahora se devuelve lo que haya.
    stock = meta(html_txt, "tiendanube:stock")
    tapa = meta(html_txt, "og:image:secure_url") or meta(html_txt, "og:image")
    return {
        "precio": (round(float(precio), 2) if precio else None),
        "moneda": moneda_de(html_txt),
        "stock": int(stock) if stock and stock.isdigit() else None,
        "disponible": disponible_en_json_ld(html_txt),
        "cover_url": tapa.replace("http://", "https://") if tapa else None,
        "isbn": isbn_en_pagina(html_txt),
    }


def revalidar_opciones(item):
    """Lee el precio de cada opcion de compra cargada. Sin esto no se puede
    comparar: la que no se consulta nunca queda sin precio y no entra."""
    tocadas = []
    for o in item.get("opciones") or []:
        url = o.get("url")
        if not url:
            continue
        host = urlparse(url).netloc
        if not any(d in host for d in SOPORTADOS):
            if not o.get("precio"):
                print(f'      {o.get("tienda")}: {host} no se puede leer, cargale el precio a mano')
            continue
        try:
            datos = scrapear(url)
        except Exception as e:
            print(f'      {o.get("tienda")}: {str(e)[:50]}')
            continue
        time.sleep(PAUSA)
        if not datos or datos.get("precio") is None:
            print(f'      {o.get("tienda")}: la pagina no publica el precio')
            continue
        o["precio"] = datos["precio"]
        o["moneda"] = datos.get("moneda") or moneda_por_url(url) or o.get("moneda")
        if datos.get("stock") is not None:
            o["stock"] = f'{datos["stock"]} unidades' if datos["stock"] else "sin stock"
        elif datos.get("disponible"):
            o["stock"] = datos["disponible"]
        o["verificado"] = HOY
        tocadas.append(f'{o.get("tienda")} {o["precio"]} {o.get("moneda") or ""}'.strip())
    return tocadas


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
        return "sin_datos", "no pude leer nada de la pagina"

    cambios = []
    viejo = item.get("precio")
    nuevo["moneda"] = nuevo.get("moneda") or moneda_por_url(item.get("url_producto"))
    if nuevo.get("moneda") and item.get("moneda") != nuevo["moneda"]:
        cambios.append("moneda " + nuevo["moneda"])
        item["historial"].append(
            f'{HOY}: moneda {item.get("moneda") or "sin definir"} -> {nuevo["moneda"]} '
            f'(leida de la tienda)')
        item["moneda"] = nuevo["moneda"]

    if nuevo["precio"] is not None and viejo != nuevo["precio"]:
        # Una baja de precio es la unica forma confiable de detectar una oferta:
        # el descuento que declara la tienda puede ser sobre un precio inflado.
        if viejo and nuevo["precio"] < viejo:
            pct = round((1 - nuevo["precio"] / viejo) * 100)
            item["bajo_precio"] = {"anterior": viejo, "porcentaje": pct, "fecha": HOY}
            cambios.append(f"BAJO {pct}%")
        elif viejo and nuevo["precio"] > viejo:
            item.pop("bajo_precio", None)
        cambios.append(f"precio ARS {viejo} → ARS {nuevo['precio']}")
        item["historial"].append(f"{HOY}: precio ARS {viejo} → ARS {nuevo['precio']}")
        item["precio"] = nuevo["precio"]
        if item.get("precio_transferencia"):
            item["precio_transferencia"] = None  # hay que reconfirmarlo a mano

    # Si la tienda no publica unidades pero si dice si hay stock, se guarda eso.
    if nuevo["stock"] is None and nuevo.get("disponible") and item.get("stock") != nuevo["disponible"]:
        cambios.append("stock: " + nuevo["disponible"])
        item["stock"] = nuevo["disponible"]

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

    # Si el tomo tiene varias opciones de compra, se actualiza la que coincide
    # con la URL que se acaba de leer, para poder compararlas despues.
    for o in item.get("opciones") or []:
        if o.get("url") == item.get("url_producto"):
            o["precio"] = nuevo["precio"] if nuevo["precio"] is not None else o.get("precio")
            o["moneda"] = nuevo.get("moneda") or o.get("moneda") or item.get("moneda")
            o["stock"] = item.get("stock")
            o["envio"] = item.get("envio")
            o["verificado"] = HOY

    if nuevo["precio"] is None:
        cambios.append("OJO: la pagina no publica el precio de forma legible, "
                       "cargalo a mano")
    else:
        item["fecha_verificacion"] = HOY
    return ("actualizado" if cambios else "sin_cambios"), ", ".join(cambios) or "todo igual"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--id", default="", help="revalidar un solo tomo")
    ap.add_argument("--solo", default="", help="filtra por texto en el dominio")
    args = ap.parse_args()

    data = json.loads(JSON.read_text(encoding="utf-8"))
    if args.id:
        # Un tomo puntual entra aunque no tenga pagina de producto: puede que
        # solo queramos sacarle la tapa de la web de la editorial.
        objetivo = [i for i in data["items"] if i["id"] == args.id]
        if objetivo and not any(objetivo[0].get(k) for k in
                                ("url_producto", "url_editorial", "cover_url")):
            print(f'{objetivo[0]["coleccion"]} #{objetivo[0]["numero"]} no tiene ninguna '
                  f'pagina cargada.\n\nPara sacar la tapa hace falta al menos una: la del '
                  f'producto en la tienda, la de la editorial, o pegar en el campo de tapa '
                  f'la direccion de cualquier pagina donde se vea.')
            return 0
    else:
        objetivo = [i for i in data["items"]
                    if i.get("url_producto") and args.solo in (i["url_producto"] or "")]
    print(f"{len(objetivo)} items con URL de producto\n")

    resumen, detalle = {}, []
    for n, item in enumerate(objetivo, 1):
        if item.get("url_producto"):
            estado, msg = revalidar(item, args.dry_run)
        else:
            estado, msg = "sin_cambios", "sin pagina de producto, solo busco la tapa"

        # Las demas opciones de compra, para poder compararlas
        otras = revalidar_opciones(item)
        if otras:
            msg = (msg + ", " if msg and msg != "todo igual" else "") + "opciones: " + "; ".join(otras)
            estado = "actualizado"

        # Si despues de todo la tapa sigue sin ser una imagen, se resuelve a
        # partir de las paginas que si tenemos.
        if not es_imagen(item.get("cover_url")):
            paginas = [c for c in ("cover_url", "url_producto", "url_editorial")
                       if item.get(c)]
            if not paginas and not item.get("isbn"):
                print("      sin paginas cargadas ni ISBN: no hay de donde sacar la tapa")
            else:
                img, de_donde = resolver_tapa(item)
                if img:
                    item["cover_url"] = img
                    item.setdefault("historial", []).append(
                        f"{HOY}: tapa obtenida de {de_donde}")
                    msg = (msg + ", " if msg and msg != "todo igual" else "") + f"tapa de {de_donde}"
                    estado = "actualizado"
                else:
                    print(f'      no consegui la tapa: probe {len(paginas)} pagina(s)'
                          f'{" y Open Library por ISBN" if item.get("isbn") else ""}. '
                          f'Las tiendas de afuera suelen rechazar al servidor: pega la '
                          f'direccion de la imagen a mano.')

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
