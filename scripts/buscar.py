#!/usr/bin/env python3
"""Busca candidatos de compra para los tomos pendientes y los guarda en el JSON.

No usa buscadores generales: recorre el buscador propio de cada comiquería de
la lista blanca. Casi todas son Tiendanube, que expone la búsqueda en
/search/?q= y el precio y el stock en los meta tags del producto.

Lo que NO hace, a propósito:
  - No marca nada como "encontrado". Escribe candidatos en el campo
    "candidatos" del item, con nivel de confianza, para que los revises.
  - No toca las tiendas que bloquean acceso automatizado (robots.txt) ni
    MercadoLibre, que rechaza bots.
  - No inventa: si no encuentra el precio en la página, descarta el candidato.

Confianza:
  alta   el ISBN del tomo aparece en la página del producto
  media  el título del tomo aparece en el título del producto
  baja   coincide la colección y el número, pero no el título

Uso:
    python3 scripts/buscar.py --coleccion "Obras completas Crumb"
    python3 scripts/buscar.py --pendientes 15        # los 15 mas prioritarios
    python3 scripts/buscar.py --id obras-completas-crumb-10 --dry-run
"""

import argparse
import json
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path
from urllib.parse import urljoin, quote

RAIZ = Path(__file__).resolve().parent.parent
JSON = RAIZ / "data" / "comics.json"
LOG = RAIZ / "data" / "ultima-busqueda.json"
HOY = date.today().isoformat()

UA = ("Mozilla/5.0 (compatible; catalogo-comics/1.0; "
      "busqueda de precios de uso personal)")
PAUSA = 1.5          # segundos entre pedidos
MAX_PRODUCTOS = 4    # productos a abrir por tienda y por tomo
MAX_CANDIDATOS = 3   # candidatos que se guardan por tomo

# Lista blanca. Tiendanube expone /search/?q= y los productos en /productos/.
# Excluidas a propósito: Hotel de las Ideas (robots.txt), MercadoLibre (bloquea
# bots), Buscalibre y librerías españolas (renderizan con JavaScript).
PATRONES = ["/search/?q=",        # Tiendanube
            "/buscar?q=",         # Empretienda y varios
            "/?s=",               # WordPress / WooCommerce
            "/search?q=",
            "/busqueda?q="]

TIENDAS = [
    {"nombre": "Historieteca (editorial)", "base": "https://historieteca.mitiendanube.com",
     "editorial": "Historieteca"},
    {"nombre": "Cultura Guiso", "base": "https://culturaguiso.com"},
    {"nombre": "It's a Trap!", "base": "https://www.itsatrapcomicstore.ar"},
    {"nombre": "Rey Esteban", "base": "https://www.reyesteban.com"},
    {"nombre": "Quiosquito Virtual", "base": "https://www.quiosquitovirtual.com.ar"},
    # agregadas 2026-09-10; plataforma sin confirmar, el script prueba patrones
    {"nombre": "La Revisteria", "base": "https://www.larevisteria.com"},
    {"nombre": "Crossover", "base": "https://crossovercomics.com.ar"},
    {"nombre": "La Vineta Oculta", "base": "https://lavinetaoculta.empretienda.com.ar"},
    {"nombre": "La Galera Comics", "base": "https://www.lagaleracomics.com.ar"},
]

# Tiendas de editorial que NO se pueden leer automaticamente. No se scrapean,
# pero tampoco se ignoran: el script deja el aviso en el item para revisarlas
# a mano, porque son la primera opcion del criterio de compra.
SOLO_A_MANO = [
    {"nombre": "Hotel de las Ideas (editorial)",
     "base": "https://hoteldelasideastienda.com.ar",
     "editorial": "Hotel de las Ideas",
     "motivo": "el sitio bloquea acceso automatizado (robots.txt)"},
]


def tiendas_para(item):
    """La tienda de la editorial primero; despues las comiquerias generales.

    Si la editorial del tomo se conoce, se saltean las tiendas propias de OTRAS
    editoriales: no venden catalogo ajeno, son pedidos al vacio.
    """
    ed = item.get("editorial")
    propias = [t for t in TIENDAS if ed and t.get("editorial") == ed]
    generales = [t for t in TIENDAS
                 if t not in propias and not (ed and t.get("editorial"))]
    if not ed:
        generales = [t for t in TIENDAS if t not in propias]
    return propias + generales


def aviso_a_mano(item):
    """Si la editorial del tomo tiene tienda que no se puede leer, lo registra."""
    ed = item.get("editorial")
    for t in SOLO_A_MANO:
        if ed and t["editorial"] == ed:
            return {"tienda": t["nombre"], "motivo": t["motivo"],
                    "url": f'{t["base"]}/search/?q={quote(terminos(item)[-2] if len(terminos(item)) > 1 else terminos(item)[0])}'}
    return None

PALABRAS_VACIAS = {"de", "la", "el", "los", "las", "un", "una", "y", "en", "a",
                   "del", "al", "mi", "mis", "tu", "su", "lo", "por", "con"}


# ----------------------------------------------------------------- utilidades
def normalizar(s):
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s.lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9 ]+", " ", s)


def sin_anio(titulo):
    return re.sub(r"\s*\(\d{4}\)\s*$", "", titulo or "")


def significativas(titulo):
    """Palabras utiles de un titulo: sin articulos, sin numeros, sin el anio."""
    return {p for p in normalizar(sin_anio(titulo)).split()
            if len(p) > 3 and not p.isdigit() and p not in PALABRAS_VACIAS}


def leer(url, limite=600_000):
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept-Language": "es-AR,es"})
    with urllib.request.urlopen(req, timeout=25) as r:
        return r.read(limite).decode("utf-8", errors="replace")


def meta(html_txt, clave):
    m = re.search(r'<meta[^>]+(?:property|name)=["\']' + re.escape(clave) +
                  r'["\'][^>]+content=["\']([^"\']*)["\']', html_txt, re.I)
    if m:
        return m.group(1)
    m = re.search(r'<meta[^>]+content=["\']([^"\']*)["\'][^>]+(?:property|name)=["\']' +
                  re.escape(clave) + r'["\']', html_txt, re.I)
    return m.group(1) if m else None


def links_producto(html_txt, base):
    """URLs de producto que aparecen en una página de resultados."""
    crudos = re.findall(r"""href=["']([^"']*?/(?:productos?|product|item)/[^"'?#]+)""",
                        html_txt, re.I)
    vistos, salida = set(), []
    for c in crudos:
        u = urljoin(base, c).rstrip("/") + "/"
        if u not in vistos:
            vistos.add(u)
            salida.append(u)
    return salida[:MAX_PRODUCTOS]


# ------------------------------------------------------------------ búsqueda
def terminos(item):
    """Qué buscar, del término más preciso al más amplio."""
    t = []
    if item.get("isbn"):
        t.append(item["isbn"])
    if item.get("titulo"):
        t.append(sin_anio(item["titulo"]))
    t.append(f'{item["coleccion"]} {item["numero"]}')
    return t


def evaluar(item, pagina, url):
    """Convierte una página de producto en candidato, o devuelve None."""
    precio = meta(pagina, "tiendanube:price") or meta(pagina, "product:price:amount")
    if not precio:
        return None                      # sin precio no es candidato
    titulo = meta(pagina, "og:title") or ""
    stock = meta(pagina, "tiendanube:stock")

    encontradas = significativas(titulo)
    esperadas = significativas(item.get("titulo"))
    del_coleccion = significativas(item["coleccion"])

    if item.get("isbn") and item["isbn"] in pagina.replace("-", ""):
        confianza = "alta"
    elif esperadas and esperadas <= encontradas:
        confianza = "media"
    elif encontradas & (esperadas | del_coleccion):
        confianza = "baja"
    else:
        return None      # no comparte ni una palabra: no es este comic

    return {
        "titulo_publicacion": titulo.strip(),
        "url": url,
        "precio": int(float(precio)),
        "stock": (int(stock) if stock and stock.isdigit() else None),
        "confianza": confianza,
        "verificado": HOY,
    }


def buscar_item(item, dry):
    hallados = []

    manual = aviso_a_mano(item)
    if manual:
        item["revisar_a_mano"] = manual
        print(f'      PRIMERO A MANO: {manual["tienda"]} — {manual["motivo"]}')
        print(f'      {manual["url"]}')

    for tienda in tiendas_para(item):
        productos, usado = [], None

        # Prueba los patrones de busqueda hasta que uno devuelva productos.
        # El que funciona queda recordado para los tomos siguientes.
        for termino in terminos(item):
            for patron in ([tienda["patron"]] if tienda.get("patron") else PATRONES):
                try:
                    res = leer(f'{tienda["base"]}{patron}{quote(termino)}')
                except urllib.error.HTTPError as e:
                    if e.code in (403, 429):
                        print(f'      {tienda["nombre"]}: HTTP {e.code}, bloquea bots')
                        usado = "bloqueada"
                        break
                    continue
                except Exception as e:
                    print(f'      {tienda["nombre"]}: {str(e)[:50]}')
                    continue
                time.sleep(PAUSA)
                urls = links_producto(res, tienda["base"])
                if urls:
                    productos, usado = urls, patron
                    tienda["patron"] = patron
                    break
            if usado:
                break

        if usado and usado != "bloqueada":
            print(f'      {tienda["nombre"]}: {len(productos)} resultado(s) con {usado}')
        elif not usado:
            print(f'      {tienda["nombre"]}: sin resultados')
        if not productos:
            continue

        for prod in productos:
            try:
                pagina = leer(prod)
            except Exception:
                continue
            time.sleep(PAUSA)
            cand = evaluar(item, pagina, prod)
            if cand:
                cand["tienda"] = tienda["nombre"]
                hallados.append(cand)
                print(f'         -> {cand["confianza"]}: ARS {cand["precio"]} — '
                      f'{cand["titulo_publicacion"][:46]}')

    orden = {"alta": 0, "media": 1, "baja": 2}
    hallados.sort(key=lambda c: (orden[c["confianza"]], c["precio"]))
    return hallados[:MAX_CANDIDATOS]


# ---------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--coleccion")
    ap.add_argument("--id")
    ap.add_argument("--pendientes", type=int, default=0,
                    help="cantidad de tomos pendientes a procesar")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    data = json.loads(JSON.read_text(encoding="utf-8"))
    items = data["items"]

    if args.id:
        objetivo = [i for i in items if i["id"] == args.id]
    elif args.coleccion:
        objetivo = [i for i in items if i["coleccion"] == args.coleccion
                    and i["estado"] != "descartada"]
    else:
        candidatos = [i for i in items if i["estado"] == "pendiente"]
        # primero las colecciones chicas: cierran colección completa
        tamano = {}
        for i in items:
            if i["estado"] != "descartada":
                tamano[i["coleccion"]] = tamano.get(i["coleccion"], 0) + 1
        candidatos.sort(key=lambda i: (tamano[i["coleccion"]], i["coleccion"]))
        objetivo = candidatos[:args.pendientes or 10]

    if not objetivo:
        print("No hay nada que buscar con ese criterio.", file=sys.stderr)
        return 1

    print(f"Buscando {len(objetivo)} tomos en {len(TIENDAS)} tiendas\n")
    resumen, detalle = {"con_candidatos": 0, "sin_nada": 0}, []

    for n, item in enumerate(objetivo, 1):
        print(f'[{n}/{len(objetivo)}] {item["coleccion"]} #{item["numero"]}'
              f'{" — " + item["titulo"] if item.get("titulo") else ""}')
        cands = buscar_item(item, args.dry_run)
        if cands:
            resumen["con_candidatos"] += 1
            item["candidatos"] = cands
            item["historial"].append(
                f'{HOY}: busqueda automatica — {len(cands)} candidato(s), '
                f'mejor confianza {cands[0]["confianza"]}')
        else:
            resumen["sin_nada"] += 1
            item["candidatos"] = []
            print("      sin candidatos")
        detalle.append({"item": item["id"], "candidatos": len(cands)})

    print(f'\ncon candidatos: {resumen["con_candidatos"]} · '
          f'sin nada: {resumen["sin_nada"]}')

    if args.dry_run:
        print("\n--dry-run: no escribi nada.")
        return 0

    data["actualizado"] = HOY
    JSON.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    LOG.write_text(json.dumps({"corrida": HOY, "resumen": resumen, "detalle": detalle},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    print("\nEscribi data/comics.json y data/ultima-busqueda.json")
    print("Los candidatos quedan a revision: ninguno se marco como encontrado.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
