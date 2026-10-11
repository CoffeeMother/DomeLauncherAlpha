"""
Cuenta premium (Microsoft / Minecraft Java) para jugar online.

Flujo: ventana de inicio de sesión de Microsoft (PKCE + state) -> Xbox Live -> XSTS -> Minecraft -> perfil.
- Solo se guarda el *refresh token* (cuenta.json en la carpeta de datos). El token de acceso del juego se pide
  de nuevo cada vez que se pulsa Jugar y vive solo en memoria.
- Ningún token llega a la interfaz: la UI solo recibe el nombre y el UUID.
- Hace falta un "Client ID" de una aplicación de Azure (ver README de ajustes). Se guarda en la configuración.
"""
import os, re, json, uuid as _uuid, threading, urllib.parse
import requests
import minecraft_launcher_lib as mll
import nucleo as nu

RUTA = os.path.join(nu.BASE, "cuenta.json")
REDIRECT = "https://login.live.com/oauth20_desktop.srf"
CLIENT_ID_INTEGRADO = "b0089c24-3d3b-4e3d-88d8-be58a39f4f6d"             # opcional: pon aquí el Client ID de tu app si repartes el launcher
_GUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_NOMBRE = re.compile(r"[A-Za-z0-9_]{1,16}")
_HEX32 = re.compile(r"[0-9a-fA-F]{32}")
_lock = threading.Lock()
_s = requests.Session()
_s.headers["User-Agent"] = "DomeLauncher/2.0"
TIEMPO = 20

_XERR = {
    2148916233: "Esa cuenta de Microsoft no tiene perfil de Xbox. Entra en xbox.com con ella, créalo y vuelve a intentarlo.",
    2148916235: "Xbox Live no está disponible en tu país o región.",
    2148916236: "Esa cuenta necesita verificación de adulto en Xbox antes de poder usarse.",
    2148916237: "Esa cuenta necesita verificación de adulto en Xbox antes de poder usarse.",
    2148916238: "Es una cuenta de menor: un adulto debe añadirla a un grupo familiar de Microsoft para poder jugar.",
}


class ErrorCuenta(Exception):
    def __init__(self, msg, caducada=False):
        super().__init__(msg)
        self.caducada = caducada          # True: la sesión guardada ya no sirve y se borró


# ------------------------------------------------------------------ configuración
def client_id():
    c = nu.leer(nu.RUTA_CFG, {})
    v = (c.get("azure_client_id") if isinstance(c, dict) else None) or os.environ.get("DOME_AZURE_CLIENT_ID") or CLIENT_ID_INTEGRADO
    return v if isinstance(v, str) and _GUID.fullmatch(v) else ""


def _leer():
    d = nu.leer(RUTA, {})
    if not (isinstance(d, dict) and isinstance(d.get("refresh_token"), str) and d["refresh_token"]
            and isinstance(d.get("name"), str) and _NOMBRE.fullmatch(d["name"]) and isinstance(d.get("id"), str) and _HEX32.fullmatch(d["id"])):
        return None
    return d


def _guardar(d):
    os.makedirs(os.path.dirname(RUTA), exist_ok=True)
    tmp = RUTA + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)      # solo el usuario (en Linux/Mac; Windows lo ignora)
    with os.fdopen(fd, "w", encoding="utf-8") as f: json.dump(d, f)
    os.replace(tmp, RUTA)


def cerrar_sesion():
    _cache.update(tok=None, t=0.0)
    with _lock:
        try: os.remove(RUTA)
        except FileNotFoundError: pass


def estado():
    """Lo único que ve la interfaz: si hay Client ID y quién ha iniciado sesión."""
    d = _leer()
    return {"client_configurado": bool(client_id()),
            "cuenta": {"nombre": d["name"], "uuid": _uuid_con_guiones(d["id"])} if d else None}


def _uuid_con_guiones(h):
    return str(_uuid.UUID(h))


# ------------------------------------------------------------------ inicio de sesión
def iniciar():
    """(url_de_login, state, code_verifier). Lanza ErrorCuenta si falta el Client ID."""
    cid = client_id()
    if not cid: raise ErrorCuenta("Falta el Client ID de Azure. Pégalo en Ajustes → Cuenta de Microsoft.")
    return mll.microsoft_account.get_secure_login_data(cid, REDIRECT)


def es_redireccion(url):
    """¿La ventana ya llegó a la página final de Microsoft (la que lleva el código)?"""
    try:
        p = urllib.parse.urlparse(url or "")
    except ValueError:
        return False
    return p.scheme == "https" and p.hostname == "login.live.com" and p.path == "/oauth20_desktop.srf"


def completar(url_final, state, verifier):
    """Termina el login con la URL a la que llegó la ventana. Guarda la sesión y devuelve estado()."""
    q = urllib.parse.parse_qs(urllib.parse.urlparse(url_final).query)
    if "error" in q:
        if q["error"][0] == "access_denied": raise ErrorCuenta("Cancelaste el inicio de sesión.")
        raise ErrorCuenta("Microsoft rechazó el inicio de sesión: " + (q.get("error_description", [q["error"][0]])[0])[:200])
    if q.get("state", [None])[0] != state or "code" not in q:
        raise ErrorCuenta("La respuesta de Microsoft no es válida. Inténtalo otra vez.")
    tok = _post_token({"client_id": client_id(), "scope": mll.microsoft_account.__SCOPE__, "code": q["code"][0],
                       "redirect_uri": REDIRECT, "grant_type": "authorization_code", "code_verifier": verifier},
                      mll.microsoft_account.__TOKEN_URL__)
    perfil = _cadena(tok["access_token"])
    with _lock:
        _guardar({"id": perfil["id"], "name": perfil["name"], "refresh_token": tok["refresh_token"]})
    return estado()


# ------------------------------------------------------------------ sesión para jugar
def sesion_para_jugar():
    """Renueva la sesión y devuelve {'name','uuid','token'} para lanzar el juego (token solo en memoria)."""
    d = _leer()
    if not d: raise ErrorCuenta("No hay ninguna sesión iniciada.", caducada=True)
    cid = client_id()
    if not cid: raise ErrorCuenta("Falta el Client ID de Azure (Ajustes → Cuenta de Microsoft).")
    try:
        tok = _post_token({"client_id": cid, "scope": mll.microsoft_account.__SCOPE__, "refresh_token": d["refresh_token"],
                           "grant_type": "refresh_token"}, mll.microsoft_account.__TOKEN_URL__)
    except ErrorCuenta as e:
        if e.caducada: cerrar_sesion()
        raise
    perfil = _cadena(tok["access_token"])
    with _lock:
        _guardar({"id": perfil["id"], "name": perfil["name"], "refresh_token": tok.get("refresh_token") or d["refresh_token"]})
    return {"name": perfil["name"], "uuid": _uuid_con_guiones(perfil["id"]), "token": perfil["mc_token"]}


# ------------------------------------------------------------------ pasos de la cadena de autenticación
def _red(fn, *a, **k):
    try:
        return fn(*a, timeout=TIEMPO, **k)
    except requests.RequestException:
        raise ErrorCuenta("No pude conectar con los servidores de Microsoft/Mojang (¿sin internet?).")


def _json(r):
    try: return r.json()
    except ValueError: raise ErrorCuenta(f"Respuesta no válida del servidor (código {r.status_code}).")


def _post_token(datos, url):
    r = _red(_s.post, url, data=datos)
    j = _json(r)
    if not isinstance(j, dict) or "access_token" not in j:
        err = j.get("error") if isinstance(j, dict) else ""
        if err in ("invalid_grant", "interaction_required", "invalid_request") and "refresh_token" in datos:
            raise ErrorCuenta("Tu sesión de Microsoft caducó. Inicia sesión otra vez.", caducada=True)
        if err in ("invalid_client", "unauthorized_client"):
            raise ErrorCuenta("El Client ID de Azure no es válido para este tipo de inicio de sesión (revisa que la app admita cuentas personales de Microsoft).")
        raise ErrorCuenta("Microsoft no aceptó el inicio de sesión" + (f" ({err})" if isinstance(err, str) and re.fullmatch(r"[\w.\-]{1,40}", err) else "") + ".")
    return j


def _cadena(ms_token):
    """Microsoft -> Xbox Live -> XSTS -> Minecraft -> perfil. Devuelve {'id','name','mc_token'}."""
    xbl = _json(_red(_s.post, "https://user.auth.xboxlive.com/user/authenticate", headers={"Accept": "application/json"},
                     json={"Properties": {"AuthMethod": "RPS", "SiteName": "user.auth.xboxlive.com", "RpsTicket": f"d={ms_token}"},
                           "RelyingParty": "http://auth.xboxlive.com", "TokenType": "JWT"}))
    try: xbl_tok, uhs = xbl["Token"], xbl["DisplayClaims"]["xui"][0]["uhs"]
    except (KeyError, IndexError, TypeError): raise ErrorCuenta("Xbox Live no aceptó la sesión de Microsoft.")

    xs = _json(_red(_s.post, "https://xsts.auth.xboxlive.com/xsts/authorize", headers={"Accept": "application/json"},
                    json={"Properties": {"SandboxId": "RETAIL", "UserTokens": [xbl_tok]},
                          "RelyingParty": "rp://api.minecraftservices.com/", "TokenType": "JWT"}))
    if not isinstance(xs, dict) or "Token" not in xs:
        raise ErrorCuenta(_XERR.get(xs.get("XErr") if isinstance(xs, dict) else None, "Xbox Live rechazó la cuenta."))

    mc = _json(_red(_s.post, "https://api.minecraftservices.com/authentication/login_with_xbox",
                    json={"identityToken": f"XBL3.0 x={uhs};{xs['Token']}"}))
    if not isinstance(mc, dict) or not isinstance(mc.get("access_token"), str):
        raise ErrorCuenta("Minecraft rechazó la sesión. Si usas tu propio Client ID de Azure, Mojang debe haber aprobado tu app (aka.ms/mce-reviewappid).")
    mc_tok = mc["access_token"]

    pr = _red(_s.get, "https://api.minecraftservices.com/minecraft/profile", headers={"Authorization": f"Bearer {mc_tok}"})
    perfil = _json(pr)
    if not isinstance(perfil, dict) or perfil.get("error") == "NOT_FOUND" or pr.status_code == 404:
        raise ErrorCuenta("Esa cuenta de Microsoft no tiene Minecraft: Java Edition comprado.")
    if not (isinstance(perfil.get("id"), str) and _HEX32.fullmatch(perfil["id"]) and isinstance(perfil.get("name"), str) and _NOMBRE.fullmatch(perfil["name"])):
        raise ErrorCuenta("Minecraft devolvió un perfil no válido.")
    return {"id": perfil["id"].lower(), "name": perfil["name"], "mc_token": mc_tok}


# ------------------------------------------------------------------ skin y capa (solo cuentas premium)
import base64, struct, time
API_MC = "https://api.minecraftservices.com/minecraft/profile"
MAX_SKIN = 24 * 1024                      # límite de Mojang para una skin
_ID_CAPA = re.compile(r"[0-9a-fA-F\-]{8,64}")
_cache = {"tok": None, "t": 0.0}


def _token():
    """Token del juego para tocar el perfil (se reutiliza 15 min para no repetir el login completo)."""
    if _cache["tok"] and time.time() - _cache["t"] < 900: return _cache["tok"]
    s = sesion_para_jugar()
    _cache.update(tok=s["token"], t=time.time())
    return s["token"]


def _llamar(metodo, url, **k):
    """Llamada autenticada al perfil; si el token ya no vale lo renueva una vez."""
    for intento in (0, 1):
        r = _red(_s.request, metodo, url, headers={"Authorization": f"Bearer {_token()}"}, **k)
        if r.status_code == 401 and intento == 0: _cache.update(tok=None); continue
        break
    if r.status_code == 429: raise ErrorCuenta("Mojang pide esperar un poco antes de otro cambio. Inténtalo en un minuto.")
    if r.status_code == 401: raise ErrorCuenta("Tu sesión de Microsoft ya no es válida. Inicia sesión otra vez.", caducada=True)
    return r


def _textura(url):
    """Descarga una textura oficial y la devuelve como data URI (solo PNG de textures.minecraft.net)."""
    if not isinstance(url, str): return ""
    p = urllib.parse.urlparse(url)
    if p.hostname != "textures.minecraft.net" or p.scheme not in ("http", "https"): return ""
    try:
        r = _s.get("https://textures.minecraft.net" + p.path)
    except requests.RequestException:
        return ""
    if r.status_code != 200 or len(r.content) > 200_000 or not r.content.startswith(b"\x89PNG\r\n\x1a\n"): return ""
    return "data:image/png;base64," + base64.b64encode(r.content).decode()


def perfil_cosmeticos():
    """Skin activa y capas del perfil: {nombre, variante, skin(data URI), capas:[{id,alias,activa,img}]}."""
    r = _llamar("GET", API_MC)
    j = _json(r)
    if r.status_code != 200 or not isinstance(j, dict) or "skins" not in j:
        raise ErrorCuenta("No pude leer tu perfil de Minecraft.")
    sk = next((x for x in j.get("skins", []) if isinstance(x, dict) and x.get("state") == "ACTIVE"), None)
    capas = []
    for c in j.get("capes", []):
        if isinstance(c, dict) and isinstance(c.get("id"), str) and _ID_CAPA.fullmatch(c["id"]):
            capas.append({"id": c["id"], "alias": str(c.get("alias", "Capa"))[:40], "activa": c.get("state") == "ACTIVE", "img": _textura(c.get("url"))})
    return {"nombre": str(j.get("name", ""))[:16], "variante": "slim" if sk and str(sk.get("variant", "")).upper() == "SLIM" else "classic",
            "skin": _textura(sk.get("url")) if sk else "", "capas": capas}


def validar_skin(datos):
    """PNG de 64x64 (o 64x32 antigua) y hasta 24 KB. Lanza ErrorCuenta con un mensaje claro."""
    if not datos.startswith(b"\x89PNG\r\n\x1a\n") or len(datos) < 33 or datos[12:16] != b"IHDR":
        raise ErrorCuenta("Ese archivo no es una imagen PNG.")
    if len(datos) > MAX_SKIN: raise ErrorCuenta("La skin pesa demasiado (máximo 24 KB).")
    w, h = struct.unpack(">II", datos[16:24])
    if (w, h) not in ((64, 64), (64, 32)): raise ErrorCuenta(f"La skin debe medir 64×64 (o 64×32) píxeles; esta mide {w}×{h}.")


def cambiar_skin(ruta, variante):
    if variante not in ("classic", "slim"): raise ErrorCuenta("Modelo de skin no válido.")
    try:
        with open(ruta, "rb") as f: datos = f.read(MAX_SKIN + 1)
    except OSError:
        raise ErrorCuenta("No pude leer ese archivo.")
    validar_skin(datos)
    r = _llamar("POST", API_MC + "/skins", data={"variant": variante}, files={"file": ("skin.png", datos, "image/png")})
    if r.status_code >= 400:
        raise ErrorCuenta("Mojang rechazó la skin" + (": " + str(_json(r).get("errorMessage", ""))[:150] if r.headers.get("content-type", "").startswith("application/json") else "."))
    return perfil_cosmeticos()


def activar_capa(id_capa):
    """id_capa=None quita la capa."""
    if id_capa is None: r = _llamar("DELETE", API_MC + "/capes/active")
    elif isinstance(id_capa, str) and _ID_CAPA.fullmatch(id_capa): r = _llamar("PUT", API_MC + "/capes/active", json={"capeId": id_capa})
    else: raise ErrorCuenta("Capa no válida.")
    if r.status_code >= 400: raise ErrorCuenta("Mojang no aceptó el cambio de capa.")
    return perfil_cosmeticos()