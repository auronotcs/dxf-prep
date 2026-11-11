param([switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$prototypeRoot = $PSScriptRoot
$webRoot = Join-Path $prototypeRoot 'build\web'
$url = 'http://127.0.0.1:58743'
$pythonPath = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
if (-not (Test-Path -LiteralPath (Join-Path $webRoot 'index.html'))) {
    throw 'Build the prototype first: ..\.flutter-sdk\bin\flutter.bat build web --release --no-web-resources-cdn'
}
function Test-PrototypeServer {
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri "$url/prototype-id.txt" -TimeoutSec 2
        return $response.Content.Trim() -eq 'cam-workbench-flutter-prototype-v1'
    } catch { return $false }
}
if (-not (Test-PrototypeServer)) {
    if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'The bundled Python runtime is missing. Serve build/web on localhost using Python 3 or another static HTTP server.' }
    $server = Start-Process -FilePath $pythonPath -ArgumentList @('-m', 'http.server', '58743', '--bind', '127.0.0.1') -WorkingDirectory $webRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $prototypeRoot 'build\server.log') -RedirectStandardError (Join-Path $prototypeRoot 'build\server-error.log')
    $ready = $false
    for ($i = 0; $i -lt 20; $i++) {
        Start-Sleep -Milliseconds 250
        $server.Refresh()
        if ($server.HasExited) { throw 'The preview server could not start. Check build/server-error.log; port 58743 may be in use.' }
        if (Test-PrototypeServer) { $ready = $true; break }
    }
    if (-not $ready) { throw 'The preview server did not become ready. Check build/server-error.log.' }
    $server.Id | Set-Content -LiteralPath (Join-Path $prototypeRoot 'build\server.pid')
}
if (-not $NoBrowser) { Start-Process $url }
Write-Output "Flutter prototype ready: $url"
