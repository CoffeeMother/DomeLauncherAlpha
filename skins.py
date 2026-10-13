"""
Biblioteca de skins (como la del launcher oficial): guardas tus skins con nombre y las aplicas con un clic.

- Cada skin se guarda como PNG con nombre propio (hash del contenido) en <datos>/skins/ y el índice en skins.json.
- Solo se aceptan PNG de 64x64 o 64x32 y hasta 24 KB (el mismo límite de Mojang).
- Nada de lo que llega desde la interfaz puede apuntar fuera de esa carpeta: se trabaja con ids, no con rutas.
- Aplicar una skin la sube a tu cuenta con cuenta.cambiar_skin (solo cuentas premium).
"""
import os, re, json, base64, hashlib, threading
import nucleo as nu
import cuenta as cu

CARPETA = os.path.join(nu.BASE, "skins")
RUTA = os.path.join(CARPETA, "skins.json")
MAX_SKINS = 60
_ID = re.compile(r"[0-9a-f]{12}")
_lock = threading.Lock()
_uri = {}                                    # archivo -> data URI


class ErrorSkins(Exception):
    pass


# ------------------------------------------------------------------ índice
def _leer():
    d = nu.leer(RUTA, {})
    if not isinstance(d, dict): d = {}
    lista = []
    for s in d.get("skins", []) if isinstance(d.get("skins"), list) else []:
        if (isinstance(s, dict) and isinstance(s.get("id"), str) and _ID.fullmatch(s["id"]) and isinstance(s.get("archivo"), str)
                and re.fullmatch(r"[0-9a-f]{16}\.png", s["archivo"]) and os.path.isfile(os.path.join(CARPETA, s["archivo"]))):
            lista.append({"id": s["id"], "nombre": _nombre(s.get("nombre")), "variante": "slim" if s.get("variante") == "slim" else "classic",
                          "archivo": s["archivo"]})
    activa = d.get("activa") if isinstance(d.get("activa"), str) and any(s["id"] == d.get("activa") for s in lista) else ""
    return {"skins": lista, "activa": activa, "importada": bool(d.get("importada"))}


def _escribir(d):
    os.makedirs(CARPETA, exist_ok=True)
    tmp = RUTA + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f: json.dump(d, f, ensure_ascii=False, indent=2)
    os.replace(tmp, RUTA)


def _nombre(n, defecto="Skin"):
    n = " ".join(str(n or "").split())[:24]
    return n or defecto


def _data_uri(archivo):
    if archivo not in _uri:
        with open(os.path.join(CARPETA, archivo), "rb") as f: _uri[archivo] = "data:image/png;base64," + base64.b64encode(f.read()).decode("ascii")
    return _uri[archivo]


def _publico(d):
    return {"skins": [{"id": s["id"], "nombre": s["nombre"], "variante": s["variante"], "uri": _data_uri(s["archivo"])} for s in d["skins"]],
            "activa": d["activa"]}


# ------------------------------------------------------------------ guardar
def _guardar_png(datos):
    """Valida el PNG y lo guarda con nombre propio. Devuelve el nombre del archivo."""
    try: cu.validar_skin(datos)
    except cu.ErrorCuenta as e: raise ErrorSkins(str(e))
    nombre = hashlib.sha256(datos).hexdigest()[:16] + ".png"
    os.makedirs(CARPETA, exist_ok=True)
    destino = os.path.join(CARPETA, nombre)
    if not os.path.exists(destino):
        with open(destino + ".tmp", "wb") as f: f.write(datos)
        os.replace(destino + ".tmp", destino)
    return nombre


def leer_png(ruta):
    """Lee un archivo del disco y devuelve (bytes, data URI) tras validar que es una skin."""
    try:
        with open(ruta, "rb") as f: datos = f.read(cu.MAX_SKIN + 1)
    except OSError:
        raise ErrorSkins("No pude leer ese archivo.")
    try: cu.validar_skin(datos)
    except cu.ErrorCuenta as e: raise ErrorSkins(str(e))
    return datos, "data:image/png;base64," + base64.b64encode(datos).decode("ascii")


def listar():
    with _lock: return _publico(_leer())


def agregar_datos(datos, nombre, variante, activar=False, renombrar=True):
    """Añade una skin (bytes de un PNG) a la biblioteca. Devuelve su id. Si ya estaba, solo actualiza nombre y modelo."""
    if variante not in ("classic", "slim"): raise ErrorSkins("Modelo de skin no válido.")
    with _lock:
        archivo = _guardar_png(datos)
        d = _leer()
        previa = next((s for s in d["skins"] if s["archivo"] == archivo and s["variante"] == variante), None)
        if previa:
            if renombrar: previa["nombre"] = _nombre(nombre, previa["nombre"])
            sid = previa["id"]
        else:
            if len(d["skins"]) >= MAX_SKINS: raise ErrorSkins(f"La biblioteca está llena ({MAX_SKINS} skins). Borra alguna.")
            sid = hashlib.sha256((archivo + variante).encode()).hexdigest()[:12]
            d["skins"].append({"id": sid, "nombre": _nombre(nombre), "variante": variante, "archivo": archivo})
        if activar: d["activa"] = sid
        _escribir(d)
        return sid


def importar_actual(perfil):
    """La primera vez, guarda en la biblioteca la skin que ya tienes puesta (para no perderla al probar otras)."""
    with _lock:
        d = _leer()
        if d["importada"]: return
    try:
        uri = perfil.get("skin") or ""
        if uri.startswith("data:image/png;base64,"):
            sid = agregar_datos(base64.b64decode(uri.split(",", 1)[1]), perfil.get("nombre") or "Mi skin", perfil.get("variante") or "classic", activar=True, renombrar=False)
        else:
            sid = None
    except (ErrorSkins, ValueError):
        sid = None
    with _lock:
        d = _leer(); d["importada"] = True
        if sid and not d["activa"]: d["activa"] = sid
        _escribir(d)


def _buscar(d, sid):
    s = next((x for x in d["skins"] if x["id"] == sid), None) if isinstance(sid, str) and _ID.fullmatch(sid) else None
    if not s: raise ErrorSkins("Esa skin ya no está en la biblioteca.")
    return s


def renombrar(sid, nombre):
    with _lock:
        d = _leer(); s = _buscar(d, sid)
        s["nombre"] = _nombre(nombre, s["nombre"]); _escribir(d)
        return _publico(d)


def borrar(sid):
    with _lock:
        d = _leer(); s = _buscar(d, sid)
        d["skins"] = [x for x in d["skins"] if x["id"] != sid]
        if d["activa"] == sid: d["activa"] = ""
        _escribir(d)
        if not any(x["archivo"] == s["archivo"] for x in d["skins"]):
            try: os.remove(os.path.join(CARPETA, s["archivo"])); _uri.pop(s["archivo"], None)
            except OSError: pass
        return _publico(d)


def aplicar(sid):
    """Sube la skin de la biblioteca a la cuenta. Devuelve el perfil nuevo (cuenta.perfil_cosmeticos)."""
    with _lock:
        d = _leer(); s = _buscar(d, sid)
        ruta = os.path.join(CARPETA, s["archivo"])
    perfil = cu.cambiar_skin(ruta, s["variante"])       # lanza ErrorCuenta con un mensaje claro si Mojang la rechaza
    with _lock:
        d = _leer()
        if any(x["id"] == sid for x in d["skins"]): d["activa"] = sid; _escribir(d)
    return perfil
