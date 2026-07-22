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

function Install-GoogleChrome {
    $installerUri = 'https://dl.google.com/dl/chrome/install/googlechromestandaloneenterprise64.msi'
    $downloadDirectory = Join-Path $env:TEMP 'windows-a11y-installers'
    $installerPath = Join-Path $downloadDirectory 'googlechromestandaloneenterprise64.msi'

    New-Item -ItemType Directory -Path $downloadDirectory -Force | Out-Null

    Write-Output "$logPrefix Downloading the Google Chrome Enterprise MSI from Google..."
    Invoke-WebRequest -Uri $installerUri -OutFile $installerPath -UseBasicParsing

    # The Chrome Enterprise URL always points at the current stable MSI, so a
    # static checksum would become stale. Verify Google's code-signing identity
    # instead of bypassing integrity checks with Chocolatey's --ignore-checksums.
    $signature = Get-AuthenticodeSignature -FilePath $installerPath
    $publisher = $signature.SignerCertificate.Subject
    if ($signature.Status -ne 'Valid' -or $publisher -notmatch '(^|, )O=Google LLC(,|$)') {
        throw "$logPrefix Chrome MSI signature verification failed (status: $($signature.Status), publisher: $publisher)"
    }

    Write-Output "$logPrefix Installing/updating Google Chrome (verified publisher: $publisher)..."
    & msiexec.exe /i $installerPath /qn /norestart
    $exitCode = $LASTEXITCODE
    if ($exitCode -notin @(0, 1641, 3010)) {
        throw "$logPrefix Google Chrome MSI installation failed with exit code $exitCode"
    }

    Remove-Item -Path $installerPath -Force
}

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

$packages = @('firefox', 'nvda')
foreach ($package in $packages) {
    Install-OrUpgradePackage -Package $package
}

Install-GoogleChrome

Write-Output "$logPrefix Installed package versions:"
$softwareExecutables = [ordered]@{
    GOOGLECHROME = 'C:\Program Files\Google\Chrome\Application\chrome.exe'
    FIREFOX = 'C:\Program Files\Mozilla Firefox\firefox.exe'
    NVDA = 'C:\Program Files (x86)\NVDA\nvda.exe'
}
foreach ($software in $softwareExecutables.GetEnumerator()) {
    if (-not (Test-Path $software.Value)) {
        throw "$logPrefix $($software.Key) executable was not found at $($software.Value)"
    }

    $versionInfo = (Get-Item $software.Value).VersionInfo
    $version = if ($versionInfo.ProductVersion) { $versionInfo.ProductVersion } else { $versionInfo.FileVersion }
    Write-Output "VERSION_$($software.Key)=$version"
}
