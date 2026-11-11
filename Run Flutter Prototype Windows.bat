@echo off
set "APP=%~dp0flutter_workbench\build\windows\x64\runner\Release\workbench_prototype.exe"
if not exist "%APP%" (
  echo The Windows prototype has not been built yet.
  echo From flutter_workbench run: ..\.flutter-sdk\bin\flutter.bat build windows --release
  pause
  exit /b 1
)
start "" "%APP%"
