
import os, re, shutil
from tema import _tipo_imagen

CARPETA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web", "iconos")
MAX_BYTES = 2 * 1024 * 1024
DEFECTO = "tierra.png"
_NOMBRE = re.compile(r"[\w\- ().]{1,60}\.(png|jpg|gif|webp)", re.I | re.UNICODE)


class ErrorIcono(Exception):
    pass


def valido(nombre):
    """True si 'nombre' es un archivo de icono que existe en la carpeta."""
    return (isinstance(nombre, str) and os.path.basename(nombre) == nombre and not nombre.startswith(".")
            and bool(_NOMBRE.fullmatch(nombre)) and os.path.isfile(os.path.join(CARPETA, nombre)))


def listar():
    try:
        return sorted((n for n in os.listdir(CARPETA) if valido(n)), key=lambda n: (n != DEFECTO, n.lower()))
    except OSError:
        return []


def importar(ruta):
    """Copia una imagen a la carpeta de iconos tras comprobar su contenido. Devuelve el nombre guardado."""
    try:
        if not os.path.isfile(ruta): raise ErrorIcono("No encontré ese archivo.")
        if os.path.getsize(ruta) > MAX_BYTES: raise ErrorIcono("La imagen pesa más de 2 MB; usa una más pequeña.")
        with open(ruta, "rb") as f: datos = f.read(MAX_BYTES + 1)
    except OSError:
        raise ErrorIcono("No pude leer ese archivo.")
    ext = _tipo_imagen(datos[:16])
    if not ext: raise ErrorIcono("Solo se admiten imágenes PNG, JPG, WEBP o GIF.")
    base = re.sub(r"[^\w\- ]", "", os.path.splitext(os.path.basename(ruta))[0], flags=re.UNICODE).strip()[:40] or "icono"
    nombre, n = f"{base}.{ext}", 2
    while os.path.exists(os.path.join(CARPETA, nombre)):
        with open(os.path.join(CARPETA, nombre), "rb") as f:
            if f.read(MAX_BYTES + 1) == datos: return nombre          # ya estaba, mismo contenido
        nombre = f"{base} {n}.{ext}"; n += 1
    os.makedirs(CARPETA, exist_ok=True)
    with open(os.path.join(CARPETA, nombre + ".tmp"), "wb") as f: f.write(datos)
    os.replace(os.path.join(CARPETA, nombre + ".tmp"), os.path.join(CARPETA, nombre))
    return nombre
