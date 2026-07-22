[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$logPrefix = '[configure-system]'

Write-Output "$logPrefix Enabling Remote Desktop..."
Set-ItemProperty -Path 'HKLM:\System\CurrentControlSet\Control\Terminal Server' -Name 'fDenyTSConnections' -Value 0
Get-NetFirewallRule -Name 'RemoteDesktop*' | Enable-NetFirewallRule

Write-Output "$logPrefix Verifying the Traditional Chinese base image..."
$installedUiCulture = [System.Globalization.CultureInfo]::InstalledUICulture.Name
$systemLocale = (Get-WinSystemLocale).Name
if ($installedUiCulture -ne 'zh-TW' -or $systemLocale -ne 'zh-TW') {
    throw "Expected the AWS Traditional Chinese base AMI to use zh-TW, but installed UI culture is $installedUiCulture and system locale is $systemLocale."
}

Write-Output "$logPrefix Remote Desktop enabled and zh-TW base locale confirmed."
