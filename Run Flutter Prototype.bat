@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0flutter_workbench\launch.ps1"
if errorlevel 1 pause
