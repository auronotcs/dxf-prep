@echo off
setlocal
set "PYTHON=C:\Users\auron\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
set "TCL_LIBRARY=C:\Users\auron\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\tcl\tcl8.6"
set "TK_LIBRARY=C:\Users\auron\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\tcl\tk8.6"

if exist "%PYTHON%" (
  "%PYTHON%" -m PyInstaller --noconfirm --clean --onefile --windowed --name "DXF Prep" --icon "dxf_prep_icon.ico" --hidden-import _tkinter --add-data "dxf_prep_icon.ico;." --add-data "%TCL_LIBRARY%;tcl\tcl8.6" --add-data "%TK_LIBRARY%;tcl\tk8.6" --add-binary "C:\Users\auron\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\DLLs\tcl86t.dll;." --add-binary "C:\Users\auron\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\DLLs\tk86t.dll;." --add-binary "C:\Users\auron\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\DLLs\_tkinter.pyd;." "dxf_prep_app.py"
) else (
  py -m PyInstaller --noconfirm --clean --onefile --windowed --name "DXF Prep" --icon "dxf_prep_icon.ico" --hidden-import _tkinter --add-data "dxf_prep_icon.ico;." "dxf_prep_app.py"
)
