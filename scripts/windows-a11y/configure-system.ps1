[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$logPrefix = '[configure-system]'

Write-Output "$logPrefix Enabling Remote Desktop..."
Set-ItemProperty -Path 'HKLM:\System\CurrentControlSet\Control\Terminal Server' -Name 'fDenyTSConnections' -Value 0
Enable-NetFirewallRule -DisplayGroup 'Remote Desktop'

Write-Output "$logPrefix Installing Traditional Chinese language pack..."
if (-not (Get-InstalledLanguage -Language zh-TW -ErrorAction SilentlyContinue)) {
    Install-Language -Language zh-TW -ErrorAction Stop
}

Write-Output "$logPrefix Setting system display language to zh-TW..."
Set-SystemPreferredUILanguage -Language zh-TW
Set-WinSystemLocale -SystemLocale zh-TW
Set-Culture -CultureInfo zh-TW

Write-Output "$logPrefix Display language configured. A reboot is required for the UI language change to fully apply."
Write-Output "REBOOT_REQUIRED=true"
