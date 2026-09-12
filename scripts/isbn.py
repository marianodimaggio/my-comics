#!/usr/bin/env python3
"""Propone el ISBN de cada tomo consultando Open Library y Google Books.

Las dos APIs son gratis y no piden clave. Se busca por titulo y editorial, y
lo que encuentra queda en el campo isbn_candidatos de cada tomo, para revisar.
Solo se asigna solo cuando hay una unica coincidencia fuerte: titulo y
editorial iguales. Nunca pisa un ISBN ya cargado.

Por que importa: con ISBN el buscador de tiendas pasa a confianza alta, y la
tapa se puede traer de covers.openlibrary.org sin depender de Whakoom.

Limite conocido: estas bases cubren bien la edicion espanola y la americana,
y bastante mal la historieta argentina independiente. Ahi va a devolver poco.

Uso:
    python3 scripts/isbn.py --coleccion "Obras completas Crumb"
    python3 scripts/isbn.py --cantidad 20
    python3 scripts/isbn.py --dry-run
"""

import argparse
import json
import re
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
COMICS = RAIZ / "data" / "comics.json"
LOG = RAIZ / "data" / "ultima-isbn.json"
HOY = date.today().isoformat()

UA = "Mozilla/5.0 (compatible; catalogo-comics/1.0; uso personal)"
PAUSA = 1.2
VACIAS = {"de", "la", "el", "los", "las", "un", "una", "y", "en", "a", "del", "al", "the", "of"}
# Palabras que aparecen en el nombre de media industria: si dos editoriales solo
# comparten una de estas, no son la misma. "OVNI Press" y "CRC Press" no lo son.
GENERICAS = {"press", "ediciones", "edicion", "editorial", "editores", "books", "book",
             "comics", "comic", "publishing", "publishers", "publications", "media",
             "group", "editions", "libros", "edizioni", "verlag", "independently",
             "createspace", "platform", "pub", "sociedad", "limitada"}


def a_isbn13(isbn):
    """Convierte un ISBN-10 a su ISBN-13 para poder comparar los dos formatos."""
    isbn = re.sub(r"[^0-9Xx]", "", isbn or "").upper()
    if len(isbn) == 13:
        return isbn
    if len(isbn) != 10:
        return None
    cuerpo = "978" + isbn[:9]
    suma = sum((1 if n % 2 == 0 else 3) * int(d) for n, d in enumerate(cuerpo))
    return cuerpo + str((10 - suma % 10) % 10)


def normalizar(s):
    s = unicodedata.normalize("NFKD", (s or "").lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9 ]+", " ", s)


def palabras(s):
    return {p for p in normalizar(sin_anio(s)).split() if len(p) > 3 and p not in VACIAS}


def sin_anio(t):
    return re.sub(r"\s*\(\d{4}\)\s*$", "", t or "")


def tapa_por_isbn(isbn):
    """Tapa de Open Library. Con default=false devuelve 404 si no la tiene,
    asi que no hay riesgo de guardar la imagen en blanco que sirve por defecto."""
    url = f"https://covers.openlibrary.org/b/isbn/{isbn}-L.jpg?default=false"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA}, method="HEAD")
        with urllib.request.urlopen(req, timeout=15) as r:
            if r.status == 200 and int(r.headers.get("Content-Length") or 1000) > 600:
                return url.split("?")[0]
    except Exception:
        pass
    return None


def pedir(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read(400_000).decode("utf-8", errors="replace"))


def open_library(titulo, editorial):
    q = urllib.parse.urlencode({"title": sin_anio(titulo), "limit": 6,
                                "fields": "title,publisher,isbn,first_publish_year"})
    try:
        datos = pedir("https://openlibrary.org/search.json?" + q)
    except Exception as e:
        print(f"      Open Library: {str(e)[:60]}")
        return []
    salida = []
    for d in datos.get("docs", []):
        for i in (d.get("isbn") or [])[:3]:
            salida.append({"isbn": i, "titulo": d.get("title"),
                           "editorial": ", ".join((d.get("publisher") or [])[:2]) or None,
                           "anio": d.get("first_publish_year"), "fuente": "Open Library"})
    return salida


GOOGLE_CAIDO = False


def google_books(titulo, editorial):
    global GOOGLE_CAIDO
    if GOOGLE_CAIDO:
        return []
    q = f'intitle:"{sin_anio(titulo)}"'
    if editorial:
        q += f' inpublisher:"{editorial}"'
    url = "https://www.googleapis.com/books/v1/volumes?" + urllib.parse.urlencode(
        {"q": q, "maxResults": 6})
    try:
        datos = pedir(url)
    except Exception as e:
        if not GOOGLE_CAIDO:
            print(f"      Google Books no responde ({str(e)[:40]}). "
                  f"Sigo solo con Open Library.")
            GOOGLE_CAIDO = True
        return []
    salida = []
    for d in datos.get("items", []):
        v = d.get("volumeInfo", {})
        for ident in v.get("industryIdentifiers", []):
            if ident.get("type") in ("ISBN_13", "ISBN_10"):
                salida.append({"isbn": ident["identifier"], "titulo": v.get("title"),
                               "editorial": v.get("publisher"),
                               "anio": (v.get("publishedDate") or "")[:4] or None,
                               "fuente": "Google Books"})
    return salida


def puntuar(item, c):
    """alta si coinciden titulo y editorial, media si solo el titulo."""
    esperadas = palabras(item.get("titulo"))
    if not esperadas:
        return None
    if not esperadas <= palabras(c.get("titulo")):
        return None
    ed_item = palabras(item.get("editorial")) - GENERICAS
    ed_cand = palabras(c.get("editorial")) - GENERICAS
    return "alta" if (ed_item and ed_item & ed_cand) else "media"


def buscar(item):
    if not item.get("titulo"):
        print("      sin titulo, no hay por donde buscar")
        return []
    crudos = open_library(item["titulo"], item.get("editorial"))
    time.sleep(PAUSA)
    crudos += google_books(item["titulo"], item.get("editorial"))
    time.sleep(PAUSA)

    # El mismo libro aparece con ISBN-10 y con ISBN-13: se unifica a 13 para no
    # contarlo dos veces, que era lo que impedia asignarlo solo.
    vistos, salida = set(), []
    for c in crudos:
        isbn = a_isbn13(c.get("isbn"))
        if not isbn or isbn in vistos:
            continue
        conf = puntuar(item, c)
        if not conf:
            continue
        vistos.add(isbn)
        c["isbn"] = isbn
        c["confianza"] = conf
        c["verificado"] = HOY
        salida.append(c)
    salida.sort(key=lambda c: 0 if c["confianza"] == "alta" else 1)
    return salida[:4]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", help="buscar el ISBN de un solo tomo")
    ap.add_argument("--coleccion")
    ap.add_argument("--cantidad", type=int, default=15)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    data = json.loads(COMICS.read_text(encoding="utf-8"))
    # primero los que tienen ISBN cargado y les falta la tapa: es gratis y sale rapido
    con_isbn_sin_tapa = [i for i in data["items"]
                         if i["estado"] != "descartada" and i.get("isbn") and not i.get("cover_url")]
    if args.id:
        # Un tomo puntual: se busca aunque ya tenga ISBN, porque lo pediste vos.
        objetivo = [i for i in data["items"] if i["id"] == args.id]
        if objetivo and not objetivo[0].get("titulo"):
            objetivo[0]["titulo"] = objetivo[0]["coleccion"]
            print("Sin titulo cargado: busco por el nombre de la coleccion.\n")
    else:
        objetivo = [i for i in data["items"]
                    if i["estado"] != "descartada" and not i.get("isbn") and i.get("titulo")]
        if args.coleccion:
            objetivo = [i for i in objetivo if i["coleccion"] == args.coleccion]
        else:
            objetivo = objetivo[:args.cantidad]

    if not objetivo:
        print("No hay tomos que buscar con ese criterio.", file=sys.stderr)
        return 1

    if con_isbn_sin_tapa and not args.coleccion and not args.id:
        print(f"{len(con_isbn_sin_tapa)} tomos tienen ISBN y les falta la tapa:")
        for item in con_isbn_sin_tapa:
            tapa = tapa_por_isbn(item["isbn"])
            time.sleep(PAUSA)
            print(f'   {item["coleccion"]} #{item["numero"]}: '
                  f'{"tapa conseguida" if tapa else "Open Library no la tiene"}')
            if tapa:
                item["cover_url"] = tapa
                item.setdefault("historial", []).append(
                    f"{HOY}: tapa desde Open Library, por ISBN")
        print()

    print(f"Buscando ISBN para {len(objetivo)} tomos\n")
    resumen = {"asignados": 0, "propuestos": 0, "sin_nada": 0, "tapas": 0}

    for n, item in enumerate(objetivo, 1):
        print(f'[{n}/{len(objetivo)}] {item["coleccion"]} #{item["numero"]} — {item["titulo"]}')
        cands = buscar(item)
        if not cands:
            resumen["sin_nada"] += 1
            print("      sin resultados")
            continue
        item["isbn_candidatos"] = cands
        altas = [c for c in cands if c["confianza"] == "alta"]
        if len(altas) == 1:
            item["isbn"] = altas[0]["isbn"]
            item.setdefault("historial", []).append(
                f'{HOY}: ISBN {altas[0]["isbn"]} asignado desde {altas[0]["fuente"]} '
                f'(coinciden titulo y editorial)')
            resumen["asignados"] += 1
            print(f'      ASIGNADO {altas[0]["isbn"]} ({altas[0]["fuente"]})')
            if not item.get("cover_url"):
                tapa = tapa_por_isbn(item["isbn"])
                time.sleep(PAUSA)
                if tapa:
                    item["cover_url"] = tapa
                    item["historial"].append(f"{HOY}: tapa desde Open Library, por ISBN")
                    resumen["tapas"] = resumen.get("tapas", 0) + 1
                    print(f"      tapa conseguida: {tapa}")
        else:
            resumen["propuestos"] += 1
            for c in cands:
                print(f'      {c["confianza"]}: {c["isbn"]} — {c["titulo"]} — '
                      f'{c.get("editorial") or "sin editorial"} — {c["fuente"]}')

    print("\n" + " · ".join(f"{k}: {v}" for k, v in resumen.items()))
    if args.dry_run:
        print("\n--dry-run: no escribi nada.")
        return 0

    data["actualizado"] = HOY
    COMICS.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    LOG.write_text(json.dumps({"corrida": HOY, "resumen": resumen}, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    print("\nEscribi data/comics.json y data/ultima-isbn.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
