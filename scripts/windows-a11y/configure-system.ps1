[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$logPrefix = '[configure-system]'

Write-Output "$logPrefix Enabling Remote Desktop..."
Set-ItemProperty -Path 'HKLM:\System\CurrentControlSet\Control\Terminal Server' -Name 'fDenyTSConnections' -Value 0
Get-NetFirewallRule -Name 'RemoteDesktop*' | Enable-NetFirewallRule

Write-Output "$logPrefix Ensuring the zh-TW language pack is installed..."
if (-not (Get-InstalledLanguage -Language 'zh-TW' -ErrorAction SilentlyContinue)) {
    Install-Language -Language 'zh-TW' -CopyToSettings -ErrorAction Stop
}

Write-Output "$logPrefix Setting the system UI language and locale to zh-TW..."
Set-SystemPreferredUILanguage -Language 'zh-TW'
Set-WinSystemLocale -SystemLocale 'zh-TW'
Set-Culture -CultureInfo 'zh-TW'

$preferredUiLanguage = Get-SystemPreferredUILanguage
$systemLocale = (Get-WinSystemLocale).Name
if ($preferredUiLanguage -ne 'zh-TW' -or $systemLocale -ne 'zh-TW') {
    throw "Failed to configure zh-TW: preferred UI language is $preferredUiLanguage and system locale is $systemLocale."
}

Write-Output "$logPrefix Remote Desktop enabled and zh-TW configured. A reboot is required for the UI language to take effect."
Write-Output 'REBOOT_REQUIRED=true'
