"""
Tema del launcher: modo (claro / oscuro / OLED / auto), colores, fondo (imagen o GIF) y ajustes.

Todo se guarda en launcher_config.json bajo la clave "tema" (el resto de la configuración no se toca).
Los fondos elegidos se copian a <datos>/fondos/ con un nombre propio (hash del contenido), así que
seguir funcionando aunque muevas o borres el archivo original, y nada de lo que llega desde la
interfaz puede apuntar fuera de esa carpeta.
"""
import os, re, json, base64, hashlib, threading
import nucleo as nu

CARPETA_FONDOS = os.path.join(nu.BASE, "fondos")
MAX_BYTES = 25 * 1024 * 1024
MODOS = ("auto", "claro", "oscuro", "oled")
TIPOS_FONDO = ("default", "none", "custom")
CLAVES_CONFIG = {"animaciones": bool, "curseforge_key": str, "azure_client_id": str}   # ajustes simples que la interfaz puede guardar
_GUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_CLAVE_CF = re.compile(r"[A-Za-z0-9$./_\-]{20,200}")
_NOMBRE = re.compile(r"fondo_[0-9a-f]{16}\.(png|jpg|gif|webp)")
_HEX = re.compile(r"#[0-9a-fA-F]{6}")
_MIME = {"png": "image/png", "jpg": "image/jpeg", "gif": "image/gif", "webp": "image/webp"}
_lock = threading.Lock()


class ErrorTema(Exception):
    pass


def base():
    return {"modo": "oscuro", "acento": "#8b5cf6", "acento2": "",
            "fondo": {"tipo": "default", "archivo": ""}, "velo": 100, "desenfoque": 0}


def _entero(v, lo, hi, defecto):
    try: v = int(round(float(v)))
    except (TypeError, ValueError): return defecto
    return max(lo, min(hi, v))


def sanitizar(t):
    """Devuelve un tema válido a partir de cualquier cosa (valores raros -> los de por defecto)."""
    r, t = base(), (t if isinstance(t, dict) else {})
    if t.get("modo") in MODOS: r["modo"] = t["modo"]
    if isinstance(t.get("acento"), str) and _HEX.fullmatch(t["acento"]): r["acento"] = t["acento"].lower()
    if isinstance(t.get("acento2"), str) and _HEX.fullmatch(t["acento2"]): r["acento2"] = t["acento2"].lower()
    r["velo"] = _entero(t.get("velo"), 0, 100, 100)
    r["desenfoque"] = _entero(t.get("desenfoque"), 0, 20, 0)
    f = t.get("fondo") if isinstance(t.get("fondo"), dict) else {}
    archivo = f.get("archivo") if isinstance(f.get("archivo"), str) else ""
    if not (_NOMBRE.fullmatch(archivo) and os.path.isfile(os.path.join(CARPETA_FONDOS, archivo))): archivo = ""
    tipo = f.get("tipo") if f.get("tipo") in TIPOS_FONDO else "default"
    if tipo == "custom" and not archivo: tipo = "default"
    r["fondo"] = {"tipo": tipo, "archivo": archivo}
    return r


# ----------------------------------------------------------- configuración
def _escribir_cfg(cfg):
    os.makedirs(os.path.dirname(nu.RUTA_CFG), exist_ok=True)
    tmp = nu.RUTA_CFG + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f: json.dump(cfg, f, ensure_ascii=False, indent=4)
    os.replace(tmp, nu.RUTA_CFG)


def _cfg():
    c = nu.leer(nu.RUTA_CFG, {})
    return c if isinstance(c, dict) else {}


def leer_tema():
    return sanitizar(_cfg().get("tema"))


def guardar_tema(t):
    with _lock:
        cfg, limpio = _cfg(), sanitizar(t)
        cfg["tema"] = limpio
        _escribir_cfg(cfg)
        _limpiar(limpio["fondo"]["archivo"])
    return limpio


def guardar_clave(clave, valor):
    if clave not in CLAVES_CONFIG: raise ErrorTema("Ajuste desconocido.")
    if not isinstance(valor, CLAVES_CONFIG[clave]): raise ErrorTema("Valor no válido.")
    if clave == "curseforge_key":
        valor = valor.strip()
        if valor and not _CLAVE_CF.fullmatch(valor): raise ErrorTema("La clave de CurseForge no tiene un formato válido (copia solo la clave).")
    if clave == "azure_client_id":
        valor = valor.strip()
        if valor and not _GUID.fullmatch(valor): raise ErrorTema("El Client ID debe verse así: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx.")
    with _lock:
        cfg = _cfg()
        if clave in ("curseforge_key", "azure_client_id") and not valor: cfg.pop(clave, None)     # vacío = quitar la clave
        else: cfg[clave] = valor
        _escribir_cfg(cfg)


def leer_clave(clave):
    """Valor de un ajuste simple (para uso del backend; no se envía a la interfaz)."""
    return _cfg().get(clave)


def color_ventana():
    """Color de la ventana al arrancar (evita un destello oscuro si usas el modo claro)."""
    return {"claro": "#f3f1f8", "oled": "#000000"}.get(leer_tema()["modo"], "#0b0a10")


# ------------------------------------------------------------------ fondos
def _tipo_imagen(cab):
    if cab.startswith(b"\x89PNG\r\n\x1a\n"): return "png"
    if cab.startswith(b"\xff\xd8\xff"): return "jpg"
    if cab[:6] in (b"GIF87a", b"GIF89a"): return "gif"
    if cab[:4] == b"RIFF" and cab[8:12] == b"WEBP": return "webp"
    return None


def importar_fondo(ruta):
    """Copia una imagen/GIF a la carpeta de fondos tras validarla. Devuelve el nombre guardado."""
    try:
        if not os.path.isfile(ruta): raise ErrorTema("No encontré ese archivo.")
        if os.path.getsize(ruta) > MAX_BYTES: raise ErrorTema("La imagen pesa más de 25 MB.")
        with open(ruta, "rb") as f: datos = f.read(MAX_BYTES + 1)
    except OSError:
        raise ErrorTema("No pude leer ese archivo.")
    if len(datos) > MAX_BYTES: raise ErrorTema("La imagen pesa más de 25 MB.")
    ext = _tipo_imagen(datos[:16])      # se mira el contenido, no la extensión
    if not ext: raise ErrorTema("Solo se admiten imágenes PNG, JPG, WEBP o GIF.")
    nombre = f"fondo_{hashlib.sha256(datos).hexdigest()[:16]}.{ext}"
    os.makedirs(CARPETA_FONDOS, exist_ok=True)
    destino = os.path.join(CARPETA_FONDOS, nombre)
    if not os.path.exists(destino):
        with open(destino + ".tmp", "wb") as f: f.write(datos)
        os.replace(destino + ".tmp", destino)
    return nombre


def elegir_fondo(ruta):
    """Importa el archivo y lo deja como fondo activo. Devuelve el tema guardado."""
    nombre = importar_fondo(ruta)
    t = leer_tema(); t["fondo"] = {"tipo": "custom", "archivo": nombre}
    # sanitizar() comprueba que exista, y ya está copiado
    return guardar_tema(t)


def _limpiar(usado):
    """Borra los fondos antiguos que ya no usa el tema."""
    try:
        for n in os.listdir(CARPETA_FONDOS):
            if n != usado and (_NOMBRE.fullmatch(n) or n.endswith(".tmp")):
                try: os.remove(os.path.join(CARPETA_FONDOS, n))
                except OSError: pass
    except OSError:
        pass


def datos_fondo(archivo):
    """{mime, b64} del fondo guardado o None."""
    if not (isinstance(archivo, str) and _NOMBRE.fullmatch(archivo)): return None
    try:
        with open(os.path.join(CARPETA_FONDOS, archivo), "rb") as f: datos = f.read()
    except OSError:
        return None
    return {"mime": _MIME[archivo.rsplit(".", 1)[1]], "b64": base64.b64encode(datos).decode("ascii")}
