[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$logPrefix = '[install-software]'

if (-not (Get-Command choco -ErrorAction SilentlyContinue)) {
    Write-Output "$logPrefix Installing Chocolatey..."
    Set-ExecutionPolicy Bypass -Scope Process -Force
    [System.Net.ServicePointManager]::SecurityProtocol = [System.Net.ServicePointManager]::SecurityProtocol -bor 3072
    Invoke-Expression ((New-Object System.Net.WebClient).DownloadString('https://community.chocolatey.org/install.ps1'))
    $env:Path = "$env:Path;C:\ProgramData\chocolatey\bin"
}

$packages = @('googlechrome', 'firefox', 'nvda')
foreach ($package in $packages) {
    Write-Output "$logPrefix Installing/updating $package..."
    choco upgrade $package -y --no-progress | Out-Null
    if ($LASTEXITCODE -ne 0 -and $LASTEXITCODE -ne 3010) {
        throw "$logPrefix choco upgrade $package failed with exit code $LASTEXITCODE"
    }
}

Write-Output "$logPrefix Installed package versions:"
$versionLines = @(choco list --limit-output |
    Where-Object { $_ -match '^(googlechrome|firefox|nvda)\|' })
if ($versionLines.Count -lt $packages.Count) {
    throw "$logPrefix Expected $($packages.Count) installed package versions but found $($versionLines.Count): $($versionLines -join '; ')"
}
foreach ($line in $versionLines) {
    $parts = $line -split '\|'
    Write-Output "VERSION_$($parts[0].ToUpper())=$($parts[1])"
}
