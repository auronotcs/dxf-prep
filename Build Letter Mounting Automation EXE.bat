@echo off
setlocal
set "PYTHON=C:\Users\auron\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
set "PYROOT=C:\Users\auron\.cache\codex-runtimes\codex-primary-runtime\dependencies\python"
set "TCL_LIBRARY=%PYROOT%\tcl\tcl8.6"
set "TK_LIBRARY=%PYROOT%\tcl\tk8.6"
set "PYTHONPATH=%~dp0.build_tools"

if not exist "%PYTHON%" (
  echo Bundled Python runtime not found.
  exit /b 1
)

"%PYTHON%" -m PyInstaller --noconfirm --clean --onefile --windowed ^
  --name "Letter Mounting Automation v3" ^
  --icon "assets\letter_mounting_icon.ico" ^
  --paths "holegen_libs" ^
  --hidden-import _tkinter ^
  --collect-all scipy ^
  --collect-all shapely ^
  --add-binary "holegen_libs\numpy.libs\*.dll;numpy.libs" ^
  --add-binary "holegen_libs\scipy.libs\*.dll;scipy.libs" ^
  --add-data "Keyhole example.dxf;." ^
  --add-data "assets\letter_mounting_icon.ico;assets" ^
  --add-data "%PYROOT%\Lib\tkinter;Lib\tkinter" ^
  --add-data "%TCL_LIBRARY%;tcl\tcl8.6" ^
  --add-data "%TK_LIBRARY%;tcl\tk8.6" ^
  --add-binary "%PYROOT%\DLLs\tcl86t.dll;." ^
  --add-binary "%PYROOT%\DLLs\tk86t.dll;." ^
  --add-binary "%PYROOT%\DLLs\_tkinter.pyd;." ^
  "letter_mounting_automation_app.py"
