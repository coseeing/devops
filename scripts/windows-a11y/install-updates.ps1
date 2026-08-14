[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$logPrefix = '[install-updates]'

function ConvertTo-HResultHex {
    param(
        [Parameter(Mandatory)]
        [long]$HResult
    )

    $unsignedValue = $HResult -band 0xffffffffL
    return '0x{0:X8}' -f $unsignedValue
}

function Test-TransientWindowsUpdateHResult {
    param(
        [Parameter(Mandatory)]
        [long]$HResult
    )

    $hResultHex = ConvertTo-HResultHex -HResult $HResult
    return $hResultHex -in @(
        '0x8007045B', # ERROR_SHUTDOWN_IN_PROGRESS
        '0x8024001E', # WU_E_SERVICE_STOP
        '0x80240016'  # WU_E_INSTALL_NOT_ALLOWED
    )
}

function Write-WindowsUpdateComExceptionDiagnostics {
    param(
        [Parameter(Mandatory)]
        [string]$Operation,

        [Parameter(Mandatory)]
        [System.Runtime.InteropServices.COMException]$Exception,

        [string]$Prefix = '[install-updates]'
    )

    $hResultHex = ConvertTo-HResultHex -HResult $Exception.HResult
    Write-Host "$Prefix COM failure during $Operation; HRESULT: $hResultHex"
    if (Test-TransientWindowsUpdateHResult -HResult $Exception.HResult) {
        Write-Host 'TRANSIENT_WINDOWS_UPDATE_ERROR=true'
    }
}

function Invoke-WindowsUpdateComOperation {
    param(
        [Parameter(Mandatory)]
        [string]$Operation,

        [Parameter(Mandatory)]
        [scriptblock]$Action
    )

    try {
        & $Action
    } catch [System.Runtime.InteropServices.COMException] {
        Write-WindowsUpdateComExceptionDiagnostics -Operation $Operation -Exception $_.Exception
        throw
    }
}

function Write-InstallationDiagnostics {
    param(
        [Parameter(Mandatory)]
        $InstallResult,

        [Parameter(Mandatory)]
        $Updates,

        [string]$Prefix = '[install-updates]'
    )

    $aggregateHResult = ConvertTo-HResultHex -HResult $InstallResult.HResult
    Write-Output "$Prefix Install result code: $($InstallResult.ResultCode); HRESULT: $aggregateHResult"
    $hasTransientError = Test-TransientWindowsUpdateHResult -HResult $InstallResult.HResult

    for ($index = 0; $index -lt $Updates.Count; $index++) {
        $update = $Updates[$index]
        $updateResult = $InstallResult.GetUpdateResult($index)
        $updateHResult = ConvertTo-HResultHex -HResult $updateResult.HResult
        Write-Output (
            "$Prefix Update result: $($update.Title); code: $($updateResult.ResultCode); " +
            "HRESULT: $updateHResult; reboot required: $($updateResult.RebootRequired)"
        )
        if (Test-TransientWindowsUpdateHResult -HResult $updateResult.HResult) {
            $hasTransientError = $true
        }
    }

    if ($hasTransientError) {
        Write-Output 'TRANSIENT_WINDOWS_UPDATE_ERROR=true'
    }
}

Write-Output "$logPrefix Searching for updates..."
$updateSession = Invoke-WindowsUpdateComOperation -Operation 'session creation' -Action {
    New-Object -ComObject Microsoft.Update.Session
}
$updateSearcher = Invoke-WindowsUpdateComOperation -Operation 'searcher creation' -Action {
    $updateSession.CreateUpdateSearcher()
}
$searchResult = Invoke-WindowsUpdateComOperation -Operation 'search' -Action {
    $updateSearcher.Search("IsInstalled=0 and IsHidden=0")
}

if ($searchResult.Updates.Count -eq 0) {
    Write-Output "$logPrefix No updates found."
    Write-Output "REBOOT_REQUIRED=false"
    exit 0
}

$updatesToDownload = New-Object -ComObject Microsoft.Update.UpdateColl
foreach ($update in $searchResult.Updates) {
    Write-Output "$logPrefix Found update: $($update.Title)"
    if (-not $update.EulaAccepted) { $update.AcceptEula() }
    $updatesToDownload.Add($update) | Out-Null
}

Write-Output "$logPrefix Downloading $($updatesToDownload.Count) update(s)..."
$downloader = $updateSession.CreateUpdateDownloader()
$downloader.Updates = $updatesToDownload
$downloadResult = Invoke-WindowsUpdateComOperation -Operation 'download' -Action {
    $downloader.Download()
}
if ($downloadResult.ResultCode -ne 2) {
    throw "$logPrefix Download failed with result code $($downloadResult.ResultCode)"
}

$updatesToInstall = New-Object -ComObject Microsoft.Update.UpdateColl
foreach ($update in $updatesToDownload) {
    if ($update.IsDownloaded) { $updatesToInstall.Add($update) | Out-Null }
}

Write-Output "$logPrefix Installing $($updatesToInstall.Count) update(s)..."
$installer = $updateSession.CreateUpdateInstaller()
$installer.Updates = $updatesToInstall
$installResult = Invoke-WindowsUpdateComOperation -Operation 'install' -Action {
    $installer.Install()
}

Write-InstallationDiagnostics -InstallResult $installResult -Updates $updatesToInstall
Write-Output "$logPrefix Reboot required: $($installResult.RebootRequired)"

if ($installResult.ResultCode -ne 2 -and $installResult.ResultCode -ne 3) {
    throw "$logPrefix Install failed with result code $($installResult.ResultCode)"
}

if ($installResult.RebootRequired) {
    Write-Output "REBOOT_REQUIRED=true"
} else {
    Write-Output "REBOOT_REQUIRED=false"
}
