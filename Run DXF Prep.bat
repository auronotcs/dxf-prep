@echo off
setlocal
set "PYTHON=C:\Users\auron\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
set "APP=%~dp0dxf_prep_app.py"
set "TCL_LIBRARY=C:\Users\auron\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\tcl\tcl8.6"
set "TK_LIBRARY=C:\Users\auron\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\tcl\tk8.6"

if exist "%PYTHON%" (
  "%PYTHON%" "%APP%"
) else (
  py "%APP%"
)
