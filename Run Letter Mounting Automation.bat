@echo off
setlocal
set "PYTHON=C:\Users\auron\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
set "PYTHONW=C:\Users\auron\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\pythonw.exe"
set "APP=%~dp0letter_mounting_automation_app.py"
set "TCL_LIBRARY=C:\Users\auron\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\tcl\tcl8.6"
set "TK_LIBRARY=C:\Users\auron\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\tcl\tk8.6"

if "%~1"=="" if exist "%PYTHONW%" (
  start "" "%PYTHONW%" "%APP%"
  exit /b 0
)

if exist "%PYTHON%" (
  "%PYTHON%" "%APP%" %*
) else (
  py "%APP%" %*
)
