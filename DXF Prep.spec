# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['dxf_prep_app.py'],
    pathex=[],
    binaries=[('C:/Users/auron/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/DLLs/tcl86t.dll', '.'), ('C:/Users/auron/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/DLLs/tk86t.dll', '.'), ('C:/Users/auron/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/DLLs/_tkinter.pyd', '.')],
    datas=[('dxf_prep_icon.ico', '.'), ('C:/Users/auron/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/Lib/tkinter', 'Lib/tkinter'), ('C:/Users/auron/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/tcl/tcl8.6', 'tcl/tcl8.6'), ('C:/Users/auron/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/tcl/tk8.6', 'tcl/tk8.6')],
    hiddenimports=['_tkinter'],
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
    name='DXF Prep',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['dxf_prep_icon.ico'],
)
