
import os, re, json, shutil, subprocess, sys, time, zipfile
try:
    import tomllib            # Python 3.11+
except ImportError:           # Python más viejo: se lee el .toml con expresiones regulares
    tomllib = None
import minecraft_launcher_lib as mll

BASE = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), ".domeLauncher")
RUTA_INST = os.path.join(BASE, "instancias.json")
RUTA_CFG = os.path.join(BASE, "launcher_config.json")
CARPETA_INSTANCIAS = os.path.join(BASE, "instancias")
SUBCARPETAS = ("mods", "config", "saves", "resourcepacks", "shaderpacks", "logs")
os.makedirs(BASE, exist_ok=True)

# Tipo que ve el usuario -> id del loader en minecraft-launcher-lib (y en Modrinth, para los mods)
LOADERS = {"Forge": "forge", "NeoForge": "neoforge", "Fabric": "fabric", "Quilt": "quilt"}


def id_loader(tipo):
    """'forge' / 'neoforge' / 'fabric' / 'quilt' / '' (Vanilla). Es el mismo nombre que usa Modrinth."""
    return LOADERS.get(tipo, "")


# ------------------------------------------------------------ archivos
def leer(ruta, defecto):
    try:
        with open(ruta, encoding="utf-8") as f: return json.load(f)
    except Exception:
        return defecto


def guardar_inst(inst):
    with open(RUTA_INST, "w", encoding="utf-8") as f:
        json.dump(inst, f, ensure_ascii=False, indent=4)


# ----------------------------------------------------------- instancias
def _slug(nombre):
    s = re.sub(r"[^\w\- ]", "", nombre, flags=re.UNICODE).strip().replace(" ", "_")
    return s or "instancia"


def slug_unico(nombre, inst):
    usados = {d.get("carpeta") for d in inst.values()}
    base, n, s = _slug(nombre), 2, _slug(nombre)
    while s in usados or os.path.exists(os.path.join(CARPETA_INSTANCIAS, s)):
        s = f"{base}_{n}"; n += 1
    return s


def carpeta_juego(d):
    """Carpeta de juego de la instancia (donde viven mods, mundos, config)."""
    return os.path.join(CARPETA_INSTANCIAS, d["carpeta"]) if d.get("carpeta") else BASE


def preparar_carpeta(d):
    ruta = carpeta_juego(d)
    for s in SUBCARPETAS:
        os.makedirs(os.path.join(ruta, s), exist_ok=True)
    return ruta


def contar_mods(d):
    try:
        return sum(1 for f in os.listdir(os.path.join(carpeta_juego(d), "mods"))
                   if f.lower().endswith((".jar", ".jar.disabled")))
    except OSError:
        return 0


def abrir_carpeta(ruta):
    os.makedirs(ruta, exist_ok=True)
    if sys.platform.startswith("win"): os.startfile(ruta)
    elif sys.platform == "darwin": subprocess.Popen(["open", ruta])
    else: subprocess.Popen(["xdg-open", ruta])


# ------------------------------------------------------------- versiones
def _clave(v):
    return tuple(int(x) for x in re.findall(r"\d+", v))


_cache_versiones = {}      # tipo -> (momento, lista): abrir «Nueva instancia» es instantáneo la 2ª vez


def versiones(tipo):
    """Releases de Minecraft, nuevas primero (copia de 10 min). Con un loader, solo las que ese loader puede instalar."""
    ahora = time.time()
    if tipo in _cache_versiones and ahora - _cache_versiones[tipo][0] < 600: return list(_cache_versiones[tipo][1])
    res = _versiones(tipo)
    _cache_versiones[tipo] = (ahora, res)
    return list(res)


def _versiones(tipo):
    lista = [v["id"] for v in mll.utils.get_version_list() if v["type"] == "release"]
    if tipo in LOADERS:
        loader = mll.mod_loader.get_mod_loader(LOADERS[tipo])
        soportadas = set(loader.get_minecraft_versions(True))
        lista = [v for v in lista if v in soportadas and loader.is_minecraft_version_supported(v)]
    return lista


def _es_estable(v):
    return not any(x in v.lower() for x in ("beta", "alpha", "rc", "snapshot", "pre"))


def ultima_loader(tipo, mc):
    """Build MÁS NUEVA del loader para esa versión (prefiere las estables; si solo hay betas, usa la mejor beta)."""
    loader = mll.mod_loader.get_mod_loader(LOADERS[tipo])
    builds = loader.get_loader_versions(mc, False)
    if not builds: raise Exception(f"No existe {tipo} para Minecraft {mc}")
    return max(builds, key=lambda v: (_es_estable(v), _clave(v)))


# -------------------------------------------------------------- instalar
def java_de_mojang(mc):
    """Java que Mojang pide para esa versión (se descarga junto con Minecraft). None si no está."""
    try:
        with open(os.path.join(BASE, "versions", mc, f"{mc}.json"), encoding="utf-8") as f:
            comp = json.load(f)["javaVersion"]["component"]
        return mll.runtime.get_executable_path(comp, BASE)
    except Exception:
        return None


def instalar(tipo, mc, cb, loader_version=None):
    """Instala la versión y devuelve el id REAL con el que se lanza
    (p. ej. 1.20.1-forge-47.4.0, neoforge-21.1.77 o fabric-loader-0.16.9-1.21.1).
    loader_version: versión exacta del loader (la piden los modpacks); si no existe, se usa la más nueva."""
    mll.install.install_minecraft_version(mc, BASE, callback=cb)  # también baja el Java de Mojang
    if tipo not in LOADERS:
        return mc
    cb["setStatus"](f"Instalando {tipo} para {mc}…")
    loader = mll.mod_loader.get_mod_loader(LOADERS[tipo])
    build = loader_version
    if build:
        try: disponibles = set(loader.get_loader_versions(mc, False))
        except Exception: disponibles = None
        if disponibles is not None and build not in disponibles:
            cb["setStatus"](f"{tipo} {build} ya no está disponible; se usa la versión más nueva")
            build = None
    # Forge/NeoForge ejecutan Java al instalar: se usa el de Mojang, no el 'java' del sistema
    return loader.install(mc, BASE, loader_version=build or ultima_loader(tipo, mc), callback=cb,
                          java=java_de_mojang(mc) or "java")


def reparar_version_id(d):
    """Instancias Forge viejas guardaban un id que no existe en disco (1.20.1-47.x en vez de
    1.20.1-forge-47.x). Busca la carpeta real; devuelve True si corrigió algo."""
    if d.get("tipo") != "Forge": return False
    vers = os.path.join(BASE, "versions")
    if not os.path.isdir(vers) or os.path.isdir(os.path.join(vers, d.get("version_id", ""))): return False
    cand = [x for x in os.listdir(vers) if x.startswith(f"{d.get('version_target', '')}-forge-")]
    if not cand: return False
    d["version_id"] = max(cand, key=_clave)
    return True


# -------------------------------------------------------------- lanzar
def ram_mb(d):
    """RAM de la instancia en MB (acepta decimales: '6.5' = 6656 MB)."""
    try: gb = float(str(d.get("ram", "4")).replace(",", "."))
    except ValueError: gb = 4.0
    return max(512, int(round(gb * 1024)))


def comando_jugar(d, usuario, cfg, sesion=None, rend=None):
    """Devuelve (comando, carpeta_de_juego) para lanzar la instancia.
    sesion = {"name","uuid","token"} de una cuenta premium; si es None se juega sin conexión con el nombre dado."""
    rend = rend or {}
    xmx = ram_mb(d)
    xms = min(xmx, rend["xms_mb"]) if rend.get("xms_mb") else xmx
    jvm = [f"-Xmx{xmx}M", f"-Xms{xms}M"] + list(rend.get("flags") or []) + (d.get("custom_jvm_args") or cfg.get("argumentos_jvm", "")).split()
    juego = preparar_carpeta(d)
    if sesion: usuario, uid, token = sesion["name"], sesion["uuid"], sesion["token"]
    else: uid, token = "00000000-0000-0000-0000-000000000000", "00000000"
    op = {"username": usuario, "uuid": uid, "token": token,
          "jvmArguments": jvm, "launcherVersion": "2.0.0", "gameDirectory": juego,
          "width": d.get("resolution_w", "854"), "height": d.get("resolution_h", "480")}
    java = d.get("java_executable")
    if java and java != "java":
        op["executablePath"] = java          # Java elegido a mano por el usuario
    # Si no: la librería usa el Java de Mojang que corresponde a la versión (también para Forge)
    return mll.command.get_minecraft_command(d["version_id"], BASE, op), juego


# -------------------------------------------------------------- eliminar
def eliminar(nombre):
    inst = leer(RUTA_INST, {})
    d = inst.pop(nombre, None)
    if not d: return
    guardar_inst(inst)
    if d.get("carpeta"):  # nunca se borra .domeLauncher entero (instancias viejas)
        shutil.rmtree(os.path.join(CARPETA_INSTANCIAS, d["carpeta"]), ignore_errors=True)
    vid = d.get("version_id")
    # La versión se borra solo si ninguna otra instancia la usa (ni la necesita como base de Forge)
    if vid and not any(vid in (o.get("version_id"), o.get("version_target")) for o in inst.values()):
        shutil.rmtree(os.path.join(BASE, "versions", vid), ignore_errors=True)


# ================================================================== MODS
_cache_mods = {}   # (ruta, tamaño, fecha) -> metadatos del .jar (leer el zip cada vez sería lento)
_ACEPTA = {"forge": {"forge"}, "neoforge": {"neoforge"}, "fabric": {"fabric"}, "quilt": {"quilt", "fabric"}}


def _leer_entrada(zf, nombre, limite=2_000_000):
    try:
        if zf.getinfo(nombre).file_size > limite: return None
        return zf.read(nombre).decode("utf-8-sig", errors="replace")
    except KeyError:
        return None


def _toml(texto):
    """Lee el primer [[mods]] de un mods.toml / neoforge.mods.toml."""
    if tomllib:
        try:
            m = (tomllib.loads(texto).get("mods") or [{}])[0]
            return {k: str(m.get(k, "")) for k in ("modId", "displayName", "version", "description")}
        except Exception:
            pass
    r = {}
    for k in ("modId", "displayName", "version", "description"):
        mm = re.search(rf'^\s*{k}\s*=\s*(?:"""(.*?)"""|"([^"]*)")', texto, re.M | re.S)
        r[k] = (mm.group(1) or mm.group(2) or "").strip() if mm else ""
    return r


def leer_jar(ruta):
    """Nombre, versión y loader(s) de un mod leyendo su .jar (sin ejecutar nada)."""
    st = os.stat(ruta)
    clave = (ruta, st.st_size, st.st_mtime)
    if clave in _cache_mods: return _cache_mods[clave]
    meta = {"id": "", "nombre": "", "version": "", "descripcion": "", "loaders": []}
    try:
        with zipfile.ZipFile(ruta) as zf:
            nombres = set(zf.namelist())
            manifest = _leer_entrada(zf, "META-INF/MANIFEST.MF") or ""
            vman = (re.search(r"^Implementation-Version:\s*(.+)$", manifest, re.M) or [None, ""])[1].strip()
            if "fabric.mod.json" in nombres:
                meta["loaders"].append("fabric")
                j = json.loads(_leer_entrada(zf, "fabric.mod.json") or "{}", strict=False)
                meta.update(id=j.get("id", ""), nombre=j.get("name", ""), version=str(j.get("version", "")),
                            descripcion=j.get("description", ""))
            if "quilt.mod.json" in nombres:
                meta["loaders"].append("quilt")
                q = json.loads(_leer_entrada(zf, "quilt.mod.json") or "{}", strict=False).get("quilt_loader", {})
                if not meta["nombre"]:
                    meta.update(id=q.get("id", ""), version=str(q.get("version", "")),
                                nombre=(q.get("metadata") or {}).get("name", ""),
                                descripcion=(q.get("metadata") or {}).get("description", ""))
            for archivo, loader in (("META-INF/neoforge.mods.toml", "neoforge"), ("META-INF/mods.toml", "forge")):
                if archivo in nombres:
                    meta["loaders"].append(loader)
                    if not meta["nombre"]:
                        m = _toml(_leer_entrada(zf, archivo) or "")
                        meta.update(id=m["modId"], nombre=m["displayName"], version=m["version"], descripcion=m["description"])
            if "mcmod.info" in nombres and "forge" not in meta["loaders"]:
                meta["loaders"].append("forge")  # Forge viejo (1.12 y anteriores)
            if "${" in meta["version"] or meta["version"] in ("", "null"):
                meta["version"] = vman if "${" in meta["version"] or not meta["version"] else meta["version"]
    except (zipfile.BadZipFile, OSError, ValueError):
        meta["error"] = "Archivo dañado o no es un .jar"
    meta["descripcion"] = " ".join(str(meta["descripcion"]).split())[:160]
    _cache_mods[clave] = meta
    return meta


def compatibilidad(loader_inst, loaders_jar):
    """('ok'|'warn'|'bad', texto) según si el mod sirve para el loader de la instancia."""
    if not loaders_jar: return "warn", "No pude detectar para qué loader es"
    if loaders_jar and set(loaders_jar) & _ACEPTA.get(loader_inst, set()): return "ok", ""
    if loader_inst == "neoforge" and "forge" in loaders_jar: return "warn", "Mod de Forge: puede no funcionar en NeoForge"
    return "bad", f"Es un mod de {'/'.join(loaders_jar)}, esta instancia usa {loader_inst}"


def loader_de(d):
    return d.get("loader") or id_loader(d.get("tipo", ""))


def listar_mods(d):
    carpeta = os.path.join(carpeta_juego(d), "mods")
    loader, res = loader_de(d), []
    try: archivos = os.listdir(carpeta)
    except OSError: return res
    for f in archivos:
        bajo = f.lower()
        if not bajo.endswith((".jar", ".jar.disabled")): continue
        ruta = os.path.join(carpeta, f)
        meta = leer_jar(ruta)
        nivel, aviso = ("bad", meta["error"]) if meta.get("error") else compatibilidad(loader, meta["loaders"])
        res.append({"archivo": f, "activo": bajo.endswith(".jar"), "nombre": meta["nombre"] or re.sub(r"\.jar(\.disabled)?$", "", f, flags=re.I),
                    "id": meta["id"], "version": meta["version"], "descripcion": meta["descripcion"],
                    "loaders": meta["loaders"], "nivel": nivel, "aviso": aviso, "kb": round(os.path.getsize(ruta) / 1024)})
    return sorted(res, key=lambda m: m["nombre"].lower())


def _ruta_mod(d, archivo):
    if os.path.basename(archivo) != archivo or not archivo.lower().endswith((".jar", ".jar.disabled")):
        raise ValueError("Archivo de mod no válido")
    return os.path.join(carpeta_juego(d), "mods", archivo)


def activar_mod(d, archivo, activo):
    """Activa/desactiva un mod renombrando x.jar <-> x.jar.disabled (Forge/Fabric ignoran los .disabled)."""
    ruta = _ruta_mod(d, archivo)
    base = re.sub(r"\.disabled$", "", ruta, flags=re.I)
    destino = base if activo else base + ".disabled"
    if os.path.normcase(destino) != os.path.normcase(ruta):
        if os.path.exists(destino): raise FileExistsError("Ya existe un archivo con ese nombre")
        os.rename(ruta, destino)


def borrar_mod(d, archivo):
    os.remove(_ruta_mod(d, archivo))


def anadir_mods(d, rutas):
    """Copia .jar a la carpeta mods de la instancia. Devuelve (copiados, omitidos[(nombre, motivo)])."""
    destino = os.path.join(preparar_carpeta(d), "mods")
    copiados, omitidos = [], []
    for r in rutas or []:
        nombre = os.path.basename(r)
        if not nombre.lower().endswith(".jar") or not os.path.isfile(r): omitidos.append((nombre, "no es un .jar")); continue
        if os.path.exists(os.path.join(destino, nombre)) or os.path.exists(os.path.join(destino, nombre + ".disabled")):
            omitidos.append((nombre, "ya está instalado")); continue
        shutil.copy2(r, os.path.join(destino, nombre)); copiados.append(nombre)
    return copiados, omitidos
