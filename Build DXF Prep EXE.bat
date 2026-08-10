@echo off
setlocal
set "PYTHON=C:\Users\auron\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
set "PYROOT=C:\Users\auron\.cache\codex-runtimes\codex-primary-runtime\dependencies\python"
set "TCL_LIBRARY=C:\Users\auron\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\tcl\tcl8.6"
set "TK_LIBRARY=C:\Users\auron\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\tcl\tk8.6"

if exist "%PYTHON%" (
  "%PYTHON%" -m PyInstaller --noconfirm --clean --onefile --windowed --name "DXF Prep" --icon "dxf_prep_icon.ico" --hidden-import _tkinter --add-data "dxf_prep_icon.ico;." --add-data "%PYROOT%\Lib\tkinter;Lib\tkinter" --add-data "%TCL_LIBRARY%;tcl\tcl8.6" --add-data "%TK_LIBRARY%;tcl\tk8.6" --add-binary "%PYROOT%\DLLs\tcl86t.dll;." --add-binary "%PYROOT%\DLLs\tk86t.dll;." --add-binary "%PYROOT%\DLLs\_tkinter.pyd;." "dxf_prep_app.py"
) else (
  py -m PyInstaller --noconfirm --clean --onefile --windowed --name "DXF Prep" --icon "dxf_prep_icon.ico" --hidden-import _tkinter --add-data "dxf_prep_icon.ico;." "dxf_prep_app.py"
)
