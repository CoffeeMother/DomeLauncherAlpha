"""
Noticias oficiales de Minecraft: Java (notas de versión y snapshots) desde el servidor de contenido
del launcher oficial de Mojang (launchercontent.mojang.com).

- Se guarda una copia en disco: si no hay internet se muestra la última que se bajó.
- El texto de cada nota viene en HTML: se limpia con una lista de etiquetas permitidas antes de
  llegar a la interfaz (sin scripts, sin atributos raros, enlaces e imágenes solo de dominios oficiales).
"""
import os, re, json, time, html, urllib.parse
from html.parser import HTMLParser
import requests
import nucleo as nu

BASE_URL = "https://launchercontent.mojang.com"
RUTA_CACHE = os.path.join(nu.BASE, "novedades_cache.json")
CADUCA = 30 * 60                       # segundos que vale la copia antes de volver a preguntar
HOSTS_ENLACE = ("www.minecraft.net", "minecraft.net", "feedback.minecraft.net", "help.minecraft.net",
                "bugs.mojang.com", "launchercontent.mojang.com")
_ID = re.compile(r"[\w.\-]{1,120}")
_IMG = re.compile(r"/v2/images/[\w.\-%]{1,200}")
_CONTENIDO = re.compile(r"javaPatchNotes/[\w.\-]{1,200}\.json")
_s = requests.Session()
_s.headers["User-Agent"] = "DomeLauncher/2.0 (launcher personal de Minecraft)"
_articulos = {}                         # id -> html limpio


class ErrorNoticias(Exception):
    pass


def _bajar(ruta):
    try:
        r = _s.get(BASE_URL + ruta, timeout=12)
        if r.status_code >= 400: raise ErrorNoticias(f"Mojang respondió con error {r.status_code}.")
        if len(r.content) > 6_000_000: raise ErrorNoticias("La respuesta es demasiado grande.")
        return r.json()
    except requests.RequestException:
        raise ErrorNoticias("No pude conectar con los servidores de Mojang (¿sin internet?).")
    except ValueError:
        raise ErrorNoticias("Respuesta no válida de Mojang.")


def _indice():
    """(entradas crudas, sin_conexion). Usa la copia si es reciente; si falla la red, la copia vieja."""
    c = nu.leer(RUTA_CACHE, {})
    ent = c.get("entries") if isinstance(c, dict) else None
    if ent and time.time() - float(c.get("t", 0)) < CADUCA: return ent, False
    try:
        datos = _bajar("/v2/javaPatchNotes.json")
        ent = datos.get("entries") if isinstance(datos, dict) else None
        if not isinstance(ent, list): raise ErrorNoticias("Respuesta no válida de Mojang.")
        ent = ent[:30]
        try:
            os.makedirs(nu.BASE, exist_ok=True)
            with open(RUTA_CACHE, "w", encoding="utf-8") as f: json.dump({"t": time.time(), "entries": ent}, f)
        except OSError:
            pass
        return ent, False
    except ErrorNoticias:
        if ent: return ent, True
        raise


def _imagen(e):
    u = (e.get("image") or {}).get("url") if isinstance(e.get("image"), dict) else None
    return BASE_URL + u if isinstance(u, str) and _IMG.fullmatch(u) else ""


def lista(maximo=12):
    ent, offline = _indice()
    res = []
    for e in sorted((x for x in ent if isinstance(x, dict)), key=lambda x: str(x.get("date", "")), reverse=True):
        if not (isinstance(e.get("id"), str) and _ID.fullmatch(e["id"])): continue
        res.append({"id": e["id"], "titulo": str(e.get("title", ""))[:120], "tipo": "snapshot" if e.get("type") == "snapshot" else "release",
                    "version": str(e.get("version", ""))[:40], "fecha": str(e.get("date", ""))[:10],
                    "resumen": " ".join(html.unescape(re.sub(r"<[^>]*>", " ", str(e.get("shortText", "")))).split())[:180],
                    "imagen": _imagen(e)})
        if len(res) >= maximo: break
    return {"entradas": res, "sin_conexion": offline}


def articulo(id_):
    if not (isinstance(id_, str) and _ID.fullmatch(id_)): raise ErrorNoticias("Noticia no válida.")
    if id_ in _articulos: return _articulos[id_]
    ent, _ = _indice()
    e = next((x for x in ent if isinstance(x, dict) and x.get("id") == id_), None)
    ruta = e.get("contentPath") if e else None
    if not (isinstance(ruta, str) and _CONTENIDO.fullmatch(ruta)): raise ErrorNoticias("No encontré esa noticia.")
    cuerpo = _bajar("/v2/" + ruta).get("body", "")
    res = {"titulo": str(e.get("title", ""))[:120], "fecha": str(e.get("date", ""))[:10], "imagen": _imagen(e),
           "html": limpiar_html(str(cuerpo)[:400_000])}
    _articulos[id_] = res
    return res


# ----------------------------------------------------------------- HTML seguro
_PERMITIDAS = {"p", "h1", "h2", "h3", "h4", "ul", "ol", "li", "strong", "b", "em", "i", "code", "pre", "br", "hr", "a", "img", "blockquote", "span"}
_VACIAS = {"br", "hr", "img"}
_IGNORAR = {"script", "style", "iframe", "object", "embed", "svg", "math", "form", "template", "noscript"}


def url_enlace_valida(u):
    p = urllib.parse.urlparse(u or "")
    return p.scheme == "https" and p.hostname in HOSTS_ENLACE


class _Limpio(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.pila, self.ignorando = [], [], 0

    def handle_starttag(self, tag, attrs):
        if tag in _IGNORAR: self.ignorando += 1; return
        if self.ignorando or tag not in _PERMITIDAS: return
        a = dict(attrs)
        if tag == "a":
            href = urllib.parse.urljoin("https://www.minecraft.net/", a.get("href") or "")
            ok = url_enlace_valida(href)
            self.out.append(f'<a href="{html.escape(href, True)}" data-ext="1">' if ok else "<span>")
            self.pila.append(("a", "</a>" if ok else "</span>")); return
        if tag == "img":
            src = urllib.parse.urljoin(BASE_URL + "/", a.get("src") or "")
            p = urllib.parse.urlparse(src)
            if p.scheme == "https" and p.hostname == "launchercontent.mojang.com":
                self.out.append(f'<img src="{html.escape(src, True)}" alt="{html.escape((a.get("alt") or "")[:120], True)}" loading="lazy" referrerpolicy="no-referrer">')
            return
        self.out.append(f"<{tag}>")
        if tag not in _VACIAS: self.pila.append((tag, f"</{tag}>"))

    def handle_endtag(self, tag):
        if tag in _IGNORAR: self.ignorando = max(0, self.ignorando - 1); return
        if self.ignorando or tag not in _PERMITIDAS or tag in _VACIAS: return
        if any(t == tag for t, _ in self.pila):   # cierra también lo que quedara abierto dentro
            while self.pila:
                t, cierre = self.pila.pop(); self.out.append(cierre)
                if t == tag: break

    def handle_data(self, data):
        if not self.ignorando: self.out.append(html.escape(data))

    def cerrar(self):
        while self.pila: self.out.append(self.pila.pop()[1])
        return "".join(self.out)


def limpiar_html(texto):
    p = _Limpio()
    p.feed(texto); p.close()
    return p.cerrar()
