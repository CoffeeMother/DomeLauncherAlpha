"""
Cliente de CurseForge para Dome Launcher (solo mods y modpacks).

- Busca mods y modpacks, y instala mods con sus dependencias obligatorias.
- Lee modpacks de CurseForge (.zip con manifest.json) para que modpack.py los instale y actualice.

REQUISITO: la API de CurseForge necesita una clave personal y gratuita (console.curseforge.com).
Se pega en Ajustes y se guarda en launcher_config.json (o en la variable CURSEFORGE_API_KEY).

Respeto a los autores: algunos mods/modpacks desactivan las descargas desde aplicaciones de
terceros; la API no entrega su URL. En ese caso NO se intenta saltarse el bloqueo: se avisa con el
enlace de su página para descargarlos a mano.

Seguridad: solo se descarga por https desde *.forgecdn.net, se verifica el SHA-1 que da la API,
los nombres de archivo se limpian y todos los parámetros que llegan de la interfaz se validan.
"""
import os, re, json, hashlib, zipfile, urllib.parse
import requests
import nucleo as nu
import modrinth as mr
import tema as tm

API = "https://api.curseforge.com/v1"
GAME_ID = 432
CLASE_MOD, CLASE_MODPACK, CLASE_RESOURCEPACK, CLASE_SHADER, CLASE_MUNDO = 6, 4471, 12, 6552, 17
LOADER_CF = {"forge": 1, "fabric": 4, "quilt": 5, "neoforge": 6}
ORDEN_CF = {"relevance": 1, "follows": 2, "updated": 3, "downloads": 6, "newest": 11}
TIPOS = {"modpack": CLASE_MODPACK, "mod": CLASE_MOD}
MAX_BYTES = 400 * 1024 * 1024
MAX_BYTES_ZIP = 2 * 1024 ** 3
_ID = re.compile(r"\d{1,10}")
_MANIFEST_LOADER = {"forge": "Forge", "neoforge": "NeoForge", "fabric": "Fabric", "quilt": "Quilt"}
_s = requests.Session()
_s.headers["User-Agent"] = "DomeLauncher/2.0 (launcher personal de Minecraft)"
_s.headers["Accept"] = "application/json"


class ErrorCurseForge(mr.ErrorModrinth):
    """Hereda de ErrorModrinth para que la interfaz muestre su mensaje igual que los demás."""


# ------------------------------------------------------------------ red
# Clave de API integrada: así el launcher funciona sin configurar nada. Es TU clave personal: si compartes el
# launcher con otras personas, borra este valor (cada una debe usar la suya) y créala de nuevo en console.curseforge.com.
CLAVE_INTEGRADA = "cfc_pat_7452396b6e778119c919923c097c19e4084fe33cf75cf14978cace862e8ab9a4"


def clave():
    """Orden: la guardada en Ajustes, la variable CURSEFORGE_API_KEY y, si no hay, la integrada."""
    k = str(tm.leer_clave("curseforge_key") or os.environ.get("CURSEFORGE_API_KEY", "") or CLAVE_INTEGRADA).strip()
    if not k: raise ErrorCurseForge("Falta la clave de API de CurseForge. Consíguela gratis en console.curseforge.com y pégala en Ajustes → CurseForge.")
    return k


def hay_clave():
    try: clave(); return True
    except ErrorCurseForge: return False


def _api(metodo, ruta, bloqueo_ok=False, **kw):
    """Llama a la API. Con bloqueo_ok=True un 403 devuelve None (descarga no permitida por el autor)."""
    try:
        r = _s.request(metodo, API + ruta, timeout=20, headers={"x-api-key": clave()}, **kw)
    except requests.RequestException:
        raise ErrorCurseForge("No pude conectar con CurseForge (¿sin internet?).")
    if r.status_code == 403 and bloqueo_ok: return None
    if r.status_code in (401, 403): raise ErrorCurseForge("CurseForge rechazó la clave de API. Revísala en Ajustes → CurseForge.")
    if r.status_code == 404: raise ErrorCurseForge("CurseForge no encontró eso (puede que se haya eliminado).")
    if r.status_code == 429: raise ErrorCurseForge("CurseForge pide esperar un momento (demasiadas consultas).")
    if r.status_code >= 400: raise ErrorCurseForge(f"CurseForge respondió con error {r.status_code}.")
    try: return r.json()
    except ValueError: raise ErrorCurseForge("Respuesta no válida de CurseForge.")


def _id(valor):
    if not _ID.fullmatch(str(valor)): raise ErrorCurseForge("Identificador no válido.")
    return int(valor)


def _web_ok(u):
    p = urllib.parse.urlparse(str(u or ""))
    return p.scheme == "https" and p.hostname in ("www.curseforge.com", "curseforge.com")


def url_web_valida(u):
    return _web_ok(u)


def _host_cdn_ok(u):
    p = urllib.parse.urlparse(str(u or ""))
    return p.scheme == "https" and str(p.hostname or "").endswith(".forgecdn.net")


# ------------------------------------------------------------- buscar
def buscar(tipo, texto, pagina=0, orden="downloads", version="", loaders=None, d=None):
    """Busca mods o modpacks. CurseForge filtra por UN loader y solo si se indica la versión del juego."""
    if tipo not in TIPOS: raise ErrorCurseForge("CurseForge solo ofrece mods y modpacks en este launcher.")
    p = {"gameId": GAME_ID, "classId": TIPOS[tipo], "searchFilter": str(texto or "")[:100], "sortOrder": "desc",
         "sortField": ORDEN_CF.get(orden, 6), "pageSize": 20, "index": min(max(0, int(pagina)) * 20, 9980)}
    if version and re.fullmatch(r"[\w.\-]{1,20}", str(version)):
        p["gameVersion"] = version
        ls = [l for l in (loaders or []) if l in LOADER_CF] if tipo in ("mod", "modpack") else []
        if ls: p["modLoaderType"] = LOADER_CF[ls[0]]
    r = _api("GET", "/mods/search", params=p)
    ya = set()
    if tipo == "mod" and d:
        try: ya = {m.get("project_id") for m in mr._leer_manifiesto(d).values()}
        except Exception: pass
    res = []
    for m in r.get("data", []):
        autores = m.get("authors") or [{}]
        pantallas = m.get("screenshots") or [{}]
        res.append({"id": str(m["id"]), "titulo": str(m.get("name", "")), "autor": str((autores[0] or {}).get("name", "")),
                    "descripcion": str(m.get("summary", "")), "icono": str((m.get("logo") or {}).get("thumbnailUrl") or ""),
                    "descargas": int(m.get("downloadCount") or 0), "galeria": str((pantallas[0] or {}).get("url") or ""),
                    "color": None, "instalado": f"cf:{m['id']}" in ya, "fuente": "curseforge",
                    "descarga_bloqueada": m.get("allowModDistribution") is False})
    return {"total": int((r.get("pagination") or {}).get("totalCount", len(res))), "pagina": int(pagina), "resultados": res}


# ------------------------------------------------------------ archivos
def _tipo_release(f):
    return {1: "release", 2: "beta", 3: "alpha"}.get(f.get("releaseType"), "release")


def _archivos(pid, mc=None, loader_tipo=None, tam=50):
    p = {"pageSize": tam}
    if mc: p["gameVersion"] = mc
    if loader_tipo: p["modLoaderType"] = loader_tipo
    return [f for f in _api("GET", f"/mods/{pid}/files", params=p).get("data", []) if f.get("isAvailable", True)]


def _mejor_archivo(archivos):
    for t in (1, 2, 3):                       # estable primero
        c = [f for f in archivos if f.get("releaseType") == t]
        if c: return max(c, key=lambda f: str(f.get("fileDate", "")))
    return None


def _sha1(f):
    return next((str(h.get("value", "")).lower() for h in f.get("hashes", []) if h.get("algo") == 1), "")


def _url_descarga(f):
    """URL de descarga del archivo, o None si el autor no permite descargarlo desde otras apps."""
    if _host_cdn_ok(f.get("downloadUrl")): return f["downloadUrl"]
    r = _api("GET", f"/mods/{f['modId']}/files/{f['id']}/download-url", bloqueo_ok=True)
    u = (r or {}).get("data")
    return u if _host_cdn_ok(u) else None


def _info_mod(pid):
    return _api("GET", f"/mods/{pid}").get("data") or {}


def _web_de(m, fid=None):
    w = ((m.get("links") or {}).get("websiteUrl")) or ""
    if not _web_ok(w): return ""
    return w + (f"/files/{fid}" if fid else "")


def _limpiar_nombre(n, extensiones):
    n = os.path.basename(str(n).replace("\\", "/"))
    if not n.lower().endswith(tuple(extensiones)) or n.startswith(".") or len(n) > 200: raise ErrorCurseForge("Nombre de archivo no válido.")
    return n


def _descargar(f, destino, extensiones=(".jar",)):
    """Descarga y verifica (SHA-1) un archivo de CurseForge. Devuelve el nombre guardado."""
    url = _url_descarga(f)
    if not url: raise ErrorCurseForge("DESCARGA_BLOQUEADA")
    nombre = _limpiar_nombre(f["fileName"], extensiones)
    esperado = _sha1(f)
    if not esperado: raise ErrorCurseForge("El archivo no trae hash para verificarlo; no se instala.")
    os.makedirs(destino, exist_ok=True)
    final, parcial = os.path.join(destino, nombre), os.path.join(destino, nombre + ".part")
    h, total = hashlib.sha1(), 0
    try:
        with open(parcial, "wb") as out:
            for trozo in mr._stream(url):
                total += len(trozo)
                if total > MAX_BYTES: raise ErrorCurseForge("El archivo es demasiado grande.")
                h.update(trozo); out.write(trozo)
        if h.hexdigest() != esperado: raise ErrorCurseForge("La descarga está corrupta (el hash no coincide); se descartó.")
        os.replace(parcial, final)
    finally:
        if os.path.exists(parcial): os.remove(parcial)
    return nombre


# ---------------------------------------------------------------- mods
def _instalar_mod(d, pid, titulo, ya, res, nivel, loaders, mc):
    clave_id = f"cf:{pid}"
    if clave_id in ya: return
    ya.add(clave_id)
    f = None
    for l in loaders:                         # quilt acepta también mods de fabric
        f = _mejor_archivo(_archivos(pid, mc, LOADER_CF.get(l)))
        if f: break
    if f is None:
        if nivel == 0: raise ErrorCurseForge(f"No hay una versión de ese mod para {loaders[0]} {mc} en CurseForge.")
        res["sin_version"].append(_info_mod(pid).get("name", str(pid))); return
    if nivel < 4:                             # primero las dependencias obligatorias (relationType 3)
        for dep in f.get("dependencies", []):
            if dep.get("relationType") == 3 and dep.get("modId"):
                _instalar_mod(d, int(dep["modId"]), "", ya, res, nivel + 1, loaders, mc)
    destino = os.path.join(nu.preparar_carpeta(d), "mods")
    nombre = _limpiar_nombre(f["fileName"], (".jar",))
    if os.path.exists(os.path.join(destino, nombre)) or os.path.exists(os.path.join(destino, nombre + ".disabled")):
        res["omitidos"].append(nombre); return
    titulo = titulo or _info_mod(pid).get("name", nombre)
    try:
        archivo = _descargar(f, destino)
    except ErrorCurseForge as e:
        if str(e) != "DESCARGA_BLOQUEADA": raise
        web = _web_de(_info_mod(pid), f["id"])
        msg = f"El autor de «{titulo}» no permite descargarlo desde otras aplicaciones." + (f" Descárgalo en {web} y pon el .jar en la carpeta mods." if web else "")
        if nivel == 0: raise ErrorCurseForge(msg)
        res["sin_version"].append(f"{titulo} (descarga manual)"); return
    man = mr._leer_manifiesto(d)
    man[archivo] = {"project_id": clave_id, "version_id": str(f["id"]), "version": str(f.get("displayName", ""))[:60],
                    "titulo": titulo, "fuente": "curseforge"}
    mr._guardar_manifiesto(d, man)
    (res["instalados"] if nivel == 0 else res["dependencias"]).append(titulo)


def instalar_mod(d, project_id, titulo=""):
    pid = _id(project_id)
    loaders, mc = mr._contexto(d)
    res = {"instalados": [], "dependencias": [], "omitidos": [], "sin_version": []}
    ya = {m.get("project_id") for m in mr._leer_manifiesto(d).values()}
    if f"cf:{pid}" in ya:
        res["omitidos"].append(titulo or "Ese mod"); return res
    _instalar_mod(d, pid, titulo, ya, res, 0, loaders, mc)
    return res


# ------------------------------------------------------------- modpacks
def versiones_modpack(project_id):
    """Archivos instalables del modpack, los más nuevos primero: [{id, nombre, mc, loaders, tipo}]."""
    pid = _id(project_id)
    fs = [f for f in _archivos(pid, tam=50) if str(f.get("fileName", "")).lower().endswith(".zip")]
    fs.sort(key=lambda f: str(f.get("fileDate", "")), reverse=True)
    out = []
    for f in fs:
        gv = [str(x) for x in f.get("gameVersions", [])]
        mcs = [x for x in gv if re.fullmatch(r"\d+\.\d+(?:\.\d+)?", x)]
        out.append({"id": str(f["id"]), "nombre": str(f.get("displayName") or f.get("fileName", "")), "mc": mcs[0] if mcs else "?",
                    "loaders": [x.lower() for x in gv if x in ("Forge", "Fabric", "Quilt", "NeoForge")], "tipo": _tipo_release(f)})
    return out


def descargar_modpack(project_id, file_id, destino):
    """Baja el .zip del modpack. Devuelve (ruta, nombre_de_la_versión)."""
    pid, fid = _id(project_id), _id(file_id)
    f = _api("GET", f"/mods/{pid}/files/{fid}").get("data") or {}
    if f.get("modId") != pid: raise ErrorCurseForge("Ese archivo no pertenece a ese modpack.")
    try:
        nombre = _descargar(f, destino, (".zip",))
    except ErrorCurseForge as e:
        if str(e) != "DESCARGA_BLOQUEADA": raise
        web = _web_de(_info_mod(pid), fid)
        raise ErrorCurseForge("El autor de este modpack no permite descargarlo desde otras aplicaciones." +
                              (f" Descarga el .zip en {web} e impórtalo con «Importar modpack»." if web else ""))
    return os.path.join(destino, nombre), str(f.get("displayName") or nombre)


def _carpeta_clase(clase):
    return {CLASE_RESOURCEPACK: "resourcepacks", CLASE_SHADER: "shaderpacks"}.get(clase, "mods")


def _en_trozos(lista, n):
    for i in range(0, len(lista), n): yield lista[i:i + n]


def leer_indice_zip(ruta):
    """Valida un modpack de CurseForge (.zip) y lo devuelve en el formato común de modpack.py."""
    try:
        with zipfile.ZipFile(ruta) as zf:
            infos = zf.infolist()
            if len(infos) > 20000 or sum(i.file_size for i in infos) > MAX_BYTES_ZIP:
                raise ErrorCurseForge("El modpack es demasiado grande o está mal formado.")
            try: info = zf.getinfo("manifest.json")
            except KeyError: raise ErrorCurseForge("No es un modpack de CurseForge válido (falta manifest.json).")
            if info.file_size > 20_000_000: raise ErrorCurseForge("El manifest del modpack es demasiado grande.")
            man = json.loads(zf.read(info).decode("utf-8-sig"))
    except zipfile.BadZipFile:
        raise ErrorCurseForge("El archivo está dañado o no es un .zip.")
    except (ValueError, UnicodeDecodeError):
        raise ErrorCurseForge("El manifest del modpack no se puede leer.")
    if man.get("manifestType") != "minecraftModpack": raise ErrorCurseForge("No es un modpack de Minecraft de CurseForge.")
    mcinfo = man.get("minecraft") or {}
    mc = str(mcinfo.get("version") or "")
    if not re.fullmatch(r"[\w.\-]{1,30}", mc): raise ErrorCurseForge("El modpack no indica una versión de Minecraft válida.")
    tipo, loader_version = "Vanilla", None
    cargadores = [l for l in (mcinfo.get("modLoaders") or []) if isinstance(l, dict) and isinstance(l.get("id"), str)]
    if cargadores:
        l = next((x for x in cargadores if x.get("primary")), cargadores[0])
        nombre_l, _, ver = l["id"].partition("-")
        if nombre_l not in _MANIFEST_LOADER: raise ErrorCurseForge(f"Loader no compatible: {nombre_l}.")
        tipo, loader_version = _MANIFEST_LOADER[nombre_l], ver or None
    entradas = [x for x in (man.get("files") or []) if isinstance(x, dict) and _ID.fullmatch(str(x.get("projectID", ""))) and _ID.fullmatch(str(x.get("fileID", "")))]
    if len(entradas) > 3000: raise ErrorCurseForge("El modpack tiene demasiados archivos.")
    sobre = str(man.get("overrides") or "overrides").strip("/")
    if not re.fullmatch(r"[\w\- .]{1,60}", sobre): sobre = "overrides"
    # Un solo par de llamadas por tandas de 200: datos de los archivos y de sus proyectos
    archivos_api, mods_api = {}, {}
    for tanda in _en_trozos([int(x["fileID"]) for x in entradas], 200):
        for f in (_api("POST", "/mods/files", json={"fileIds": tanda}).get("data") or []): archivos_api[f["id"]] = f
    for tanda in _en_trozos(sorted({int(x["projectID"]) for x in entradas}), 200):
        for m in (_api("POST", "/mods", json={"modIds": tanda}).get("data") or []): mods_api[m["id"]] = m
    archivos = []
    for x in entradas:
        pid, fid = int(x["projectID"]), int(x["fileID"])
        m, f = mods_api.get(pid) or {}, archivos_api.get(fid)
        if m.get("classId") == CLASE_MUNDO: continue                    # los mundos no se instalan
        nombre_mod = str(m.get("name") or f"proyecto {pid}")
        if f is None:
            archivos.append({"path": f"mods/{pid}-{fid}.jar", "hashes": {}, "downloads": [], "env": None,
                             "manual": {"nombre": nombre_mod, "url": _web_de(m, fid)}}); continue
        url = _url_descarga(f)
        try: ruta_rel = f"{_carpeta_clase(m.get('classId'))}/{_limpiar_nombre(f['fileName'], ('.jar', '.zip'))}"
        except ErrorCurseForge: continue
        archivos.append({"path": ruta_rel, "hashes": {"sha1": _sha1(f)} if _sha1(f) else {}, "downloads": [url] if url else [],
                         "env": None, "manual": None if url else {"nombre": nombre_mod, "url": _web_de(m, fid)}})
    return {"nombre": str(man.get("name") or "Modpack").strip(), "mc": mc, "tipo": tipo, "loader_version": loader_version,
            "archivos": archivos, "prefijos": (sobre + "/",), "version_pack": str(man.get("version") or "")}