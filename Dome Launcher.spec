# -*- mode: python ; coding: utf-8 -*-

a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('web', 'web'),         # Empaqueta toda la carpeta web y su index.html
        ('logo.ico', '.'),      # Icono de la aplicación (Windows)
        ('logo.png', '.'),      # Icono para Linux y macOS
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='Dome Launcher',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,          # <--- Modificado a False (Evita los falsos positivos del antivirus)
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='logo.ico',    # <--- Ajustado a string para mayor estabilidad
)