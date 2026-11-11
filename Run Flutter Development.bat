@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0flutter_workbench\launch-development.ps1"
if errorlevel 1 pause
