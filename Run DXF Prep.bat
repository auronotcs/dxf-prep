@echo off
setlocal
set "PYTHON=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
set "APP=%~dp0dxf_prep\dxf_prep_app.py"
set "TCL_LIBRARY=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\tcl\tcl8.6"
set "TK_LIBRARY=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\tcl\tk8.6"

if exist "%PYTHON%" (
  "%PYTHON%" "%APP%"
) else (
  py "%APP%"
)
