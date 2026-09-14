#!/usr/bin/env python3
"""
Lista publica de productos del almacen — Proyecto Ubuntu.

Toma los productos y el stock desde la API de Loyverse (o desde un export CSV)
y genera la pagina que se linkea en la descripcion de WhatsApp.

El token se lee de la variable de entorno LOYVERSE_TOKEN. Nunca se escribe
en el codigo ni se sube al repositorio.

    export LOYVERSE_TOKEN="..."          # Linux / macOS
    set LOYVERSE_TOKEN=...               # Windows cmd

Comandos:

    python3 lista_almacen.py explorar
        Muestra las tiendas, cuantos productos hay y un item completo tal cual
        lo devuelve la API. Sirve para verificar el token y los nombres de los
        campos antes de generar nada.

    python3 lista_almacen.py generar
        Consulta la API y escribe index.html y lista.txt.

    python3 lista_almacen.py generar --csv export_items.csv
        Lo mismo pero desde un export manual, sin tocar la API.

Solo usa la biblioteca estandar de Python 3.8+: no hay que instalar nada.
"""

import argparse
import csv
import html
import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ---------------------------------------------------------------- parametros

# Estos dos textos aparecen arriba de la lista. Se pueden editar libremente.
HORARIOS = "Lunes y jueves de 17 a 19 h"
QUIENES = ("Venta interna de la comunidad Ubuntu, solamente para integrantes "
           "del grupo de compras colectivas.")

TIENDA_CSV = "ubuntu"      # nombre de la tienda en las columnas del export CSV
TIENDA_API = None          # nombre de la tienda en la API; None = la primera

MINIMO_KG = 0.02           # menos de 20 g es resto de balanza, no se publica
UMBRAL_POCO_KG = 0.3       # menos de esto en granel -> "queda poco"
UMBRAL_POCO_UNIDAD = 2     # menos o igual a esto en unidades -> "queda poco"

API = "https://api.loyverse.com/v1.0"
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
         "agosto", "setiembre", "octubre", "noviembre", "diciembre"]
UY = timezone(timedelta(hours=-3))

# ------------------------------------------------------------------ utilidad


def limpiar_html(texto):
    """Saca etiquetas y entidades del campo descripcion."""
    if not texto:
        return ""
    texto = re.sub(r"</p>\s*<p>", " · ", texto)
    texto = re.sub(r"<[^>]+>", " ", texto)
    texto = html.unescape(texto).replace("\xa0", " ")
    return " ".join(texto.split()).strip(" ·")


def normalizar(texto):
    """Para ordenar y buscar sin acentos ni mayusculas."""
    texto = unicodedata.normalize("NFD", texto.lower())
    return "".join(c for c in texto if unicodedata.category(c) != "Mn")


def a_numero(valor):
    try:
        return float(str(valor).replace(",", ".").strip())
    except (TypeError, ValueError):
        return 0.0


def pesos(monto):
    return "$ " + f"{monto:,.0f}".replace(",", ".")


def fecha_larga(momento):
    return f"{momento.day} de {MESES[momento.month - 1]} de {momento.year}"


def clasificar(nombre, detalle, precio, por_peso, stock, sigue_stock):
    """Arma el registro de un producto, o None si no se publica."""
    if not nombre or precio <= 0:
        return None
    if sigue_stock:
        minimo = MINIMO_KG if por_peso else 1
        if stock < minimo:
            return None
        poco = stock < UMBRAL_POCO_KG if por_peso else stock <= UMBRAL_POCO_UNIDAD
    else:
        poco = False

    if normalizar(detalle) == normalizar(nombre):
        detalle = ""

    return {"nombre": nombre, "detalle": detalle, "precio": precio,
            "por_peso": por_peso, "poco": poco}


# ----------------------------------------------------------------- API cliente


def token_o_salir():
    token = os.environ.get("LOYVERSE_TOKEN", "").strip()
    if not token:
        sys.exit("Falta la variable de entorno LOYVERSE_TOKEN.\n"
                 '  export LOYVERSE_TOKEN="tu-token"')
    return token


def pedir(ruta, token, **params):
    """GET a la API, con reintento si aparece el limite de pedidos."""
    url = f"{API}/{ruta}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    pedido = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    })
    for intento in range(4):
        try:
            with urllib.request.urlopen(pedido, timeout=30) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            cuerpo = e.read().decode("utf-8", "replace")[:300]
            if e.code == 429 and intento < 3:
                time.sleep(2 ** intento)
                continue
            if e.code == 401:
                sys.exit("La API rechazó el token (401). Revisá que esté vigente "
                         "y que tenga permiso de lectura de items e inventario.")
            sys.exit(f"Error {e.code} al pedir {ruta}: {cuerpo}")
        except urllib.error.URLError as e:
            sys.exit(f"No se pudo conectar con la API: {e.reason}")
    sys.exit(f"La API siguió respondiendo 429 al pedir {ruta}.")


def paginar(ruta, clave, token, **params):
    """Recorre todas las paginas de un endpoint y devuelve la lista completa."""
    salida, cursor = [], None
    while True:
        p = dict(params, limit=250)
        if cursor:
            p["cursor"] = cursor
        datos = pedir(ruta, token, **p)
        salida.extend(datos.get(clave, []))
        cursor = datos.get("cursor")
        if not cursor:
            return salida


def elegir_tienda(token):
    tiendas = paginar("stores", "stores", token)
    if not tiendas:
        sys.exit("La cuenta no tiene tiendas.")
    if TIENDA_API:
        for t in tiendas:
            if normalizar(t.get("name", "")) == normalizar(TIENDA_API):
                return t
        sys.exit(f'No encontré la tienda "{TIENDA_API}". '
                 f'Hay: {", ".join(t.get("name", "?") for t in tiendas)}')
    return tiendas[0]


def leer_desde_api(token, tienda):
    """Devuelve la lista de productos publicables segun la API."""
    store_id = tienda["id"]

    stock = {}
    for nivel in paginar("inventory", "inventory_levels", token, store_id=store_id):
        stock[nivel.get("variant_id")] = a_numero(nivel.get("in_stock"))

    productos = []
    for item in paginar("items", "items", token):
        if item.get("deleted_at"):
            continue
        nombre_item = (item.get("item_name") or "").strip()
        detalle = limpiar_html(item.get("description"))
        por_peso = bool(item.get("sold_by_weight"))
        sigue_stock = bool(item.get("track_stock"))

        for variante in item.get("variants", []):
            fila = next((s for s in variante.get("stores", [])
                         if s.get("store_id") == store_id), None)
            if fila is None or not fila.get("available_for_sale", True):
                continue
            # los de precio variable no tienen precio fijo para publicar
            if (fila.get("pricing_type") or "FIXED").upper() != "FIXED":
                continue

            sufijo = (variante.get("variant_name") or "").strip()
            nombre = f"{nombre_item} {sufijo}".strip() if sufijo else nombre_item

            p = clasificar(
                nombre=nombre,
                detalle=detalle,
                precio=a_numero(fila.get("price")),
                por_peso=por_peso,
                stock=stock.get(variante.get("variant_id"), 0.0),
                sigue_stock=sigue_stock,
            )
            if p:
                productos.append(p)

    productos.sort(key=lambda p: normalizar(p["nombre"]))
    return productos


# ------------------------------------------------------------------- CSV


def leer_desde_csv(ruta_csv):
    col_precio = f"Precio [{TIENDA_CSV}]"
    col_stock = f"En inventario [{TIENDA_CSV}]"
    col_venta = f"Disponibles para la venta [{TIENDA_CSV}]"

    productos = []
    with open(ruta_csv, newline="", encoding="utf-8-sig") as f:
        for fila in csv.DictReader(f):
            if (fila.get(col_venta) or "Y").strip().upper() != "Y":
                continue
            p = clasificar(
                nombre=(fila.get("Nombre") or "").strip(),
                detalle=limpiar_html(fila.get("Descripción")),
                precio=a_numero(fila.get(col_precio)),
                por_peso=(fila.get("Vendido por peso") or "N").strip().upper() == "Y",
                stock=a_numero(fila.get(col_stock)),
                sigue_stock=(fila.get("Seguir el Inventario") or "N").strip().upper() == "Y",
            )
            if p:
                productos.append(p)

    productos.sort(key=lambda p: normalizar(p["nombre"]))
    return productos


# ------------------------------------------------------------------- salida


def fila_html(p):
    detalle = f'<span class="detalle">{html.escape(p["detalle"])}</span>' if p["detalle"] else ""
    poco = '<span class="poco">queda poco</span>' if p["poco"] else ""
    buscar = html.escape(normalizar(p["nombre"] + " " + p["detalle"]))
    return (
        f'    <li data-buscar="{buscar}">\n'
        f'      <span class="nombre">{html.escape(p["nombre"])}{poco}{detalle}</span>\n'
        f'      <span class="linea" aria-hidden="true"></span>\n'
        f'      <span class="precio">{pesos(p["precio"])}</span>\n'
        f"    </li>\n"
    )


def armar_html(productos, momento):
    secciones = ""
    for titulo, nota, items in (
        ("A granel", "el precio es por kilo · llevás la cantidad que quieras",
         [p for p in productos if p["por_peso"]]),
        ("Por unidad", "precio de cada uno",
         [p for p in productos if not p["por_peso"]]),
    ):
        if not items:
            continue
        secciones += (
            f'<section class="grupo">\n  <h2>{titulo}</h2>\n'
            f'  <p class="nota">{nota}</p>\n  <ul class="lista">\n'
            + "".join(fila_html(p) for p in items)
            + "  </ul>\n</section>\n"
        )

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow, noarchive, nosnippet">
<title>Qué hay en el almacén — Proyecto Ubuntu</title>
<meta name="description" content="Productos disponibles y precios del almacén de Proyecto Ubuntu.">
<meta property="og:title" content="Qué hay en el almacén">
<meta property="og:description" content="Productos disponibles y precios · actualizado el {fecha_larga(momento)}">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,500;9..144,700&family=Archivo:wght@400;500;600&display=swap" rel="stylesheet">
<style>
  :root {{
    --papel: #eceee4;
    --tinta: #23281f;
    --tinta-suave: #6b7263;
    --linea: #c8ccbc;
    --verde: #3d5a3c;
    --ambar: #8a5a12;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; background: var(--papel); color: var(--tinta);
    font-family: Archivo, system-ui, sans-serif;
    font-size: 17px; line-height: 1.4; -webkit-text-size-adjust: 100%;
  }}
  .hoja {{ max-width: 34rem; margin: 0 auto; padding: 2rem 1.25rem 4rem; }}
  header h1 {{
    font-family: Fraunces, Georgia, serif; font-weight: 700;
    font-size: clamp(2.3rem, 11vw, 3.2rem); line-height: 0.98;
    letter-spacing: -0.015em; margin: 0 0 0.6rem;
  }}
  .aviso {{
    margin: 0 0 0.35rem; padding-left: 0.85rem;
    border-left: 3px solid var(--verde);
    font-size: 0.95rem; max-width: 30rem;
  }}
  .horario {{
    margin: 0 0 1.1rem; padding-left: 0.85rem;
    border-left: 3px solid var(--verde);
    font-family: Fraunces, Georgia, serif;
    font-size: 1.15rem; font-weight: 500; color: var(--verde);
  }}
  .actualizado {{ color: var(--tinta-suave); font-size: 0.9rem; margin: 0 0 1.5rem; }}
  .actualizado strong {{ color: var(--verde); font-weight: 600; }}
  .buscador {{
    position: sticky; top: 0; z-index: 2; padding: 0.75rem 0 0.9rem;
    background: linear-gradient(var(--papel) 72%, rgba(236,238,228,0));
  }}
  .buscador input {{
    width: 100%; padding: 0.7rem 0.9rem; font: inherit; color: inherit;
    background: #f7f8f2; border: 1px solid var(--linea); border-radius: 2px;
  }}
  .buscador input:focus-visible {{ outline: 2px solid var(--verde); outline-offset: 1px; }}
  .grupo {{ margin-top: 2rem; }}
  .grupo h2 {{
    font-family: Fraunces, Georgia, serif; font-weight: 500; font-size: 1.35rem;
    margin: 0; padding-bottom: 0.2rem; border-bottom: 2px solid var(--tinta);
  }}
  .nota {{ color: var(--tinta-suave); font-size: 0.85rem; margin: 0.4rem 0 0.9rem; }}
  .lista {{ list-style: none; margin: 0; padding: 0; }}
  .lista li {{
    display: flex; align-items: baseline; gap: 0.4rem;
    padding: 0.5rem 0; border-bottom: 1px solid var(--linea);
  }}
  .nombre {{ flex: 0 1 auto; }}
  .detalle {{ display: block; font-size: 0.82rem; color: var(--tinta-suave); }}
  .poco {{
    margin-left: 0.45rem; font-size: 0.72rem; color: var(--ambar);
    border: 1px solid currentColor; border-radius: 999px;
    padding: 0.05rem 0.4rem; white-space: nowrap;
  }}
  .linea {{
    flex: 1 1 auto; min-width: 1.5rem;
    border-bottom: 1px dotted var(--linea); transform: translateY(-0.25rem);
  }}
  .precio {{ font-variant-numeric: tabular-nums; font-weight: 600; white-space: nowrap; }}
  .sin-resultados {{ display: none; padding: 1.5rem 0; color: var(--tinta-suave); }}
  body.vacio .sin-resultados {{ display: block; }}
  body.vacio .grupo {{ display: none; }}
  footer {{
    margin-top: 2.5rem; padding-top: 1.2rem; border-top: 1px solid var(--linea);
    font-size: 0.85rem; color: var(--tinta-suave);
  }}
</style>
</head>
<body>
<div class="hoja">

<header>
  <h1>Qué hay hoy<br>en el almacén</h1>
  <p class="aviso">{html.escape(QUIENES)}</p>
  <p class="horario">{html.escape(HORARIOS)}</p>
  <p class="actualizado">Actualizado el <strong>{fecha_larga(momento)}</strong> a las {momento:%H:%M} · {len(productos)} productos disponibles</p>
</header>

<div class="buscador">
  <input id="q" type="search" placeholder="Buscar producto" aria-label="Buscar producto" autocomplete="off">
</div>

{secciones}
<p class="sin-resultados">No hay ningún producto con ese nombre.</p>

<footer>
  Los precios pueden cambiar. Si algo se agotó entre una actualización y otra, avisamos por el grupo.
</footer>

</div>
<script>
  const q = document.getElementById('q');
  const items = Array.from(document.querySelectorAll('.lista li'));
  const grupos = Array.from(document.querySelectorAll('.grupo'));
  q.addEventListener('input', () => {{
    const texto = q.value.toLowerCase()
      .normalize('NFD').replace(/[\\u0300-\\u036f]/g, '').trim();
    let visibles = 0;
    items.forEach(li => {{
      const coincide = !texto || li.dataset.buscar.includes(texto);
      li.hidden = !coincide;
      if (coincide) visibles++;
    }});
    grupos.forEach(g => {{ g.hidden = !g.querySelector('.lista li:not([hidden])'); }});
    document.body.classList.toggle('vacio', visibles === 0);
  }});
</script>
</body>
</html>
"""


def armar_txt(productos, momento):
    granel = [p for p in productos if p["por_peso"]]
    unidad = [p for p in productos if not p["por_peso"]]
    lineas = [f"*ALMACÉN UBUNTU — {fecha_larga(momento)}*", "", QUIENES,
              f"Abrimos {HORARIOS[0].lower()}{HORARIOS[1:]}", ""]
    if granel:
        lineas.append("*A granel* (precio por kilo)")
        lineas += [f"{p['nombre']} — {pesos(p['precio'])}" for p in granel]
        lineas.append("")
    if unidad:
        lineas.append("*Por unidad*")
        lineas += [f"{p['nombre']} — {pesos(p['precio'])}" for p in unidad]
    return "\n".join(lineas) + "\n"


# ------------------------------------------------------------------ comandos


def cmd_explorar(args):
    token = token_o_salir()

    tiendas = paginar("stores", "stores", token)
    print("TIENDAS")
    for t in tiendas:
        print(f"  {t.get('name')!r}  id={t.get('id')}")
    tienda = elegir_tienda(token)
    print(f"\nUsando: {tienda.get('name')!r}\n")

    items = paginar("items", "items", token)
    print(f"ITEMS: {len(items)}")
    niveles = paginar("inventory", "inventory_levels", token, store_id=tienda["id"])
    print(f"NIVELES DE INVENTARIO: {len(niveles)}")

    if items:
        print("\nUN ITEM COMPLETO, TAL CUAL LO DEVUELVE LA API:")
        print(json.dumps(items[0], indent=2, ensure_ascii=False))
    if niveles:
        print("\nUN NIVEL DE INVENTARIO:")
        print(json.dumps(niveles[0], indent=2, ensure_ascii=False))

    productos = leer_desde_api(token, tienda)
    print(f"\nQuedarían {len(productos)} productos publicables.")
    for p in productos[:10]:
        unidad = "el kilo" if p["por_peso"] else "c/u"
        print(f"  {p['nombre']:<32} {pesos(p['precio']):>9}  {unidad}"
              + ("  (queda poco)" if p["poco"] else ""))
    if len(productos) > 10:
        print(f"  ... y {len(productos) - 10} más")


def cmd_generar(args):
    if args.csv:
        productos = leer_desde_csv(args.csv)
        origen = f"CSV {args.csv}"
    else:
        token = token_o_salir()
        tienda = elegir_tienda(token)
        productos = leer_desde_api(token, tienda)
        origen = f"API · tienda {tienda.get('name')!r}"

    if not productos:
        sys.exit("No hay ningún producto publicable. No se sobrescribe la página.")

    salida = Path(args.salida)
    salida.mkdir(parents=True, exist_ok=True)
    momento = datetime.now(UY)
    (salida / "index.html").write_text(armar_html(productos, momento), encoding="utf-8")
    (salida / "lista.txt").write_text(armar_txt(productos, momento), encoding="utf-8")
    print(f"{len(productos)} productos ({origen}) -> {salida/'index.html'}")


def main():
    parser = argparse.ArgumentParser(description="Lista pública del almacén.")
    sub = parser.add_subparsers(dest="comando", required=True)

    e = sub.add_parser("explorar", help="verificar el token y ver qué devuelve la API")
    e.set_defaults(func=cmd_explorar)

    g = sub.add_parser("generar", help="escribir index.html y lista.txt")
    g.add_argument("--csv", help="usar un export CSV en vez de la API")
    g.add_argument("-o", "--salida", default="publicado", help="carpeta de salida")
    g.set_defaults(func=cmd_generar)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
