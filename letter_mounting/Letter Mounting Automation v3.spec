# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas = [('Keyhole example.dxf', '.'), ('assets/letter_mounting_icon.ico', 'assets'), ('C:/Users/auron/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/Lib/tkinter', 'Lib/tkinter'), ('C:/Users/auron/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/tcl/tcl8.6', 'tcl/tcl8.6'), ('C:/Users/auron/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/tcl/tk8.6', 'tcl/tk8.6')]
binaries = [('holegen_libs/numpy.libs/*.dll', 'numpy.libs'), ('holegen_libs/scipy.libs/*.dll', 'scipy.libs'), ('C:/Users/auron/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/DLLs/tcl86t.dll', '.'), ('C:/Users/auron/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/DLLs/tk86t.dll', '.'), ('C:/Users/auron/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/DLLs/_tkinter.pyd', '.')]
hiddenimports = ['_tkinter']
tmp_ret = collect_all('scipy')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('shapely')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['letter_mounting_automation_app.py'],
    pathex=['holegen_libs'],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
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
    name='Letter Mounting Automation v3',
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
    icon=['assets/letter_mounting_icon.ico'],
)
