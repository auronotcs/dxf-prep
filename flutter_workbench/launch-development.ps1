$ErrorActionPreference = 'Stop'
$pythonPath = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'The Python runtime used by Design is missing.' }
$flutterPath = Join-Path $PSScriptRoot '..\.flutter-sdk\bin\flutter.bat'
if (-not (Test-Path -LiteralPath $flutterPath)) { throw 'The local Flutter SDK is missing.' }
$launcherPath = Join-Path $PSScriptRoot 'tools\dev_launcher.py'
Start-Process -FilePath $pythonPath -ArgumentList @('-u', ('"' + $launcherPath + '"')) -WorkingDirectory $PSScriptRoot -WindowStyle Hidden
Write-Output 'Starting Flutter development mode. The first debug build may take a few minutes.'
Write-Output 'Use Reload UI in the app after editing Flutter code. Startup details: flutter_workbench\build\development.log'
