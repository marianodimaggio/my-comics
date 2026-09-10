# Cómics pendientes — catálogo de compra

Catálogo de 481 tomos faltantes de 41 colecciones, con precio y tienda cuando
hay una publicación real detrás. Nada se estima: si no hay publicación
verificable, el tomo queda como pendiente y sin botón de compra.

## Estructura

    index.html                    la interfaz; lee los datos por fetch
    data/comics.json              fuente de datos única
    data/comics.csv               el mismo contenido, para editar a mano
    data/ultima-actualizacion.json  log de la última corrida del regenerador
    scripts/refresh.py            regenerador: revalida precio, stock y tapa
    .github/workflows/refresh.yml  corre el regenerador todos los lunes

`index.html` no contiene datos. Para cambiar un precio se edita
`data/comics.json` y se sube: la página lo toma en la próxima carga.

## Estados

| estado | significa |
|---|---|
| `encontrado` | hay publicación con precio verificable |
| `no_encontrado` | ya se buscó y no apareció publicación confirmada |
| `pendiente` | todavía no se investigó |

Y en paralelo, `tipo_enlace`:

| tipo_enlace | botón | significa |
|---|---|---|
| `especifico` | Comprar | URL del producto exacto |
| `general` | Ver opciones | solo catálogo o listado; falta confirmar el producto |
| `ninguno` | deshabilitado | sin enlace |

## El regenerador

`scripts/refresh.py` recorre los items que ya tienen `url_producto` y relee
precio, stock y tapa desde la página. Solo funciona con tiendas que publican
esos datos en los meta tags (Tiendanube, que es la mayoría de las comiquerías
argentinas). MercadoLibre y las tiendas que arman la página con JavaScript
quedan afuera a propósito: hay que mirarlas a mano.

Trazabilidad: cada cambio de precio o de disponibilidad se agrega al campo
`historial` del item. El script nunca borra un item; si la publicación se cayó,
la marca sin stock y deja constancia.

    python3 scripts/refresh.py --dry-run    # ver qué cambiaría
    python3 scripts/refresh.py              # aplicar
    python3 scripts/refresh.py --solo historieteca

No compra, no agrega al carrito, no inicia sesión.

## Agregar un hallazgo nuevo

Editar el item en `data/comics.json`:

```json
{
  "id": "manta-8",
  "coleccion": "Manta",
  "numero": "8",
  "titulo": null,
  "editorial": "Libera la Bestia",
  "cover_url": null,
  "tienda": "La Viñeta Oculta",
  "url_producto": null,
  "url_general": "https://lavinetaoculta.empretienda.com.ar/editoriales/libera-la-bestia",
  "precio": 20000,
  "precio_transferencia": null,
  "condicion": "nuevo",
  "stock": null,
  "envio": null,
  "fecha_verificacion": "2026-09-09",
  "tipo_enlace": "general",
  "estado": "encontrado",
  "observaciones": "Falta la URL del producto.",
  "historial": []
}
```

Cargar `url_producto` y cambiar `tipo_enlace` a `especifico` es lo que
convierte el botón en "Comprar" y habilita al regenerador a seguir ese tomo.
