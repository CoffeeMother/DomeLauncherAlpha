
import os, sys, shutil, threading
import nucleo as nu
import tema as tm

DEFECTO = {"hilos": 8, "jvm": True, "gpu": True, "ram": 4.0}
MIN_GB, HILOS_MAX = 0.5, 32
_lock = threading.Lock()
_orig = {}


class ErrorRendimiento(Exception):
    pass


# ------------------------------------------------------------------ RAM
def ram_total_gb():
    """RAM física del equipo en GB (0 si no se pudo averiguar)."""
    try:
        if sys.platform.startswith("win"):
            import ctypes
            class M(ctypes.Structure):
                _fields_ = [("l", ctypes.c_ulong), ("u", ctypes.c_ulong), ("total", ctypes.c_ulonglong), ("a", ctypes.c_ulonglong),
                            ("p", ctypes.c_ulonglong), ("b", ctypes.c_ulonglong), ("v", ctypes.c_ulonglong), ("w", ctypes.c_ulonglong), ("x", ctypes.c_ulonglong)]
            m = M(); m.l = ctypes.sizeof(M)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)): return round(m.total / 1024 ** 3, 1)
        elif sys.platform == "darwin":
            import subprocess
            return round(int(subprocess.check_output(["sysctl", "-n", "hw.memsize"], timeout=3)) / 1024 ** 3, 1)
        else:
            with open("/proc/meminfo") as f:
                for l in f:
                    if l.startswith("MemTotal:"): return round(int(l.split()[1]) / 1024 ** 2, 1)
    except Exception:
        pass
    return 0.0


def ram_valida(valor, total=None):
    """Convierte lo que escribió el usuario a GB (float). Lanza ErrorRendimiento con un mensaje claro."""
    if isinstance(valor, bool): raise ErrorRendimiento("Escribe la memoria en GB, por ejemplo 6 o 6.5.")
    try: gb = float(str(valor).strip().replace(",", "."))
    except (TypeError, ValueError): raise ErrorRendimiento("Escribe la memoria en GB, por ejemplo 6 o 6.5.")
    if gb != gb or gb in (float("inf"), float("-inf")): raise ErrorRendimiento("Cantidad de memoria no válida.")
    total = ram_total_gb() if total is None else total
    if gb < MIN_GB: raise ErrorRendimiento(f"El mínimo es {MIN_GB} GB.")
    if total and gb > total: raise ErrorRendimiento(f"Tu equipo tiene {total:g} GB de RAM; no se puede asignar más.")
    if gb > 1024: raise ErrorRendimiento("Cantidad de memoria no válida.")
    return round(gb, 2)


def ram_texto(valor):
    """GB validados como texto compacto ('4', '6.5') para guardarlos en la instancia."""
    return f"{ram_valida(valor):g}"


# ------------------------------------------------------------------ configuración
def leer():
    c = tm._cfg().get("rendimiento")
    c = c if isinstance(c, dict) else {}
    r = dict(DEFECTO)
    try: r["hilos"] = max(1, min(HILOS_MAX, int(c.get("hilos", r["hilos"]))))
    except (TypeError, ValueError): pass
    for k in ("jvm", "gpu"):
        if isinstance(c.get(k), bool): r[k] = c[k]
    try: r["ram"] = ram_valida(c.get("ram", r["ram"]), total=0)
    except ErrorRendimiento: pass
    return r


def guardar(nuevo):
    if not isinstance(nuevo, dict): raise ErrorRendimiento("Ajustes no válidos.")
    r = leer()
    if "hilos" in nuevo:
        try: h = int(nuevo["hilos"])
        except (TypeError, ValueError): raise ErrorRendimiento("El número de descargas en paralelo no es válido.")
        r["hilos"] = max(1, min(HILOS_MAX, h))
    for k in ("jvm", "gpu"):
        if k in nuevo:
            if not isinstance(nuevo[k], bool): raise ErrorRendimiento("Ajuste no válido.")
            r[k] = nuevo[k]
    if "ram" in nuevo: r["ram"] = ram_valida(nuevo["ram"])
    with tm._lock:
        cfg = tm._cfg(); cfg["rendimiento"] = r; tm._escribir_cfg(cfg)
    return r


def hilos():
    return leer()["hilos"]


# ------------------------------------------------------------------ descargas en paralelo
def aplicar_hilos():
    """Hace que minecraft-launcher-lib use los hilos elegidos para librerías y assets (por defecto usa los que decide Python)."""
    import minecraft_launcher_lib.install as mi
    with _lock:
        if not _orig: _orig.update(libs=mi.install_libraries, assets=mi.install_assets)
        n = hilos()
        mi.install_libraries = lambda *a, **k: _orig["libs"](*a, **{**k, "max_workers": n})
        mi.install_assets = lambda *a, **k: _orig["assets"](*a, **{**k, "max_workers": n})
    return n


# ------------------------------------------------------------------ JVM
_FLAGS_G1 = ["-XX:+UnlockExperimentalVMOptions", "-XX:+UseG1GC", "-XX:MaxGCPauseMillis=130", "-XX:+DisableExplicitGC",
             "-XX:+ParallelRefProcEnabled", "-XX:G1NewSizePercent=30", "-XX:G1MaxNewSizePercent=40", "-XX:G1HeapRegionSize=8M",
             "-XX:G1ReservePercent=20", "-XX:G1HeapWastePercent=5", "-XX:G1MixedGCCountTarget=4", "-XX:InitiatingHeapOccupancyPercent=15",
             "-XX:G1MixedGCLiveThresholdPercent=90", "-XX:G1RSetUpdatingPauseTimePercent=5", "-XX:SurvivorRatio=32",
             "-XX:+PerfDisableSharedMem", "-XX:MaxTenuringThreshold=1"]


def opciones_jvm():
    """Ajustes que usa nucleo.comando_jugar: {'flags': [...], 'xms_mb': int|None}."""
    if not leer()["jvm"]: return {"flags": [], "xms_mb": None}
    return {"flags": list(_FLAGS_G1), "xms_mb": 1024}      # heap inicial pequeño = arranque más rápido; crece hasta -Xmx


# ------------------------------------------------------------------ GPU
def preparar_gpu(java, env):
    """Pide la GPU de alto rendimiento para ese java. Modifica 'env' (Linux) o el registro (Windows). No lanza errores."""
    if not leer()["gpu"]: return
    try:
        if sys.platform.startswith("win"):
            import winreg
            rutas = []
            ruta = java if os.path.isabs(java) else shutil.which(java)
            if ruta and os.path.isfile(ruta):
                rutas.append(ruta)
                hermano = os.path.join(os.path.dirname(ruta), "javaw.exe" if os.path.basename(ruta).lower() == "java.exe" else "java.exe")
                if os.path.isfile(hermano): rutas.append(hermano)
            if rutas:
                with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\DirectX\UserGpuPreferences") as k:
                    for r in rutas: winreg.SetValueEx(k, os.path.normpath(r), 0, winreg.REG_SZ, "GpuPreference=2;")
        elif sys.platform.startswith("linux"):
            env.setdefault("DRI_PRIME", "1")                      # AMD/Intel híbridas (Mesa); inofensivo con una sola gráfica
            if os.path.isdir("/proc/driver/nvidia"):              # solo con el driver propietario de NVIDIA cargado:
                env.setdefault("__NV_PRIME_RENDER_OFFLOAD", "1")  # sin él, forzar «nvidia» rompería OpenGL (el juego no abriría)
                env.setdefault("__GLX_VENDOR_LIBRARY_NAME", "nvidia")
    except Exception:
        pass
