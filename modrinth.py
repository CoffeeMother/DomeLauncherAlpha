"""
Cliente de Modrinth para Dome Launcher (API v2, gratuita y sin clave).

- Busca mods compatibles con el loader y la versión de Minecraft de la instancia.
- Instala el mod y sus dependencias obligatorias, verificando el hash SHA-512 de cada archivo.
- Detecta actualizaciones de los mods ya instalados (por hash, sirve incluso para mods que
  pusiste a mano) y los actualiza.

Seguridad: solo se descarga por https desde los servidores de archivos de Modrinth, el nombre del
archivo se limpia y, si el hash no coincide, el archivo se descarta.
"""
import os, json, hashlib, urllib.parse
import requests
import nucleo as nu

API = "https://api.modrinth.com/v2"
HOSTS_DESCARGA = ("cdn.modrinth.com", "cdn-raw.modrinth.com")
MAX_BYTES = 300 * 1024 * 1024
MANIFIESTO = "dome_mods.json"          # en la carpeta de la instancia: qué mod vino de qué proyecto
_s = requests.Session()
_s.headers["User-Agent"] = "DomeLauncher/2.0 (launcher personal de Minecraft)"
_hash_cache = {}                        # (ruta, tamaño, fecha) -> sha512


class ErrorModrinth(Exception):
    pass


# --------------------------------------------------------------------- red
def _revisar(r):
    if r.status_code == 429: raise ErrorModrinth("Modrinth pide esperar un momento (demasiadas consultas).")
    if r.status_code >= 400: raise ErrorModrinth(f"Modrinth respondió con error {r.status_code}.")
    return r.json()


def _get(ruta, params=None):
    try: return _revisar(_s.get(API + ruta, params=params, timeout=15))
    except requests.RequestException: raise ErrorModrinth("No pude conectar con Modrinth (¿sin internet?).")


def _post(ruta, cuerpo):
    try: return _revisar(_s.post(API + ruta, json=cuerpo, timeout=20))
    except requests.RequestException: raise ErrorModrinth("No pude conectar con Modrinth (¿sin internet?).")


def _stream(url):
    cab = {}
    if str(urllib.parse.urlparse(url).hostname or "").endswith(".forgecdn.net"):
        # CurseForge exige su clave también para descargar desde su CDN (solo se envía a ese host)
        try:
            import curseforge as _cf
            cab["x-api-key"] = _cf.clave()
        except Exception:
            pass
    try:
        r = _s.get(url, stream=True, timeout=30, headers=cab)
        r.raise_for_status()
        yield from r.iter_content(65536)
    except requests.RequestException:
        raise ErrorModrinth("Se cortó la descarga (¿sin internet?).")


# ------------------------------------------------------------- instancia
def _contexto(d):
    loader = nu.loader_de(d)
    if not loader: raise ErrorModrinth("Las instancias Vanilla no usan mods.")
    mc = d.get("version_target") or d.get("version_id")
    return (["quilt", "fabric"] if loader == "quilt" else [loader]), mc


def _carpeta_mods(d):
    return os.path.join(nu.preparar_carpeta(d), "mods")


def _ruta_manifiesto(d):
    return os.path.join(nu.carpeta_juego(d), MANIFIESTO)


def _leer_manifiesto(d):
    """{archivo: {project_id, version_id, version, titulo}} sin entradas de archivos que ya no existen."""
    datos = nu.leer(_ruta_manifiesto(d), {})
    mods = _carpeta_mods(d)
    return {a: m for a, m in datos.items()
            if os.path.exists(os.path.join(mods, a)) or os.path.exists(os.path.join(mods, a + ".disabled"))}


def _guardar_manifiesto(d, datos):
    with open(_ruta_manifiesto(d), "w", encoding="utf-8") as f:
        json.dump(datos, f, ensure_ascii=False, indent=2)


def _sha512(ruta):
    st = os.stat(ruta)
    clave = (ruta, st.st_size, st.st_mtime)
    if clave not in _hash_cache:
        h = hashlib.sha512()
        with open(ruta, "rb") as f:
            for bloque in iter(lambda: f.read(1 << 20), b""): h.update(bloque)
        _hash_cache[clave] = h.hexdigest()
    return _hash_cache[clave]


def _hashes_instalados(d):
    """{sha512: archivo} de todos los .jar de la instancia (activos y desactivados)."""
    mods, res = _carpeta_mods(d), {}
    for f in os.listdir(mods):
        if f.lower().endswith((".jar", ".jar.disabled")): res[_sha512(os.path.join(mods, f))] = f
    return res


def _proyectos_instalados(d):
    """IDs de proyecto ya instalados: los del manifiesto + los que Modrinth reconoce por hash."""
    ids = {m["project_id"] for m in _leer_manifiesto(d).values()}
    try:
        hs = list(_hashes_instalados(d))
        if hs:
            ids |= {v["project_id"] for v in _post("/version_files", {"hashes": hs, "algorithm": "sha512"}).values()}
    except ErrorModrinth:
        pass  # sin red: nos quedamos con lo que dice el manifiesto
    return ids


# --------------------------------------------------------------- buscar
def buscar(d, texto, pagina=0, orden="relevance"):
    loaders, mc = _contexto(d)
    facets = [["project_type:mod"], [f"categories:{l}" for l in loaders], [f"versions:{mc}"]]
    if orden not in ("relevance", "downloads", "follows", "newest", "updated"): orden = "relevance"
    r = _get("/search", {"query": texto or "", "facets": json.dumps(facets), "limit": 20,
                         "offset": max(0, int(pagina)) * 20, "index": orden})
    ya = _proyectos_instalados(d)
    return {"total": r.get("total_hits", 0), "pagina": int(pagina), "resultados": [
        {"id": h["project_id"], "slug": h.get("slug", ""), "titulo": h.get("title", ""), "autor": h.get("author", ""),
         "descripcion": h.get("description", ""), "icono": h.get("icon_url") or "", "descargas": h.get("downloads", 0),
         "instalado": h["project_id"] in ya} for h in r.get("hits", [])]}


# -------------------------------------------------------------- instalar
def _mejor_version(pid, loaders, mc):
    vs = _get(f"/project/{pid}/version", {"loaders": json.dumps(loaders), "game_versions": json.dumps([mc])})
    for tipo in ("release", "beta", "alpha"):  # estable primero
        for v in vs:
            if v.get("version_type") == tipo: return v
    return None


def _archivo_primario(v):
    jars = [f for f in v.get("files", []) if f.get("filename", "").lower().endswith(".jar")]
    if not jars: raise ErrorModrinth("Esa versión no trae un archivo .jar.")
    return next((f for f in jars if f.get("primary")), jars[0])


def _descargar(f, destino, extensiones=(".jar",)):
    """Descarga y verifica un archivo de Modrinth. Devuelve el nombre del archivo guardado."""
    url = urllib.parse.urlparse(f.get("url", ""))
    if url.scheme != "https" or url.hostname not in HOSTS_DESCARGA:
        raise ErrorModrinth("Descarga bloqueada: el archivo no viene de los servidores de Modrinth.")
    nombre = os.path.basename(f["filename"].replace("\\", "/"))
    if not nombre.lower().endswith(tuple(extensiones)) or nombre.startswith("."): raise ErrorModrinth("Nombre de archivo no válido.")
    esperado = (f.get("hashes") or {}).get("sha512", "").lower()
    if not esperado: raise ErrorModrinth("El archivo no trae hash para verificarlo; no se instala.")
    final, parcial = os.path.join(destino, nombre), os.path.join(destino, nombre + ".part")
    h, total = hashlib.sha512(), 0
    try:
        with open(parcial, "wb") as out:
            for trozo in _stream(f["url"]):
                total += len(trozo)
                if total > MAX_BYTES: raise ErrorModrinth("El archivo es demasiado grande.")
                h.update(trozo); out.write(trozo)
        if h.hexdigest() != esperado: raise ErrorModrinth("La descarga está corrupta (el hash no coincide); se descartó.")
        os.replace(parcial, final)
    finally:
        if os.path.exists(parcial): os.remove(parcial)
    return nombre


def _titulo(pid, defecto=""):
    try: return _get(f"/project/{pid}").get("title", defecto) or defecto
    except ErrorModrinth: return defecto


def _instalar_proyecto(d, pid, titulo, ya, res, nivel=0):
    if pid in ya: return
    ya.add(pid)
    loaders, mc = _contexto(d)
    v = _mejor_version(pid, loaders, mc)
    if v is None:
        if nivel == 0: raise ErrorModrinth(f"No hay una versión de ese mod para {loaders[0]} {mc}.")
        res["sin_version"].append(_titulo(pid, pid)); return
    if nivel < 4:   # primero las dependencias obligatorias
        for dep in v.get("dependencies", []):
            if dep.get("dependency_type") != "required": continue
            pid2 = dep.get("project_id") or _get(f"/version/{dep['version_id']}")["project_id"]
            _instalar_proyecto(d, pid2, "", ya, res, nivel + 1)
    destino = _carpeta_mods(d)
    f = _archivo_primario(v)
    if os.path.exists(os.path.join(destino, f["filename"])):
        res["omitidos"].append(f["filename"]); return
    archivo = _descargar(f, destino)
    titulo = titulo or _titulo(pid, archivo)
    man = _leer_manifiesto(d)
    man[archivo] = {"project_id": pid, "version_id": v["id"], "version": v.get("version_number", ""), "titulo": titulo}
    _guardar_manifiesto(d, man)
    (res["instalados"] if nivel == 0 else res["dependencias"]).append(titulo)


def instalar(d, project_id, titulo=""):
    res = {"instalados": [], "dependencias": [], "omitidos": [], "sin_version": []}
    ya = _proyectos_instalados(d)
    if project_id in ya:
        res["omitidos"].append(titulo or "Ese mod"); return res
    _instalar_proyecto(d, project_id, titulo, ya, res)
    return res


# ---------------------------------------------------------- actualizaciones
def buscar_actualizaciones(d):
    """Lista de mods con una versión estable más nueva: [{archivo, actual, nueva, version_id, project_id}]."""
    loaders, mc = _contexto(d)
    hs = _hashes_instalados(d)
    if not hs: return []
    base = {"hashes": list(hs), "algorithm": "sha512"}
    actuales = _post("/version_files", base)
    nuevas = _post("/version_files/update", {**base, "loaders": loaders, "game_versions": [mc]})
    res = []
    for h, nueva in nuevas.items():
        if nueva.get("version_type") != "release": continue
        try: f = _archivo_primario(nueva)
        except ErrorModrinth: continue
        if (f.get("hashes") or {}).get("sha512", "").lower() in ("", h): continue   # ya tienes esa versión
        res.append({"archivo": hs[h], "actual": (actuales.get(h) or {}).get("version_number", ""),
                    "nueva": nueva.get("version_number", ""), "version_id": nueva["id"], "project_id": nueva["project_id"]})
    return res


def actualizar(d, archivo, version_id):
    """Reemplaza un mod por la versión indicada (mantiene su estado activado/desactivado)."""
    nu._ruta_mod(d, archivo)                     # valida el nombre
    loaders, mc = _contexto(d)
    v = _get(f"/version/{version_id}")
    if mc not in v.get("game_versions", []) or not set(loaders) & set(v.get("loaders", [])):
        raise ErrorModrinth("Esa versión no es compatible con esta instancia.")
    res = {"instalados": [], "dependencias": [], "omitidos": [], "sin_version": []}
    ya = _proyectos_instalados(d) | {v["project_id"]}
    for dep in v.get("dependencies", []):         # dependencias nuevas que pida esta versión
        if dep.get("dependency_type") == "required" and dep.get("project_id"):
            _instalar_proyecto(d, dep["project_id"], "", ya, res, 1)
    destino = _carpeta_mods(d)
    nuevo = _descargar(_archivo_primario(v), destino)
    viejo = os.path.join(destino, archivo)
    estaba_off = archivo.lower().endswith(".disabled")
    if os.path.basename(viejo) != nuevo and os.path.exists(viejo): os.remove(viejo)
    if estaba_off: os.replace(os.path.join(destino, nuevo), os.path.join(destino, nuevo + ".disabled"))
    man = _leer_manifiesto(d)
    man.pop(archivo, None)
    man[nuevo + (".disabled" if estaba_off else "")] = {
        "project_id": v["project_id"], "version_id": v["id"], "version": v.get("version_number", ""),
        "titulo": _titulo(v["project_id"], nuevo)}
    _guardar_manifiesto(d, man)
    res["instalados"].append(nuevo)
    return res