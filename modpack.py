"""
Modpacks para Dome Launcher: instala y ACTUALIZA modpacks como instancias.

Formatos:
  Modrinth (.mrpack)   modrinth.index.json + overrides/ + client-overrides/
  CurseForge (.zip)    manifest.json + overrides/   (ver curseforge.py)
Los dos se convierten a un mismo "índice" interno, así que instalar y actualizar funcionan igual.

Cada instancia instalada desde Modrinth/CurseForge guarda:
  - en instancias.json -> "pack": {fuente, proyecto, version_id, version, mc, tipo, loader_version}
  - en su carpeta      -> dome_pack.json: qué archivos puso el modpack (con su hash) y el hash de
                          cada archivo de configuración (overrides) tal como lo dejó el modpack.

Actualizar (mantiene tus datos):
  * Mundos, capturas, mods que añadiste tú y configs que hayas editado NO se tocan.
  * Los mods del modpack se descargan primero a una carpeta temporal; solo si todo salió bien se
    mueven a su sitio y se borran los que el modpack ya no incluye. Si algo falla, no cambia nada.
  * Una config del modpack solo se sobrescribe si sigue igual que cuando se instaló.

Seguridad: cada archivo se verifica con su hash, las rutas se validan (nada puede escribirse fuera
de la carpeta de la instancia) y solo se aceptan los servidores permitidos.
"""
import os, re, json, zipfile, hashlib, shutil, tempfile, threading, urllib.parse
from concurrent.futures import ThreadPoolExecutor
import nucleo as nu
import modrinth as mr
import curseforge as cf
import logos
import rendimiento as rd

ErrorPack = mr.ErrorModrinth
HOSTS_PACK = ("cdn.modrinth.com", "github.com", "raw.githubusercontent.com", "gitlab.com")  # los que permite la spec
REGISTRO = "dome_pack.json"
CARPETA_TEMP = ".dome_update"
MAX_ARCHIVOS = 3000
MAX_BYTES_ARCHIVO = 400 * 1024 * 1024
MAX_BYTES_ZIP = 2 * 1024 ** 3
_TIPOS = {"forge": "Forge", "neoforge": "NeoForge", "fabric-loader": "Fabric", "quilt-loader": "Quilt"}
_ID_VERSION = re.compile(r"[A-Za-z0-9]{1,20}")


def _host_ok(h):
    return h in HOSTS_PACK or str(h or "").endswith(".forgecdn.net")


# --------------------------------------------------------------- lectura
def leer_indice(ruta):
    """Valida el .mrpack y devuelve el índice común {nombre, mc, tipo, loader_version, archivos, prefijos}."""
    try:
        with zipfile.ZipFile(ruta) as zf:
            infos = zf.infolist()
            if len(infos) > 20000 or sum(i.file_size for i in infos) > MAX_BYTES_ZIP:
                raise ErrorPack("El modpack es demasiado grande o está mal formado.")
            try:
                info = zf.getinfo("modrinth.index.json")
            except KeyError:
                raise ErrorPack("No es un modpack de Modrinth válido (falta modrinth.index.json).")
            if info.file_size > 20_000_000: raise ErrorPack("El índice del modpack es demasiado grande.")
            idx = json.loads(zf.read(info).decode("utf-8-sig"))
    except zipfile.BadZipFile:
        raise ErrorPack("El archivo está dañado o no es un .mrpack.")
    except (ValueError, UnicodeDecodeError):
        raise ErrorPack("El índice del modpack no se puede leer.")
    if idx.get("formatVersion") != 1 or idx.get("game") != "minecraft":
        raise ErrorPack("Formato de modpack no compatible (solo Minecraft, formato 1).")
    deps = idx.get("dependencies") or {}
    mc = deps.get("minecraft")
    if not mc: raise ErrorPack("El modpack no indica la versión de Minecraft.")
    loaders = [k for k in _TIPOS if k in deps]
    if len(loaders) > 1: raise ErrorPack("El modpack pide más de un loader; no es compatible.")
    archivos = idx.get("files") or []
    if len(archivos) > MAX_ARCHIVOS: raise ErrorPack("El modpack tiene demasiados archivos.")
    return {"nombre": str(idx.get("name") or "Modpack").strip(), "mc": mc,
            "tipo": _TIPOS[loaders[0]] if loaders else "Vanilla",
            "loader_version": deps.get(loaders[0]) if loaders else None, "archivos": archivos,
            "prefijos": ("overrides/", "client-overrides/"), "version_pack": str(idx.get("versionId") or "")}


def _ruta_segura(base, rel):
    """Ruta absoluta dentro de 'base' o None si intenta escapar (.., rutas absolutas, unidades)."""
    rel = str(rel).replace("\\", "/")
    partes = rel.split("/")
    if not rel or rel.startswith("/") or (len(rel) > 1 and rel[1] == ":") or ".." in partes or "" in partes[:-1]:
        return None
    final = os.path.normpath(os.path.join(base, *partes))
    return final if final.startswith(os.path.normpath(base) + os.sep) else None


def _pendientes(idx):
    return [a for a in idx["archivos"] if (a.get("env") or {}).get("client") != "unsupported"]


def _hash_de(a):
    hs = a.get("hashes") or {}
    if hs.get("sha512"): return {"a": "sha512", "h": str(hs["sha512"]).lower()}
    if hs.get("sha1"): return {"a": "sha1", "h": str(hs["sha1"]).lower()}
    return {"a": "", "h": ""}


def _registro_archivos(pendientes):
    """{ruta: {a, h, manual}} de lo que el modpack instala."""
    return {a["path"]: {**_hash_de(a), "manual": bool(a.get("manual") and not a.get("downloads"))} for a in pendientes if a.get("path")}


# ------------------------------------------------------------- descargas
def _bajar(a, base, cb_sumar, faltantes):
    destino = _ruta_segura(base, a.get("path", ""))
    if destino is None: raise ErrorPack(f"Ruta no permitida dentro del modpack: {a.get('path')}")
    urls = [u for u in a.get("downloads", []) if urllib.parse.urlparse(u).scheme == "https"
            and _host_ok(urllib.parse.urlparse(u).hostname)]
    if not urls and a.get("manual"):          # el autor no permite descargarlo desde otras apps (CurseForge)
        faltantes.append({"nombre": a["manual"].get("nombre", os.path.basename(destino)), "url": a["manual"].get("url", ""), "path": a["path"]})
        cb_sumar(); return
    hs = a.get("hashes") or {}
    esperado, algoritmo = (hs.get("sha512"), "sha512") if hs.get("sha512") else (hs.get("sha1"), "sha1")
    if not esperado: raise ErrorPack(f"{a['path']} no trae hash para verificarlo.")
    if not urls: raise ErrorPack(f"{os.path.basename(destino)}: no tiene una descarga en un servidor permitido.")
    os.makedirs(os.path.dirname(destino), exist_ok=True)
    parcial, ultimo_error = destino + ".part", None
    for url in urls:
        h, total = hashlib.new(algoritmo), 0
        try:
            with open(parcial, "wb") as out:
                for trozo in mr._stream(url):
                    total += len(trozo)
                    if total > MAX_BYTES_ARCHIVO: raise ErrorPack("Un archivo del modpack es demasiado grande.")
                    h.update(trozo); out.write(trozo)
            if h.hexdigest().lower() != esperado.lower():
                raise ErrorPack(f"{os.path.basename(destino)}: la descarga está corrupta (el hash no coincide).")
            os.replace(parcial, destino); cb_sumar(); return
        except ErrorPack as e:
            ultimo_error = e
        finally:
            if os.path.exists(parcial): os.remove(parcial)
    raise ultimo_error


def _descargar_todo(lista, base, cb, faltantes):
    """Descarga los archivos en paralelo (hilos elegidos en Ajustes → Rendimiento). Si alguno falla se cancela todo."""
    total = len(lista)
    cb["setMax"](max(1, total)); cb["setStatus"](f"Descargando archivos (0/{total})…")
    hecho, lock = [0], threading.Lock()
    def sumar():
        with lock:
            hecho[0] += 1
            cb["setProgress"](hecho[0]); cb["setStatus"](f"Descargando archivos ({hecho[0]}/{total})…")
    pool = ThreadPoolExecutor(max_workers=rd.hilos())
    try:
        for fut in [pool.submit(_bajar, a, base, sumar, faltantes) for a in lista]:
            fut.result()
    finally:
        pool.shutdown(wait=True, cancel_futures=True)


# -------------------------------------------------------------- overrides
def _entradas_overrides(zf, prefijos):
    """{ruta_relativa: ZipInfo}; si un archivo está en varias carpetas, gana la última (client-overrides)."""
    res = {}
    for pref in prefijos:
        for info in zf.infolist():
            if info.filename.startswith(pref) and not info.is_dir():
                res[info.filename[len(pref):]] = info
    return res


def _sha1_flujo(f):
    h = hashlib.sha1()
    for b in iter(lambda: f.read(1 << 20), b""): h.update(b)
    return h.hexdigest()


def _sha1_archivo(ruta):
    with open(ruta, "rb") as f: return _sha1_flujo(f)


def _extraer(zf, info, destino):
    os.makedirs(os.path.dirname(destino), exist_ok=True)
    h = hashlib.sha1()
    with zf.open(info) as src, open(destino, "wb") as out:
        for b in iter(lambda: src.read(1 << 20), b""): h.update(b); out.write(b)
    return h.hexdigest()


def _aplicar_overrides(ruta, base, prefijos):
    """Instalación nueva: copia las configs del modpack y devuelve {ruta: sha1} de lo escrito."""
    escritos = {}
    with zipfile.ZipFile(ruta) as zf:
        for rel, info in _entradas_overrides(zf, prefijos).items():
            destino = _ruta_segura(base, rel)
            if destino is None: continue                  # entrada sospechosa: se ignora
            escritos[rel] = _extraer(zf, info, destino)
    return escritos


def _guardar_registro(base, origen, archivos, overrides):
    with open(os.path.join(base, REGISTRO), "w", encoding="utf-8") as f:
        json.dump({"origen": origen, "archivos": archivos, "overrides": overrides}, f, ensure_ascii=False, indent=1)


# ------------------------------------------------------------ instalación
def _nombre_libre(nombre, inst):
    nombre = (nombre or "Modpack").strip()[:30] or "Modpack"
    n, candidato = 2, nombre
    while candidato in inst:
        sufijo = f" {n}"; candidato = nombre[:30 - len(sufijo)] + sufijo; n += 1
    return candidato


def _instalar(idx, ruta_zip, cb, nombre=None, ram=None, origen=None):
    """Instala un índice común como instancia nueva. Devuelve {nombre, faltantes}."""
    inst = nu.leer(nu.RUTA_INST, {})
    nombre = _nombre_libre(nombre or idx["nombre"], inst)
    cb["setStatus"](f"Instalando Minecraft {idx['mc']}…")
    v_id = nu.instalar(idx["tipo"], idx["mc"], cb, loader_version=idx["loader_version"])
    nueva = {"tipo": idx["tipo"], "version_id": v_id, "version_target": idx["mc"], "icono": "Barrel.png",
             "loader": nu.id_loader(idx["tipo"]), "carpeta": nu.slug_unico(nombre, inst),
             "ram": str(ram or ("6" if len(idx["archivos"]) >= 40 else "4")), "java_executable": "java",
             "custom_jvm_args": "", "resolution_w": "854", "resolution_h": "480"}
    if origen:
        nueva["pack"] = {**origen, "mc": idx["mc"], "tipo": idx["tipo"], "loader_version": idx["loader_version"]}
    base = nu.preparar_carpeta(nueva)
    faltantes = []
    try:
        pendientes = _pendientes(idx)
        _descargar_todo(pendientes, base, cb, faltantes)
        cb["setStatus"]("Aplicando configuración del modpack…")
        sobre = _aplicar_overrides(ruta_zip, base, idx["prefijos"])
        _guardar_registro(base, nueva.get("pack"), _registro_archivos(pendientes), sobre)
    except BaseException:
        shutil.rmtree(base, ignore_errors=True)       # no dejar una instancia a medias
        raise
    if origen:                                           # el logo del modpack pasa a ser el icono de la instancia
        try:
            cb["setStatus"]("Descargando el logo del modpack…")
            logo = logos.de_proyecto(origen.get("fuente"), origen.get("proyecto"))
            if logo: nueva["logo"] = logo
        except Exception:
            pass                                         # sin logo no pasa nada: se queda el icono normal
    inst = nu.leer(nu.RUTA_INST, {})
    inst[nombre] = nueva
    nu.guardar_inst(inst)
    return {"nombre": nombre, "faltantes": faltantes}


def instalar_mrpack(ruta, cb, nombre=None, ram=None, origen=None):
    return _instalar(leer_indice(ruta), ruta, cb, nombre, ram, origen)


def instalar_zip_curseforge(ruta, cb, nombre=None, ram=None, origen=None):
    cb["setStatus"]("Leyendo el modpack de CurseForge…")
    return _instalar(cf.leer_indice_zip(ruta), ruta, cb, nombre, ram, origen)


def instalar_archivo(ruta, cb):
    """Importa un modpack local: .mrpack (Modrinth) o .zip (CurseForge); lo reconoce por su contenido."""
    try:
        with zipfile.ZipFile(ruta) as zf: nombres = set(zf.namelist())
    except (zipfile.BadZipFile, OSError):
        raise ErrorPack("El archivo está dañado o no es un modpack.")
    if "modrinth.index.json" in nombres: return instalar_mrpack(ruta, cb)
    if "manifest.json" in nombres: return instalar_zip_curseforge(ruta, cb)
    raise ErrorPack("No parece un modpack de Modrinth (.mrpack) ni de CurseForge (.zip).")


# --------------------------------------------------------------- Modrinth
def versiones(project_id):
    """Versiones instalables del modpack (las más nuevas primero)."""
    out = []
    for v in mr._get(f"/project/{project_id}/version")[:40]:
        if not any(f.get("filename", "").lower().endswith(".mrpack") for f in v.get("files", [])): continue
        out.append({"id": v["id"], "nombre": v.get("name") or v.get("version_number", ""),
                    "mc": (v.get("game_versions") or ["?"])[-1], "loaders": v.get("loaders", []),
                    "tipo": v.get("version_type", "release")})
    return out


def _version_modrinth(version_id, proyecto=None):
    if not _ID_VERSION.fullmatch(str(version_id)): raise ErrorPack("Versión no válida.")
    v = mr._get(f"/version/{version_id}")
    if proyecto and v.get("project_id") != proyecto: raise ErrorPack("Esa versión no pertenece a este modpack.")
    archivo = next((f for f in v.get("files", []) if f.get("filename", "").lower().endswith(".mrpack")), None)
    if not archivo: raise ErrorPack("Esa versión no trae un archivo .mrpack.")
    return v, archivo


def instalar_desde_modrinth(version_id, cb, nombre=None, ram=None):
    v, archivo = _version_modrinth(version_id)
    tmp = tempfile.mkdtemp(prefix="dome_pack_")
    try:
        cb["setStatus"]("Descargando el modpack…")
        guardado = mr._descargar(archivo, tmp, extensiones=(".mrpack",))
        origen = {"fuente": "modrinth", "proyecto": v["project_id"], "version_id": v["id"],
                  "version": v.get("version_number") or v.get("name", "")}
        return instalar_mrpack(os.path.join(tmp, guardado), cb, nombre, ram, origen)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# -------------------------------------------------------------- CurseForge
def instalar_desde_curseforge(proyecto, file_id, cb, nombre=None, ram=None):
    tmp = tempfile.mkdtemp(prefix="dome_pack_")
    try:
        cb["setStatus"]("Descargando el modpack…")
        ruta, version = cf.descargar_modpack(proyecto, file_id, tmp)
        origen = {"fuente": "curseforge", "proyecto": str(int(proyecto)), "version_id": str(int(file_id)), "version": version}
        return instalar_zip_curseforge(ruta, cb, nombre, ram, origen)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def versiones_de(fuente, proyecto):
    if fuente == "curseforge": return cf.versiones_modpack(proyecto)
    if fuente == "modrinth": return versiones(proyecto)
    raise ErrorPack("Fuente no válida.")


# ----------------------------------------------------------- actualización
def _origen(nombre):
    d = nu.leer(nu.RUTA_INST, {}).get(nombre)
    if not d: raise ErrorPack("Instancia no encontrada.")
    o = d.get("pack")
    if not (isinstance(o, dict) and o.get("fuente") in ("modrinth", "curseforge") and o.get("proyecto") and o.get("version_id")):
        raise ErrorPack("Esta instancia no se instaló desde Modrinth ni CurseForge, así que no se puede actualizar desde aquí.")
    return d, o


def comprobar_actualizacion(nombre):
    """Versiones del modpack más nuevas que la instalada: {actual, mc_actual, nuevas[], sugerida}."""
    d, o = _origen(nombre)
    vs = versiones_de(o["fuente"], o["proyecto"])
    ids = [v["id"] for v in vs]
    nuevas = vs[:ids.index(o["version_id"])] if o["version_id"] in ids else vs
    sugerida = next((v["id"] for v in nuevas if v["tipo"] == "release"), nuevas[0]["id"] if nuevas else "")
    return {"fuente": o["fuente"], "actual": o.get("version", ""), "mc_actual": d.get("version_target", ""),
            "nuevas": nuevas, "sugerida": sugerida}


def _paquete_origen(o, version_id, tmp):
    """Descarga la versión elegida: (ruta_zip, índice, nombre_versión, id_versión)."""
    if o["fuente"] == "modrinth":
        v, archivo = _version_modrinth(version_id, o["proyecto"])
        ruta = os.path.join(tmp, mr._descargar(archivo, tmp, extensiones=(".mrpack",)))
        return ruta, leer_indice(ruta), v.get("version_number") or v.get("name", ""), v["id"]
    ruta, nombre = cf.descargar_modpack(o["proyecto"], version_id, tmp)
    return ruta, cf.leer_indice_zip(ruta), nombre, str(int(version_id))


def actualizar(nombre, version_id, cb):
    """Actualiza la instancia a otra versión de su modpack sin tocar mundos ni tus cambios."""
    d, o = _origen(nombre)
    if not d.get("carpeta"): raise ErrorPack("Esta instancia no tiene carpeta propia; no se puede actualizar.")
    base = nu.carpeta_juego(d)
    reg = nu.leer(os.path.join(base, REGISTRO), {})
    viejos = reg.get("archivos") if isinstance(reg.get("archivos"), dict) else {}
    ov_viejos = reg.get("overrides") if isinstance(reg.get("overrides"), dict) else {}
    tmp = tempfile.mkdtemp(prefix="dome_upd_")
    stage = os.path.join(base, CARPETA_TEMP)
    try:
        cb["setStatus"]("Descargando la nueva versión…")
        ruta_zip, idx, nombre_version, vid = _paquete_origen(o, version_id, tmp)
        if vid == o["version_id"]: raise ErrorPack("Ya tienes instalada esa versión.")
        # ¿cambia Minecraft o el loader? Entonces hay que instalar el nuevo
        cambia = (idx["mc"], idx["tipo"], idx["loader_version"]) != (o.get("mc"), o.get("tipo"), o.get("loader_version"))
        v_id = d["version_id"]
        if cambia:
            cb["setStatus"](f"Instalando Minecraft {idx['mc']}…")
            v_id = nu.instalar(idx["tipo"], idx["mc"], cb, loader_version=idx["loader_version"])
        pendientes = _pendientes(idx)
        nuevos = _registro_archivos(pendientes)
        bajar = []
        for a in pendientes:
            p = a["path"]; destino = _ruta_segura(base, p)
            if destino is None: raise ErrorPack(f"Ruta no permitida dentro del modpack: {p}")
            vieja, nueva = viejos.get(p) or {}, nuevos[p]
            existe = os.path.exists(destino) or os.path.exists(destino + ".disabled")
            if existe and nueva["h"] and vieja.get("a") == nueva["a"] and vieja.get("h") == nueva["h"]: continue   # sin cambios
            if existe and nueva["manual"]: continue            # lo puso el usuario a mano
            bajar.append(a)
        faltantes, anadidos, quitados = [], 0, 0
        shutil.rmtree(stage, ignore_errors=True)
        try:
            _descargar_todo(bajar, stage, cb, faltantes)       # todo a la carpeta temporal primero
            for p in viejos:                                   # lo que el modpack ya no incluye
                if p in nuevos: continue
                destino = _ruta_segura(base, p)
                if not destino: continue
                borrado = False
                for suf in ("", ".disabled"):
                    if os.path.isfile(destino + suf): os.remove(destino + suf); borrado = True
                quitados += borrado
            for a in bajar:                                    # lo nuevo, a su sitio
                origen_f, destino = _ruta_segura(stage, a["path"]), _ruta_segura(base, a["path"])
                if not (origen_f and os.path.isfile(origen_f)): continue
                if os.path.exists(destino + ".disabled") and not os.path.exists(destino): destino += ".disabled"   # respeta mods que desactivaste
                os.makedirs(os.path.dirname(destino), exist_ok=True)
                os.replace(origen_f, destino); anadidos += 1
        finally:
            shutil.rmtree(stage, ignore_errors=True)
        # configuración del modpack: solo se sobrescribe lo que no has tocado
        cb["setStatus"]("Aplicando configuración del modpack…")
        nuevo_ov, conservados = {}, 0
        with zipfile.ZipFile(ruta_zip) as zf:
            for rel, info in _entradas_overrides(zf, idx["prefijos"]).items():
                destino = _ruta_segura(base, rel)
                if destino is None: continue
                with zf.open(info) as src: h_nuevo = _sha1_flujo(src)
                h_viejo = ov_viejos.get(rel)
                if not os.path.isfile(destino):
                    _extraer(zf, info, destino); nuevo_ov[rel] = h_nuevo; continue
                actual = _sha1_archivo(destino)
                if actual == h_nuevo:
                    nuevo_ov[rel] = h_nuevo
                elif h_viejo is not None and actual == h_viejo:    # sigue como lo dejó el modpack: se actualiza
                    _extraer(zf, info, destino); nuevo_ov[rel] = h_nuevo
                else:                                              # la editaste tú: se respeta
                    conservados += 1
                    if h_viejo is not None: nuevo_ov[rel] = h_viejo
        # todo bien: se anota la nueva versión
        inst = nu.leer(nu.RUTA_INST, {})
        actual = inst.get(nombre)
        if actual is None: raise ErrorPack("La instancia ya no existe.")
        pack = {**o, "version_id": vid, "version": nombre_version, "mc": idx["mc"], "tipo": idx["tipo"], "loader_version": idx["loader_version"]}
        actual.update(version_id=v_id, version_target=idx["mc"], tipo=idx["tipo"], loader=nu.id_loader(idx["tipo"]), pack=pack)
        nu.guardar_inst(inst)
        _guardar_registro(base, pack, nuevos, nuevo_ov)
        return {"nombre": nombre, "version": nombre_version, "anadidos": anadidos, "quitados": quitados,
                "conservados": conservados, "faltantes": faltantes}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
