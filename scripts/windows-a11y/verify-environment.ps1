[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

$results = [ordered]@{}

$results.ChromeInstalled = Test-Path 'C:\Program Files\Google\Chrome\Application\chrome.exe'
$results.FirefoxInstalled = Test-Path 'C:\Program Files\Mozilla Firefox\firefox.exe'
$results.NvdaInstalled = Test-Path 'C:\Program Files (x86)\NVDA\nvda.exe'

$rdpValue = (Get-ItemProperty -Path 'HKLM:\System\CurrentControlSet\Control\Terminal Server' -Name 'fDenyTSConnections').fDenyTSConnections
$results.RdpEnabled = ($rdpValue -eq 0)

$results.CoseeingIsAdmin = [bool](Get-LocalGroupMember -Group 'Administrators' -Member 'coseeing' -ErrorAction SilentlyContinue)
$results.UserIsNotAdmin = -not [bool](Get-LocalGroupMember -Group 'Administrators' -Member 'user' -ErrorAction SilentlyContinue)
$results.UserAccountExists = [bool](Get-LocalUser -Name 'user' -ErrorAction SilentlyContinue)
$results.DisplayLanguage = (Get-WinSystemLocale).Name

$checks = @('ChromeInstalled','FirefoxInstalled','NvdaInstalled','RdpEnabled','CoseeingIsAdmin','UserIsNotAdmin','UserAccountExists')
$allPassed = -not ($checks | Where-Object { $results[$_] -ne $true })
$results.AllChecksPassed = $allPassed

$json = $results | ConvertTo-Json -Compress
Write-Output "VERIFY_RESULT_JSON=$json"

if (-not $allPassed) {
    throw "Environment verification failed: $json"
}
