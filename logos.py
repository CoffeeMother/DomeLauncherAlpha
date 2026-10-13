"""
Logos de modpacks: al instalar un modpack (Modrinth o CurseForge) se baja su logo y se usa como icono de la instancia.

- Solo se descarga por https desde cdn.modrinth.com y *.forgecdn.net, hasta 1 MB, y se comprueba el contenido
  (PNG, JPG, WEBP o GIF) antes de guardarlo con un nombre propio (hash) en <datos>/logos/.
- Si algo falla (sin internet, el proyecto no tiene logo...) la instalación sigue y se usa el icono normal.
"""
import os, re, base64, hashlib, urllib.parse
import requests
import nucleo as nu
import modrinth as mr
import curseforge as cf
from tema import _tipo_imagen

CARPETA = os.path.join(nu.BASE, "logos")
MAX_BYTES = 1024 * 1024
_NOMBRE = re.compile(r"[0-9a-f]{16}\.(png|jpg|gif|webp)")
_PID = re.compile(r"[A-Za-z0-9]{1,20}")
_MIME = {"png": "image/png", "jpg": "image/jpeg", "gif": "image/gif", "webp": "image/webp"}
_cache = {}                                  # nombre -> data URI


def _host_ok(u):
    p = urllib.parse.urlparse(str(u or ""))
    h = str(p.hostname or "")
    return p.scheme == "https" and (h == "cdn.modrinth.com" or h.endswith(".forgecdn.net"))


def valido(nombre):
    return isinstance(nombre, str) and bool(_NOMBRE.fullmatch(nombre)) and os.path.isfile(os.path.join(CARPETA, nombre))


def _url_logo(fuente, proyecto):
    if fuente == "modrinth":
        if not _PID.fullmatch(str(proyecto)): return ""
        return str(mr._get(f"/project/{proyecto}").get("icon_url") or "")
    if fuente == "curseforge":
        logo = cf._info_mod(cf._id(proyecto)).get("logo") or {}
        return str(logo.get("thumbnailUrl") or logo.get("url") or "")
    return ""


def _bajar(url):
    if not _host_ok(url): return None
    try:
        r = requests.get(url, stream=True, timeout=15, allow_redirects=False, headers={"User-Agent": "DomeLauncher/2.0"})
        if r.status_code != 200: return None
        datos = b""
        for trozo in r.iter_content(65536):
            datos += trozo
            if len(datos) > MAX_BYTES: return None
    except requests.RequestException:
        return None
    return datos


def de_proyecto(fuente, proyecto):
    """Descarga y guarda el logo del proyecto. Devuelve el nombre del archivo, o '' si no tiene/no se pudo."""
    try:
        url = _url_logo(fuente, proyecto)
    except Exception:
        return ""
    datos = _bajar(url) if url else None
    ext = _tipo_imagen(datos[:16]) if datos else None
    if not ext: return ""
    nombre = f"{hashlib.sha256(datos).hexdigest()[:16]}.{ext}"
    os.makedirs(CARPETA, exist_ok=True)
    destino = os.path.join(CARPETA, nombre)
    if not os.path.exists(destino):
        with open(destino + ".tmp", "wb") as f: f.write(datos)
        os.replace(destino + ".tmp", destino)
    return nombre


def uri(nombre):
    """Logo como data URI para la interfaz ('' si no hay)."""
    if not valido(nombre): return ""
    if nombre not in _cache:
        with open(os.path.join(CARPETA, nombre), "rb") as f: datos = f.read(MAX_BYTES + 1)
        _cache[nombre] = f"data:{_MIME[nombre.rsplit('.', 1)[1]]};base64," + base64.b64encode(datos).decode("ascii")
    return _cache[nombre]


def limpiar(inst):
    """Borra los logos que ya no usa ninguna instancia."""
    usados = {d.get("logo") for d in inst.values()}
    try:
        for n in os.listdir(CARPETA):
            if (_NOMBRE.fullmatch(n) and n not in usados) or n.endswith(".tmp"):
                try: os.remove(os.path.join(CARPETA, n)); _cache.pop(n, None)
                except OSError: pass
    except OSError:
        pass
