[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$logPrefix = '[install-updates]'

Write-Output "$logPrefix Searching for updates..."
$updateSession = New-Object -ComObject Microsoft.Update.Session
$updateSearcher = $updateSession.CreateUpdateSearcher()
$searchResult = $updateSearcher.Search("IsInstalled=0 and IsHidden=0")

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
$downloadResult = $downloader.Download()
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
$installResult = $installer.Install()

Write-Output "$logPrefix Install result code: $($installResult.ResultCode)"
Write-Output "$logPrefix Reboot required: $($installResult.RebootRequired)"

if ($installResult.ResultCode -ne 2 -and $installResult.ResultCode -ne 3) {
    throw "$logPrefix Install failed with result code $($installResult.ResultCode)"
}

if ($installResult.RebootRequired) {
    Write-Output "REBOOT_REQUIRED=true"
} else {
    Write-Output "REBOOT_REQUIRED=false"
}
