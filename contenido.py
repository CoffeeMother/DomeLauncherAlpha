
import os, re, json
import nucleo as nu
import modrinth as mr
import curseforge as cf

TIPOS = ("modpack", "mod", "resourcepack", "datapack", "shader")
LOADERS = {"fabric", "quilt", "forge", "neoforge"}
ORDENES = ("relevance", "downloads", "follows", "newest", "updated")
_cache_categorias = {}
_versiones_juego = []


FUENTES = ("modrinth", "curseforge")


def _validar_tipo(tipo):
    if tipo not in TIPOS: raise mr.ErrorModrinth("Tipo de contenido no válido.")


def _validar_fuente(fuente, tipo):
    if fuente not in FUENTES: raise mr.ErrorModrinth("Fuente no válida.")



def versiones_juego():
    """Versiones release de Minecraft para el filtro (las más nuevas primero)."""
    if not _versiones_juego:
        datos = mr._get("/tag/game_version")
        _versiones_juego.extend(v["version"] for v in datos if v.get("version_type") == "release")
    return list(_versiones_juego)


def categorias(tipo, fuente="modrinth"):
    """Categorías de ese tipo de contenido. Modrinth: lista de nombres; CurseForge: lista de {id, nombre}."""
    _validar_tipo(tipo)
    if fuente == "curseforge": return cf.categorias(tipo)
    if tipo not in _cache_categorias:
        # Los data packs comparten categorías con los mods
        tipos_api = {"datapack": {"datapack", "mod"}}.get(tipo, {tipo})
        vistos, res = set(), []
        for c in mr._get("/tag/category"):
            if c.get("project_type") in tipos_api and c.get("header") == "categories" and c["name"] not in vistos:
                vistos.add(c["name"]); res.append(c["name"])
        _cache_categorias[tipo] = res
    return list(_cache_categorias[tipo])


def mundos(d):
    """Mundos (carpetas con level.dat) de la instancia, para instalar data packs."""
    saves = os.path.join(nu.carpeta_juego(d), "saves")
    try:
        return sorted(n for n in os.listdir(saves) if os.path.isfile(os.path.join(saves, n, "level.dat")))
    except OSError:
        return []


# ------------------------------------------------------------- buscar
def buscar(tipo, texto, pagina=0, orden="downloads", version="", loaders=None, categorias=None, d=None, fuente="modrinth"):
    """Busca contenido. Filtros: versión de Minecraft, loaders (cualquiera) y categorías (todas)."""
    _validar_tipo(tipo); _validar_fuente(fuente, tipo)
    if fuente == "curseforge":
        return cf.buscar(tipo, texto, pagina, orden, version, loaders, d,
                         [c for c in (categorias or []) if isinstance(c, str) and re.fullmatch(r"[a-z0-9\-]{1,40}", c)])
    if orden not in ORDENES: orden = "downloads"
    if tipo == "datapack":   # en Modrinth los data packs llevan la categoría/loader 'datapack'
        facets = [["project_type:mod", "project_type:datapack"], ["categories:datapack"]]
    else:
        facets = [[f"project_type:{tipo}"]]
    if version and re.fullmatch(r"[\w.\-]{1,20}", str(version)): facets.append([f"versions:{version}"])
    ls = [l for l in (loaders or []) if l in LOADERS] if tipo in ("modpack", "mod") else []
    if ls: facets.append([f"categories:{l}" for l in ls])
    for c in categorias or []:
        if isinstance(c, str) and re.fullmatch(r"[a-z0-9\-]{1,40}", c): facets.append([f"categories:{c}"])
    r = mr._get("/search", {"query": texto or "", "facets": json.dumps(facets), "limit": 20,
                            "offset": max(0, int(pagina)) * 20, "index": orden})
    ya = set()
    if tipo == "mod" and d and nu.loader_de(d):
        try: ya = mr._proyectos_instalados(d)
        except Exception: pass
    return {"total": r.get("total_hits", 0), "pagina": int(pagina), "resultados": [
        {"id": h["project_id"], "titulo": h.get("title", ""), "autor": h.get("author", ""),
         "descripcion": h.get("description", ""), "icono": h.get("icon_url") or "", "descargas": h.get("downloads", 0),
         "galeria": h.get("featured_gallery") or next(iter(h.get("gallery") or []), "") or "", "color": h.get("color"),
         "instalado": h["project_id"] in ya} for h in r.get("hits", [])]}


# ----------------------------------------------------------- instalar
def _mejor_version(pid, mc):
    vs = mr._get(f"/project/{pid}/version", {"game_versions": json.dumps([mc])})
    for tipo in ("release", "beta", "alpha"):   # estable primero
        for v in vs:
            if v.get("version_type") == tipo: return v
    return None


def _archivo_zip(v):
    zips = [f for f in v.get("files", []) if f.get("filename", "").lower().endswith(".zip")]
    if not zips: raise mr.ErrorModrinth("Esa versión no trae un archivo .zip.")
    return next((f for f in zips if f.get("primary")), zips[0])


def instalar(d, tipo, project_id, titulo="", mundo="", fuente="modrinth"):
    """Instala contenido en la instancia d. Devuelve {instalados, dependencias, omitidos, sin_version}."""
    _validar_tipo(tipo); _validar_fuente(fuente, tipo)
    if tipo == "modpack": raise mr.ErrorModrinth("Los modpacks se instalan como instancia nueva.")
    if tipo == "mod": return cf.instalar_mod(d, project_id, titulo) if fuente == "curseforge" else mr.instalar(d, project_id, titulo)
    res = {"instalados": [], "dependencias": [], "omitidos": [], "sin_version": []}
    base = nu.preparar_carpeta(d)
    if tipo == "datapack":
        if not mundo or os.path.basename(mundo) != mundo or mundo in (".", ".."):
            raise mr.ErrorModrinth("Elige el mundo donde instalar el data pack.")
        if not os.path.isfile(os.path.join(base, "saves", mundo, "level.dat")):
            raise mr.ErrorModrinth("Ese mundo no existe en la instancia.")
        destino = os.path.join(base, "saves", mundo, "datapacks")
        os.makedirs(destino, exist_ok=True)
    else:
        destino = os.path.join(base, "resourcepacks" if tipo == "resourcepack" else "shaderpacks")
    mc = d.get("version_target") or d.get("version_id")
    if fuente == "curseforge": return cf.instalar_zip(destino, project_id, mc, titulo)
    v = _mejor_version(project_id, mc)
    if v is None: raise mr.ErrorModrinth(f"No hay una versión compatible con Minecraft {mc}.")
    f = _archivo_zip(v)
    if os.path.exists(os.path.join(destino, os.path.basename(f["filename"]))):
        res["omitidos"].append(titulo or f["filename"]); return res
    archivo = mr._descargar(f, destino, extensiones=(".zip",))
    res["instalados"].append(titulo or archivo)
    return res
