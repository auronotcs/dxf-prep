$ErrorActionPreference = 'Stop'
$installerRoot = Join-Path $PSScriptRoot 'build\cpp-setup'
New-Item -ItemType Directory -Path $installerRoot -Force | Out-Null
$bootstrapper = Join-Path $installerRoot 'vs_buildtools.exe'
Invoke-WebRequest -UseBasicParsing -Uri 'https://aka.ms/vs/17/release/vs_buildtools.exe' -OutFile $bootstrapper
$signature = Get-AuthenticodeSignature -LiteralPath $bootstrapper
if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'O=Microsoft Corporation') {
    throw "The Microsoft installer signature could not be verified: $($signature.Status)"
}
Write-Output "Verified installer: $($signature.SignerCertificate.Subject)"
$arguments = @('--quiet', '--wait', '--norestart', '--add', 'Microsoft.VisualStudio.Workload.VCTools', '--includeRecommended', '--add', 'Microsoft.VisualStudio.Component.VC.CMake.Project')
$principal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
if ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    $installerProcess = Start-Process -FilePath $bootstrapper -ArgumentList $arguments -WindowStyle Hidden -PassThru
} else {
    $installerProcess = Start-Process -FilePath $bootstrapper -ArgumentList $arguments -Verb RunAs -WindowStyle Hidden -PassThru
}
Write-Output "Installing Microsoft C++ workload; installer process $($installerProcess.Id)."
while (-not $installerProcess.WaitForExit(30000)) {
    Write-Output 'C++ tools installation is still running.'
}
$installerProcess.Refresh()
$result = [pscustomobject]@{ExitCode=$installerProcess.ExitCode; CompletedAt=(Get-Date -Format o); RebootRequired=($installerProcess.ExitCode -eq 3010)}
$result | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $installerRoot 'result.json')
$result | Format-List
if ($installerProcess.ExitCode -notin @(0,3010)) { throw "Build Tools installer exited with code $($installerProcess.ExitCode). See Visual Studio setup logs in the Windows temporary directory." }
