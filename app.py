import os, sys, json, time, threading, subprocess
import webview
import nucleo as nu
import modrinth as mr
import modpack as mp
import contenido as ct
import curseforge as cf
import tema as tm
import iconos as ic
import novedades as nv
import cuenta as cu
import logos as lg
import rendimiento as rd
import skins as sk
import webbrowser

if getattr(sys, 'frozen', False):
    os.chdir(os.path.dirname(os.path.abspath(sys.executable)))
else:
    os.chdir(os.path.dirname(os.path.abspath(__file__)))


def recurso_path(relativa):
    """Ruta de un recurso empaquetado (web/, logo.ico...). En un .exe de un solo archivo PyInstaller
    los extrae a una carpeta temporal (sys._MEIPASS), no junto al .exe."""
    if getattr(sys, 'frozen', False):
        base = getattr(sys, '_MEIPASS', None) or os.path.dirname(os.path.abspath(sys.executable))
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, relativa)


class Api:
    def __init__(self):
        self._win = None   # con '_' para que pywebview no recorra la ventana (causaba 'maximum recursion depth')
        self.jugando = False
        self.instalando_pack = False
        self.premium = False        # True: jugar con la cuenta de Microsoft guardada; False: sin conexión
        self.login_activo = False


    def estado(self):
        inst = nu.leer(nu.RUTA_INST, {})
        cambio = False
        for d in inst.values():
            cambio |= nu.reparar_version_id(d)       # arregla instancias Forge viejas
        if cambio: nu.guardar_inst(inst)
        cfg = {k: v for k, v in nu.leer(nu.RUTA_CFG, {}).items() if k != "curseforge_key"}    # la clave de API no sale del backend
        return {"instancias": [{"nombre": n, "mods": nu.contar_mods(d), **d, "logo_uri": lg.uri(d.get("logo"))} for n, d in inst.items()], "config": cfg}


    def cuenta_estado(self):
        return cu.estado()

    def cuenta_modo(self, premium):
       
        self.premium = bool(premium) and cu.estado()["cuenta"] is not None
        return {"premium": self.premium}

    def cuenta_login(self):
        if self.login_activo: return {"ok": False, "error": "Ya hay una ventana de inicio de sesión abierta."}
        try: datos = cu.iniciar()
        except cu.ErrorCuenta as e: return {"ok": False, "error": str(e)}
        self.login_activo = True
        threading.Thread(target=self._hilo_login, args=datos, daemon=True).start()
        return {"ok": True}

    def _hilo_login(self, url, state, verifier):
        cerrada, final = threading.Event(), [None]
        try:
            w = webview.create_window("Iniciar sesión con Microsoft", url, width=520, height=700, resizable=True)
            w.events.closed += cerrada.set
            limite = time.time() + 300
            while not cerrada.is_set() and time.time() < limite:
                try: u = w.get_current_url()
                except Exception: u = None
                if u and cu.es_redireccion(u): final[0] = u; break
                time.sleep(0.3)
            try: w.destroy()
            except Exception: pass
            if not final[0]:
                return self._js("onCuentaLogin", {"ok": False, "cancelado": True, "error": "Cerraste la ventana sin terminar el inicio de sesión."})
            self._js("onCuentaLogin", {"ok": True, "estado": cu.completar(final[0], state, verifier)})
        except cu.ErrorCuenta as e:
            self._js("onCuentaLogin", {"ok": False, "error": str(e)})
        except Exception as e:
            self._js("onCuentaLogin", {"ok": False, "error": f"Error inesperado: {e}"})
        finally:
            self.login_activo = False

    def cuenta_cerrar(self):
        cu.cerrar_sesion(); self.premium = False
        return cu.estado()

    def _cosm(self, fn, *a):
        try: return {"ok": True, "datos": fn(*a)}
        except cu.ErrorCuenta as e:
            if e.caducada: self.premium = False; self._js("onCuentaCerrada", cu.estado())
            return {"ok": False, "error": str(e)}
        except Exception as e: return {"ok": False, "error": f"Error inesperado: {e}"}

    def skin_estado(self):
        if not self.premium: return {"ok": False, "error": "Inicia sesión con Microsoft para cambiar tu skin y tu capa."}
        return self._cosm(cu.perfil_cosmeticos)

    def skin_subir(self, variante):
        if not self.premium: return {"ok": False, "error": "Inicia sesión con Microsoft para cambiar tu skin."}
        tipo = webview.FileDialog.OPEN if hasattr(webview, "FileDialog") else webview.OPEN_DIALOG
        rutas = self._win.create_file_dialog(tipo, allow_multiple=False, file_types=("Skin PNG (*.png)",))
        if not rutas: return {"ok": False, "cancelado": True}
        return self._cosm(cu.cambiar_skin, rutas[0], variante)

    def capa_activar(self, id_capa):
        if not self.premium: return {"ok": False, "error": "Inicia sesión con Microsoft para cambiar tu capa."}
        return self._cosm(cu.activar_capa, id_capa)

    
    def _skin_op(self, fn, *a):
      
        try: return {"ok": True, "datos": fn(*a)}
        except (cu.ErrorCuenta, sk.ErrorSkins) as e:
            if getattr(e, "caducada", False): self.premium = False; self._js("onCuentaCerrada", cu.estado())
            return {"ok": False, "error": str(e)}
        except Exception as e: return {"ok": False, "error": f"Error inesperado: {e}"}

    def _skins_todo(self, perfil=None):
        perfil = perfil or cu.perfil_cosmeticos()
        sk.importar_actual(perfil)            # la primera vez guarda la skin que ya llevas puesta
        return {"perfil": perfil, "lib": sk.listar()}

    def skins_estado(self):
        if not self.premium: return {"ok": False, "error": "Inicia sesión con Microsoft para cambiar tu skin y tu capa."}
        return self._skin_op(self._skins_todo)

    def skin_nueva_elegir(self):
      
        if not self.premium: return {"ok": False, "error": "Inicia sesión con Microsoft para cambiar tu skin."}
        tipo = webview.FileDialog.OPEN if hasattr(webview, "FileDialog") else webview.OPEN_DIALOG
        rutas = self._win.create_file_dialog(tipo, allow_multiple=False, file_types=("Skin PNG (*.png)",))
        if not rutas: return {"ok": False, "cancelado": True}
        try:
            datos, uri = sk.leer_png(rutas[0])
        except sk.ErrorSkins as e: return {"ok": False, "error": str(e)}
        self._skin_pend = datos
        return {"ok": True, "datos": {"uri": uri, "nombre": os.path.splitext(os.path.basename(rutas[0]))[0][:24]}}

    def skin_nueva_guardar(self, nombre, variante, aplicar):
        datos = getattr(self, "_skin_pend", None)
        if not datos: return {"ok": False, "error": "Elige primero un archivo de skin."}
        def hacer():
            sid = sk.agregar_datos(datos, nombre, variante)
            self._skin_pend = None
            return self._skins_todo(sk.aplicar(sid) if aplicar else None)
        return self._skin_op(hacer)

    def skin_aplicar(self, sid):
        if not self.premium: return {"ok": False, "error": "Inicia sesión con Microsoft para cambiar tu skin."}
        return self._skin_op(lambda: self._skins_todo(sk.aplicar(sid)))

    def skin_renombrar(self, sid, nombre): return self._skin_op(lambda: {"lib": sk.renombrar(sid, nombre)})
    def skin_borrar(self, sid): return self._skin_op(lambda: {"lib": sk.borrar(sid)})


    def rendimiento_estado(self):
        return {"cfg": rd.leer(), "ram_total": rd.ram_total_gb(), "hilos_max": rd.HILOS_MAX,
                "gpu_auto": os.name == "nt" or sys.platform.startswith("linux")}

    def rendimiento_guardar(self, cfg):
        try: return {"ok": True, "cfg": rd.guardar(cfg)}
        except rd.ErrorRendimiento as e: return {"ok": False, "error": str(e)}
        except Exception as e: return {"ok": False, "error": f"Error inesperado: {e}"}

    def instancia_ram(self, nombre, gb):
        inst = nu.leer(nu.RUTA_INST, {})
        if nombre not in inst: return {"ok": False, "error": "Instancia no encontrada"}
        try: inst[nombre]["ram"] = rd.ram_texto(gb)
        except rd.ErrorRendimiento as e: return {"ok": False, "error": str(e)}
        nu.guardar_inst(inst)
        return {"ok": True, "ram": inst[nombre]["ram"]}


    def tema_obtener(self):
        return {"tema": tm.leer_tema(), "animaciones": nu.leer(nu.RUTA_CFG, {}).get("animaciones", True) is not False}

    def tema_guardar(self, t):
        try: return {"ok": True, "tema": tm.guardar_tema(t)}
        except Exception as e: return {"ok": False, "error": f"No se pudo guardar el tema: {e}"}

    def config_guardar(self, clave, valor):
        try: tm.guardar_clave(clave, valor); return {"ok": True}
        except Exception as e: return {"ok": False, "error": str(e)}

    def fondo_datos(self):
        return tm.datos_fondo(tm.leer_tema()["fondo"]["archivo"])

    def fondo_elegir(self):
        tipo = webview.FileDialog.OPEN if hasattr(webview, "FileDialog") else webview.OPEN_DIALOG
        rutas = self._win.create_file_dialog(tipo, allow_multiple=False,
                                             file_types=("Imágenes y GIF (*.png;*.jpg;*.jpeg;*.webp;*.gif)",))
        if not rutas: return {"ok": False, "cancelado": True}
        try:
            t = tm.elegir_fondo(rutas[0])
            return {"ok": True, "tema": t, "datos": tm.datos_fondo(t["fondo"]["archivo"])}
        except tm.ErrorTema as e:
            return {"ok": False, "error": str(e)}
        except Exception as e:
            return {"ok": False, "error": f"Error inesperado: {e}"}


    def novedades(self):
        try: return {"ok": True, "datos": nv.lista()}
        except nv.ErrorNoticias as e: return {"ok": False, "error": str(e)}
        except Exception as e: return {"ok": False, "error": f"Error inesperado: {e}"}

    def novedad_articulo(self, id_):
        try: return {"ok": True, "datos": nv.articulo(id_)}
        except nv.ErrorNoticias as e: return {"ok": False, "error": str(e)}
        except Exception as e: return {"ok": False, "error": f"Error inesperado: {e}"}

    def abrir_enlace(self, url):
        """Abre en el navegador solo enlaces https de los sitios oficiales de Minecraft/Mojang y de CurseForge."""
        if nv.url_enlace_valida(url) or cf.url_web_valida(url): webbrowser.open(url)

  
    def iconos_listar(self): return {"ok": True, "datos": ic.listar()}

    def icono_anadir(self):
        tipo = webview.FileDialog.OPEN if hasattr(webview, "FileDialog") else webview.OPEN_DIALOG
        rutas = self._win.create_file_dialog(tipo, allow_multiple=False,
                                             file_types=("Imágenes (*.png;*.jpg;*.jpeg;*.webp;*.gif)",))
        if not rutas: return {"ok": False, "cancelado": True}
        try: return {"ok": True, "datos": ic.importar(rutas[0]), "lista": ic.listar()}
        except ic.ErrorIcono as e: return {"ok": False, "error": str(e)}
        except Exception as e: return {"ok": False, "error": f"Error inesperado: {e}"}

    def instancia_icono(self, nombre, icono):
        inst = nu.leer(nu.RUTA_INST, {})
        if nombre not in inst: return {"ok": False, "error": "Instancia no encontrada"}
        if not ic.valido(icono): return {"ok": False, "error": "Ese icono no existe."}
        inst[nombre]["icono"] = icono
        inst[nombre].pop("logo", None)          # elegir un icono sustituye al logo del modpack
        nu.guardar_inst(inst)
        lg.limpiar(inst)
        return {"ok": True}

    def logos_completar(self):
        """Baja en segundo plano el logo de los modpacks ya instalados que todavía no lo tienen."""
        if getattr(self, "_logos_activo", False): return {"ok": True}
        self._logos_activo = True
        threading.Thread(target=self._hilo_logos, daemon=True).start()
        return {"ok": True}

    def _hilo_logos(self):
        try:
            cambio = False
            pendientes = [(n, d["pack"]) for n, d in nu.leer(nu.RUTA_INST, {}).items()
                          if isinstance(d.get("pack"), dict) and not d.get("logo") and not d.get("logo_sin")]
            for nombre, o in pendientes:
                try: logo = lg.de_proyecto(o.get("fuente"), o.get("proyecto"))
                except Exception: continue
                inst = nu.leer(nu.RUTA_INST, {})
                if nombre not in inst or inst[nombre].get("logo"): continue
                if logo: inst[nombre]["logo"] = logo
                else: inst[nombre]["logo_sin"] = True        # ese proyecto no tiene logo: no volver a intentarlo
                nu.guardar_inst(inst); cambio = True
            if cambio: self._js("onLogos", True)
        finally:
            self._logos_activo = False


    def minimizar(self): self._win.minimize()
    def maximizar(self):
        """Alterna maximizada/restaurada y devuelve el estado nuevo."""
        try:
            if getattr(self, "_maximizada", False): self._win.restore(); self._maximizada = False
            else: self._win.maximize(); self._maximizada = True
        except Exception as e:
            print(f"Aviso: no se pudo maximizar ({e}).")
        return {"maximizada": getattr(self, "_maximizada", False)}
    def cerrar(self): self._win.destroy()

    def abrir_carpeta(self, nombre, sub=""):
        d = nu.leer(nu.RUTA_INST, {}).get(nombre)
        if d: nu.abrir_carpeta(os.path.join(nu.preparar_carpeta(d), sub))

    def mods(self, nombre):
        d = nu.leer(nu.RUTA_INST, {}).get(nombre)
        if not d: return {"error": "Instancia no encontrada"}
        return {"loader": nu.loader_de(d), "tipo": d.get("tipo", "Vanilla"), "mods": nu.listar_mods(d)}

    def _accion_mod(self, nombre, fn, *args):
        d = nu.leer(nu.RUTA_INST, {}).get(nombre)
        if not d: return {"ok": False, "error": "Instancia no encontrada"}
        try:
            fn(d, *args); return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def mod_activar(self, nombre, archivo, activo): return self._accion_mod(nombre, nu.activar_mod, archivo, bool(activo))
    def mod_borrar(self, nombre, archivo): return self._accion_mod(nombre, nu.borrar_mod, archivo)

    def mod_anadir_rutas(self, nombre, rutas):
        d = nu.leer(nu.RUTA_INST, {}).get(nombre)
        if not d: return {"ok": False, "error": "Instancia no encontrada"}
        copiados, omitidos = nu.anadir_mods(d, rutas)
        return {"ok": True, "copiados": copiados, "omitidos": omitidos}

    def mod_anadir_dialogo(self, nombre):
        tipo = webview.FileDialog.OPEN if hasattr(webview, "FileDialog") else webview.OPEN_DIALOG
        rutas = self._win.create_file_dialog(tipo, allow_multiple=True, file_types=("Mods de Minecraft (*.jar)",))
        return self.mod_anadir_rutas(nombre, list(rutas or []))

   
    def _modrinth(self, nombre, fn, *args):
        d = nu.leer(nu.RUTA_INST, {}).get(nombre)
        if not d: return {"ok": False, "error": "Instancia no encontrada"}
        try:
            return {"ok": True, "datos": fn(d, *args)}
        except mr.ErrorModrinth as e:
            return {"ok": False, "error": str(e)}
        except Exception as e:
            return {"ok": False, "error": f"Error inesperado: {e}"}

    def modrinth_buscar(self, nombre, texto, pagina=0, orden="relevance"):
        return self._modrinth(nombre, mr.buscar, texto, pagina, orden)

    def modrinth_instalar(self, nombre, project_id, titulo=""):
        return self._modrinth(nombre, mr.instalar, project_id, titulo)

    def modrinth_actualizaciones(self, nombre):
        return self._modrinth(nombre, mr.buscar_actualizaciones)

    def modrinth_actualizar(self, nombre, archivo, version_id):
        return self._modrinth(nombre, mr.actualizar, archivo, version_id)

    
    def _simple(self, fn, *args):
        try: return {"ok": True, "datos": fn(*args)}
        except mr.ErrorModrinth as e: return {"ok": False, "error": str(e)}
        except Exception as e: return {"ok": False, "error": f"Error inesperado: {e}"}

    def contenido_buscar(self, tipo, texto, pagina=0, orden="downloads", version="", loaders=None, categorias=None, instancia="", fuente="modrinth"):
        d = nu.leer(nu.RUTA_INST, {}).get(instancia) if instancia else None
        return self._simple(ct.buscar, tipo, texto, pagina, orden, version, loaders, categorias, d, fuente)

    def contenido_categorias(self, tipo, fuente="modrinth"): return self._simple(ct.categorias, tipo, fuente)
    def curseforge_estado(self): return {"clave": cf.hay_clave()}
    def contenido_versiones_juego(self): return self._simple(ct.versiones_juego)
    def contenido_mundos(self, nombre): return self._modrinth(nombre, ct.mundos)

    def contenido_instalar(self, nombre, tipo, project_id, titulo="", mundo="", fuente="modrinth"):
        return self._modrinth(nombre, ct.instalar, tipo, project_id, titulo, mundo, fuente)

    def modpack_versiones(self, project_id, fuente="modrinth"):
        return self._simple(mp.versiones_de, fuente, project_id)

    def modpack_instalar(self, version_id, nombre="", ram="6", fuente="modrinth", proyecto=""):
        nombre = str(nombre or "").strip() or None
        try: ram = rd.ram_texto(ram)
        except rd.ErrorRendimiento as e: return {"ok": False, "error": str(e)}
        if fuente == "curseforge":
            return self._iniciar_pack(lambda cb: mp.instalar_desde_curseforge(proyecto, version_id, cb, nombre, ram))
        return self._iniciar_pack(lambda cb: mp.instalar_desde_modrinth(version_id, cb, nombre, ram))

    def modpack_importar_dialogo(self):
        tipo = webview.FileDialog.OPEN if hasattr(webview, "FileDialog") else webview.OPEN_DIALOG
        rutas = self._win.create_file_dialog(tipo, allow_multiple=False, file_types=("Modpacks (*.mrpack;*.zip)",))
        if not rutas: return {"ok": False, "cancelado": True}
        return self._iniciar_pack(lambda cb: mp.instalar_archivo(rutas[0], cb))

    
    def modpack_actualizacion(self, nombre): return self._simple(mp.comprobar_actualizacion, nombre)

    def modpack_actualizar(self, nombre, version_id):
        return self._iniciar_pack(lambda cb: mp.actualizar(nombre, str(version_id), cb), "onPackActualizado")

    def _iniciar_pack(self, tarea, evento="onPackListo"):
        if self.instalando_pack or self.jugando: return {"ok": False, "error": "Ya hay una instalación o el juego está abierto."}
        self.instalando_pack = True
        threading.Thread(target=self._hilo_pack, args=(tarea, evento), daemon=True).start()
        return {"ok": True}

    def _hilo_pack(self, tarea, evento="onPackListo"):
        rd.aplicar_hilos()
        prog, ultimo = {"estado": "Preparando…", "valor": 0, "max": 0}, [0.0]
        def push(forzar=False):
            if forzar or time.time() - ultimo[0] > 0.1:
                ultimo[0] = time.time(); self._js("onProgresoPack", prog)
        cb = {"setStatus": lambda t: (prog.update(estado=t), push(True)),
              "setProgress": lambda v: (prog.update(valor=v), push()),
              "setMax": lambda m: (prog.update(valor=0, max=m), push(True))}
        try:
            self._js(evento, tarea(cb))
        except mr.ErrorModrinth as e:
            self._js("onPackError", str(e))
        except Exception as e:
            self._js("onPackError", f"Error inesperado: {e}")
        finally:
            self.instalando_pack = False


    def jugar(self, nombre, usuario):
        if self.jugando: return
        datos = nu.leer(nu.RUTA_INST, {}).get(nombre)
        if not datos: return self._js("onLog", "❌ Instancia no encontrada")
        self.jugando = True
        threading.Thread(target=self._hilo, args=(datos, usuario), daemon=True).start()

    def _hilo(self, d, usuario):
        self._js("onEstadoJuego", True)
        try:
            sesion = None
            if self.premium:
                self._js("onLog", "» Comprobando tu sesión de Microsoft...")
                sesion = cu.sesion_para_jugar()
                usuario = sesion["name"]
            self._js("onLog", f"» Lanzando {d['version_id']} como '{usuario}'" + (" (cuenta premium)..." if sesion else " (sin conexión)..."))
            cmd, juego = nu.comando_jugar(d, usuario, nu.leer(nu.RUTA_CFG, {}), sesion, rd.opciones_jvm())
            entorno = os.environ.copy()
            rd.preparar_gpu(cmd[0], entorno)
            self._js("onLog", f"» Carpeta de juego: {juego}")
            self._js("onLog", f"» Java: {cmd[0]}")
            p = subprocess.Popen(cmd, cwd=juego, env=entorno, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                 encoding="utf-8", errors="replace", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            for linea in p.stdout: self._js("onLog", linea.rstrip("\n"))
            codigo = p.wait()
            self._js("onLog", f"» Proceso finalizado (código {codigo})")
            if codigo != 0: self._js("onCrash", {"version": d["version_id"], "texto": self._ultimo_crash(juego)})
        except cu.ErrorCuenta as e:
            self._js("onLog", f"❌ Cuenta: {e}")
            if e.caducada: self.premium = False; self._js("onCuentaCerrada", cu.estado())
            self._js("onCrash", {"version": d["version_id"], "texto": f"No se pudo iniciar sesión con tu cuenta de Microsoft:\n\n{e}\n\nPuedes jugar sin conexión mientras tanto."})
        except Exception as e:
            self._js("onLog", f"❌ ERROR: {e}")
            self._js("onCrash", {"version": d["version_id"], "texto": f"No se pudo lanzar Minecraft:\n\n{e}"})
        finally:
            self.jugando = False
            self._js("onEstadoJuego", False)

   
    def versiones(self, tipo="Vanilla"):
        try:
            return nu.versiones(tipo)
        except Exception as e:
            self._js("onLog", f"❌ No se pudo obtener la lista de versiones: {e}")
            return []


    def crear(self, d):
        nombre = d["nombre"].strip()
        if not nombre or nombre in nu.leer(nu.RUTA_INST, {}):
            return self._js("onErrorInstalar", "Elige un nombre que no esté en uso.")
        try: d["ram"] = rd.ram_texto(d.get("ram", rd.leer()["ram"]))
        except rd.ErrorRendimiento as e: return self._js("onErrorInstalar", str(e))
        threading.Thread(target=self._instalar, args=(nombre, d), daemon=True).start()

    def _instalar(self, nombre, d):
        rd.aplicar_hilos()
        prog, ultimo = {"estado": "Preparando…", "valor": 0, "max": 0}, [0.0]
        def push(forzar=False):
            if forzar or time.time() - ultimo[0] > 0.1:
                ultimo[0] = time.time(); self._js("onProgreso", prog)
        cb = {"setStatus": lambda t: (prog.update(estado=t), push(True)),
              "setProgress": lambda v: (prog.update(valor=v), push()),
              "setMax": lambda m: (prog.update(max=m), push(True))}
        try:
            ver = d["version"]
            v_id = nu.instalar(d["tipo"], ver, cb)
            inst = nu.leer(nu.RUTA_INST, {})
            nuevo = {"tipo": d["tipo"], "version_id": v_id, "version_target": ver, "icono": d.get("icono") if ic.valido(d.get("icono")) else ic.DEFECTO,
                     "loader": nu.id_loader(d["tipo"]), "carpeta": nu.slug_unico(nombre, inst), "ram": d.get("ram", "4"), "java_executable": "java",
                     "custom_jvm_args": "", "resolution_w": "854", "resolution_h": "480"}
            nu.preparar_carpeta(nuevo)               # crea mods/, config/, saves/... de la instancia
            inst[nombre] = nuevo
            nu.guardar_inst(inst)
            self._js("onInstalada", nombre)
        except Exception as e:
            self._js("onErrorInstalar", str(e))

    def eliminar(self, nombre):
        nu.eliminar(nombre)
        lg.limpiar(nu.leer(nu.RUTA_INST, {}))

    def _ultimo_crash(self, juego):
        carpeta = os.path.join(juego, "crash-reports")
        try:
            archivos = [os.path.join(carpeta, f) for f in os.listdir(carpeta) if f.endswith(".txt")]
            with open(max(archivos, key=os.path.getmtime), encoding="utf-8", errors="replace") as f: return f.read()
        except Exception:
            return "El juego se cerró con un error, pero Minecraft no generó un crash-report.\nRevisa el registro."

    def _js(self, fn, valor):
        try: self._win.evaluate_js(f"window.{fn}({json.dumps(valor)})")
        except Exception: pass


def _enlazar_arrastrar_soltar(api):
    """Permite soltar .jar sobre la ventana (pywebview entrega la ruta real del archivo)."""
    try:
        from webview.dom import DOMEventHandler
        def nada(e): pass
        def soltar(e):
            archivos = (e.get("dataTransfer") or {}).get("files") or []
            rutas = [f.get("pywebviewFullPath") for f in archivos if f.get("pywebviewFullPath")]
            if rutas: api._js("onArchivosSoltados", rutas)
        ev = api._win.dom.document.events
        ev.dragenter += DOMEventHandler(nada, True, True)
        ev.dragstart += DOMEventHandler(nada, True, True)
        ev.dragover += DOMEventHandler(nada, True, True)
        ev.drop += DOMEventHandler(soltar, True, True)
    except Exception as e:
        print(f"Aviso: no se pudo activar arrastrar y soltar ({e}). El botón 'Añadir .jar' sigue funcionando.")


ICO, PNG = recurso_path("logo.ico"), recurso_path("logo.png")     # cambia estos dos archivos por tu logo
TITULO = "Dome Launcher"


def _id_windows():
    """Windows agrupa la ventana bajo python.exe (con su icono) salvo que el programa tenga identidad propia."""
    if sys.platform.startswith("win"):
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("DomeLauncher.App")
        except Exception:
            pass


def _icono_windows():
    """Pone el logo en la ventana y en la barra de tareas (WM_SETICON)."""
    import ctypes
    u = ctypes.windll.user32
    u.LoadImageW.restype = ctypes.c_void_p
    u.FindWindowW.restype = ctypes.c_void_p
    hwnd = None
    for _ in range(50):                                   # la ventana tarda un instante en existir
        hwnd = u.FindWindowW(None, TITULO)
        if hwnd: break
        time.sleep(0.1)
    if not hwnd or not os.path.isfile(ICO): return
    for tam, cual in ((u.GetSystemMetrics(11), 1), (u.GetSystemMetrics(49), 0)):   # grande (barra de tareas) y pequeño (título)
        h = u.LoadImageW(None, ICO, 1, tam, tam, 0x10)    # IMAGE_ICON, LR_LOADFROMFILE
        if h: u.SendMessageW(ctypes.c_void_p(hwnd), 0x80, cual, ctypes.c_void_p(h))


def _icono_mac():
    """Icono del Dock en macOS (pywebview ya trae pyobjc)."""
    from AppKit import NSApplication, NSImage
    img = NSImage.alloc().initWithContentsOfFile_(PNG)
    if img: NSApplication.sharedApplication().setApplicationIconImage_(img)


def _al_iniciar(api):
    try:
        if sys.platform.startswith("win"): _icono_windows()
        elif sys.platform == "darwin": _icono_mac()
    except Exception as e:
        print(f"Aviso: no se pudo poner el logo ({e}).")
    _enlazar_arrastrar_soltar(api)


if __name__ == "__main__":
    _id_windows()
    api = Api()
    # Usamos recurso_path para que encuentre la carpeta web tanto en local como en el .exe compilado
    api._win = webview.create_window(TITULO, recurso_path(os.path.join("web", "index.html")),
                                    js_api=api, width=1180, height=700, frameless=True, background_color=tm.color_ventana())
    import inspect
    icono = ICO if sys.platform.startswith("win") else PNG        # Windows exige .ico; Linux/macOS aceptan .png
    extra = {"icon": icono} if "icon" in inspect.signature(webview.start).parameters and os.path.isfile(icono) else {}
    webview.start(_al_iniciar, api, **extra)