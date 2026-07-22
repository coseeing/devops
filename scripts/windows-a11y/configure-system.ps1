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
$traditionalChineseUiCultures = @('zh-TW', 'zh-HK')
if ($installedUiCulture -notin $traditionalChineseUiCultures -or $systemLocale -ne 'zh-TW') {
    throw "Expected the AWS Traditional Chinese base AMI to use a Traditional Chinese UI culture (zh-TW or zh-HK) and the zh-TW system locale, but installed UI culture is $installedUiCulture and system locale is $systemLocale."
}

Write-Output "$logPrefix Remote Desktop enabled and Traditional Chinese base locale confirmed (UI: $installedUiCulture, system: $systemLocale)."
