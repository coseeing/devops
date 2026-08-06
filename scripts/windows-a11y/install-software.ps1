[CmdletBinding()]
param([switch]$SkipExecution)

$ErrorActionPreference = 'Stop'
$logPrefix = '[install-software]'
$nvdaStableBaseUri = [Uri]'https://download.nvaccess.org/releases/stable/'

function Get-FirefoxInstallerUri {
    return [Uri]'https://download.mozilla.org/?product=firefox-latest-ssl&os=win64&lang=zh-TW'
}

function Get-NvdaStableInstallerUri {
    param(
        [Parameter(Mandatory = $true)][string]$Content,
        [Uri]$BaseUri = $nvdaStableBaseUri
    )

    $hrefPattern = 'href=["''](?<href>nvda_(?<version>\d{4}\.\d+(?:\.\d+)?)\.exe)["'']'
    $matches = @([regex]::Matches($Content, $hrefPattern, 'IgnoreCase'))
    if ($matches.Count -ne 1) {
        throw "$logPrefix Expected exactly one numeric stable NVDA installer, found $($matches.Count)."
    }
    return [Uri]::new($BaseUri, $matches[0].Groups['href'].Value)
}

function Invoke-WebRequestWithRetry {
    param(
        [Parameter(Mandatory = $true)][Uri]$Uri,
        [string]$OutFile,
        [ValidateRange(1, 3)][int]$MaxAttempts = 3
    )
    for ($attempt = 1; $attempt -le $MaxAttempts; $attempt++) {
        try {
            $parameters = @{ Uri = $Uri; UseBasicParsing = $true }
            if ($OutFile) { $parameters.OutFile = $OutFile }
            return Invoke-WebRequest @parameters
        } catch {
            if ($attempt -eq $MaxAttempts) {
                throw "$logPrefix Download from $Uri failed after $MaxAttempts attempts: $($_.Exception.Message)"
            }
            Start-Sleep -Seconds (15 * $attempt)
        }
    }
}

function Assert-AuthenticodePublisher {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$ProductName,
        [Parameter(Mandatory = $true)][string]$PublisherPattern
    )
    $signature = Get-AuthenticodeSignature -FilePath $Path
    $publisher = if ($signature.SignerCertificate) {
        $signature.SignerCertificate.Subject
    } else {
        ''
    }
    if ($signature.Status -ne 'Valid' -or $publisher -notmatch $PublisherPattern) {
        throw "$logPrefix $ProductName signature verification failed (status: $($signature.Status), publisher: $publisher)"
    }
}

function Assert-InstallerExitCode {
    param([string]$ProductName, [int]$ExitCode)
    if ($ExitCode -ne 0) {
        throw "$logPrefix $ProductName installation failed with exit code $ExitCode."
    }
}

if (-not $SkipExecution) {
    if (-not (Get-Command choco -ErrorAction SilentlyContinue)) {
        Write-Output "$logPrefix Installing Chocolatey..."
        Set-ExecutionPolicy Bypass -Scope Process -Force
        [System.Net.ServicePointManager]::SecurityProtocol = [System.Net.ServicePointManager]::SecurityProtocol -bor 3072
        Invoke-Expression ((New-Object System.Net.WebClient).DownloadString('https://community.chocolatey.org/install.ps1'))
        $env:Path = "$env:Path;C:\ProgramData\chocolatey\bin"
    }

    $chocoPath = (Get-Command choco -ErrorAction Stop).Source
}
$successfulExitCodes = @(0, 2, 1641, 3010)

function Install-GoogleChrome {
    $installerUri = 'https://dl.google.com/dl/chrome/install/googlechromestandaloneenterprise64.msi'
    $downloadDirectory = Join-Path $env:TEMP 'windows-a11y-installers'
    $installerPath = Join-Path $downloadDirectory 'googlechromestandaloneenterprise64.msi'
    $installerLogPath = Join-Path $downloadDirectory 'googlechrome-install.log'

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
    # Start-Process avoids sending msiexec's UTF-16 output through SSM and, with
    # -Wait, does not let executable verification race the installer service.
    $installerArguments = @(
        '/i'
        "`"$installerPath`""
        '/qn'
        '/norestart'
        '/L*v'
        "`"$installerLogPath`""
    )
    $installerProcess = Start-Process -FilePath "$env:SystemRoot\System32\msiexec.exe" -ArgumentList $installerArguments -Wait -PassThru
    $exitCode = $installerProcess.ExitCode
    if ($exitCode -notin @(0, 1641, 3010)) {
        $logTail = (Get-Content -LiteralPath $installerLogPath -Tail 40 -ErrorAction SilentlyContinue) -join [Environment]::NewLine
        throw "$logPrefix Google Chrome MSI installation failed with exit code $exitCode.`n$logTail"
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

function Find-GoogleChromeExecutable {
    $candidates = @(
        'C:\Program Files\Google\Chrome\Application\chrome.exe'
        'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe'
    )
    $appPathRegistryKeys = @(
        'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe'
        'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe'
    )

    foreach ($registryKey in $appPathRegistryKeys) {
        if (Test-Path $registryKey) {
            $registeredPath = (Get-Item -LiteralPath $registryKey).GetValue('')
            if ($registeredPath) {
                $candidates += $registeredPath.Trim('"')
            }
        }
    }

    return $candidates | Select-Object -Unique | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
}

function Wait-GoogleChromeExecutable {
    param([int]$TimeoutSeconds = 60)

    $stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
    do {
        $executable = Find-GoogleChromeExecutable
        if ($executable) {
            return $executable
        }

        Start-Sleep -Seconds 2
    } while ($stopwatch.Elapsed.TotalSeconds -lt $TimeoutSeconds)

    return $null
}

if (-not $SkipExecution) {
    $packages = @('firefox', 'nvda')
    foreach ($package in $packages) {
        Install-OrUpgradePackage -Package $package
    }

    Install-GoogleChrome

    Write-Output "$logPrefix Installed package versions:"
    $chromeExecutable = Wait-GoogleChromeExecutable
    if (-not $chromeExecutable) {
        $installerLogPath = Join-Path $env:TEMP 'windows-a11y-installers\googlechrome-install.log'
        $logTail = (Get-Content -LiteralPath $installerLogPath -Tail 40 -ErrorAction SilentlyContinue) -join [Environment]::NewLine
        throw "$logPrefix GOOGLECHROME executable was not found after waiting for the MSI installation to settle. MSI log: $installerLogPath`n$logTail"
    }

    $softwareExecutables = [ordered]@{
        GOOGLECHROME = $chromeExecutable
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
}
