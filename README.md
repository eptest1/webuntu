# Lista del almacén — Proyecto Ubuntu

Publica automáticamente los productos con stock y sus precios, tomándolos de
Loyverse. La página resultante es la que se linkea desde la descripción del
grupo de WhatsApp.

- `lista_almacen.py` — genera la página. Único archivo que hay que mantener.
- `.github/workflows/publicar.yml` — la corre sola cada hora y publica.

La página queda en `https://USUARIO.github.io/REPOSITORIO/` y esa dirección no
cambia nunca. También se genera `.../lista.txt`, la misma lista en texto plano
por si se quiere pegar en un mensaje.

## Puesta en marcha

1. **Crear el repositorio en GitHub, público.** Con cuenta gratuita, GitHub
   Pages solo funciona en repositorios públicos. Lo que se publica son
   productos y precios, que de todos modos son públicos; el token no.

2. **Subir estos archivos** (`lista_almacen.py`, `.github/`, `.gitignore`,
   `README.md`).

3. **Guardar el token.** En el repositorio: Settings → Secrets and variables →
   Actions → New repository secret.
   - Name: `LOYVERSE_TOKEN`
   - Secret: el token de Loyverse

   Una vez guardado no se puede volver a ver, solo reemplazar. Nunca aparece en
   los registros de ejecución.

4. **Activar Pages.** Settings → Pages → Build and deployment → Source:
   **GitHub Actions** (no "Deploy from a branch").

5. **Primera publicación.** Actions → "Publicar lista del almacén" → Run
   workflow. Al terminar, el enlace a la página aparece en el resumen de la
   ejecución.

6. **Pegar esa dirección en la descripción del grupo de WhatsApp.**

## Uso diario

Ninguno. La lista se regenera sola cada hora entre las 8 y las 20. Lo que se
carga en Loyverse aparece en la página en la hora siguiente.

Para publicar en el momento: Actions → Run workflow.

## Cambiar la frecuencia

En `publicar.yml`, la línea `cron: '0 11-23 * * *'`. Está en UTC; Uruguay es
UTC-3, así que 11-23 UTC son las 8 a 20 de acá. Algunas alternativas:

- Dos veces por día, 8 y 18: `0 11,21 * * *`
- Cada 30 minutos en horario de atención: `0,30 11-23 * * *`

## Cosas para tener en cuenta

- **Los cron se desactivan solos si el repositorio queda 60 días sin
  actividad.** GitHub avisa por correo antes; se reactiva con un clic en
  Actions. Cualquier cambio que se suba al repositorio reinicia la cuenta.
- **Si la API falla o no devuelve ningún producto, el script corta con error y
  no publica nada.** La página anterior sigue en pie. Conviene revisar de vez
  en cuando la pestaña Actions por ejecuciones en rojo; GitHub también manda
  correo cuando una falla.
- **Si se revoca o vence el token en Loyverse**, hay que reemplazar el secret.
  El error en Actions dice "La API rechazó el token (401)".

## Ajustes de la lista

Al principio de `lista_almacen.py`:

- `MINIMO_KG` — por debajo de esto un granel no se publica (resto de balanza).
- `UMBRAL_POCO_KG` / `UMBRAL_POCO_UNIDAD` — cuándo aparece "queda poco".
- `TIENDA_API` — solo si algún día hay más de una tienda en la cuenta.

## Sin GitHub

El script también funciona con un export CSV de Loyverse, sin token:

    python3 lista_almacen.py generar --csv export_items.csv

Y para verificar la conexión con la API sin escribir archivos:

    export LOYVERSE_TOKEN="..."
    python3 lista_almacen.py explorar
