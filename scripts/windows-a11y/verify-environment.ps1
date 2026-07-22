[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

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

$results = [ordered]@{}

$results.ChromePath = Find-GoogleChromeExecutable
$results.ChromeInstalled = [bool]$results.ChromePath
$results.FirefoxInstalled = Test-Path 'C:\Program Files\Mozilla Firefox\firefox.exe'
$results.NvdaInstalled = Test-Path 'C:\Program Files (x86)\NVDA\nvda.exe'

$rdpValue = (Get-ItemProperty -Path 'HKLM:\System\CurrentControlSet\Control\Terminal Server' -Name 'fDenyTSConnections').fDenyTSConnections
$results.RdpEnabled = ($rdpValue -eq 0)

$results.CoseeingIsAdmin = [bool](Get-LocalGroupMember -Group 'Administrators' -Member 'coseeing' -ErrorAction SilentlyContinue)
$results.UserIsNotAdmin = -not [bool](Get-LocalGroupMember -Group 'Administrators' -Member 'user' -ErrorAction SilentlyContinue)
$results.UserAccountExists = [bool](Get-LocalUser -Name 'user' -ErrorAction SilentlyContinue)
$results.BaseInstalledUiCulture = [System.Globalization.CultureInfo]::InstalledUICulture.Name
$results.DisplayLanguage = Get-SystemPreferredUILanguage
$results.SystemLocale = (Get-WinSystemLocale).Name
$results.DisplayLanguageIsTraditionalChinese = ($results.DisplayLanguage -in @('zh-TW', 'zh-Hant-TW'))
$results.SystemLocaleIsTraditionalChinese = ($results.SystemLocale -eq 'zh-TW')

$checks = @('ChromeInstalled','FirefoxInstalled','NvdaInstalled','RdpEnabled','CoseeingIsAdmin','UserIsNotAdmin','UserAccountExists','DisplayLanguageIsTraditionalChinese','SystemLocaleIsTraditionalChinese')
$allPassed = -not ($checks | Where-Object { $results[$_] -ne $true })
$results.AllChecksPassed = $allPassed

$json = $results | ConvertTo-Json -Compress
Write-Output "VERIFY_RESULT_JSON=$json"

if (-not $allPassed) {
    throw "Environment verification failed: $json"
}
