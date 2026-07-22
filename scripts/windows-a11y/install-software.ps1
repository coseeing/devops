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

$chocoPath = (Get-Command choco -ErrorAction Stop).Source
$successfulExitCodes = @(0, 2, 1641, 3010)

function Install-OrUpgradePackage {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Package,

        [int]$MaxAttempts = 3
    )

    for ($attempt = 1; $attempt -le $MaxAttempts; $attempt++) {
        Write-Output "$logPrefix Installing/updating $Package (attempt $attempt of $MaxAttempts)..."

        # Do not discard Chocolatey's output: it contains the installer-specific
        # error that is needed to diagnose failures from the SSM command log.
        & $chocoPath upgrade $Package -y --no-progress --execution-timeout=1200 --ignore-detected-reboot
        $exitCode = $LASTEXITCODE

        if ($exitCode -in $successfulExitCodes) {
            return
        }

        if ($attempt -lt $MaxAttempts) {
            $retryDelay = 15 * $attempt
            Write-Warning "$logPrefix choco upgrade $Package failed with exit code $exitCode; retrying in $retryDelay seconds."
            Start-Sleep -Seconds $retryDelay
        }
    }

    throw "$logPrefix choco upgrade $Package failed after $MaxAttempts attempts (last exit code: $exitCode)"
}

$packages = @('googlechrome', 'firefox', 'nvda')
foreach ($package in $packages) {
    Install-OrUpgradePackage -Package $package
}

Write-Output "$logPrefix Installed package versions:"
$versionLines = @(& $chocoPath list --limit-output |
    Where-Object { $_ -match '^(googlechrome|firefox|nvda)\|' })
if ($versionLines.Count -lt $packages.Count) {
    throw "$logPrefix Expected $($packages.Count) installed package versions but found $($versionLines.Count): $($versionLines -join '; ')"
}
foreach ($line in $versionLines) {
    $parts = $line -split '\|'
    Write-Output "VERSION_$($parts[0].ToUpper())=$($parts[1])"
}
