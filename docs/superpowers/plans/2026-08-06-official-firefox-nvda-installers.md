# Official Firefox and NVDA Installers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every Windows accessibility AMI build install the current stable Firefox zh-TW 64-bit release and current stable NVDA release directly from their official publishers.

**Architecture:** Keep `install-software.ps1` as the SSM entry point, but give it a `-SkipExecution` test seam and focused resolver, download, signature, installer, and version-output functions. Pester tests dot-source the real script and mock only Windows/network boundaries; the workflow-facing version output remains unchanged.

**Tech Stack:** Windows PowerShell 5.1-compatible PowerShell, Pester 5.5+, Authenticode, Mozilla and NV Access HTTPS download services, AWS SSM/GitHub Actions.

## Global Constraints

- Firefox is the latest stable `zh-TW` 64-bit release; exclude ESR, Beta, Developer Edition, and Nightly.
- NVDA is the latest stable numeric release; exclude alpha, beta, and release-candidate builds.
- Download only from `download.mozilla.org` and `download.nvaccess.org`.
- Require a valid Authenticode signature from `Mozilla Corporation` or `NV Access Limited`, respectively.
- Do not fall back to Chocolatey or another third-party package manager.
- Retry network operations at most three times, waiting 15 seconds and then 30 seconds.
- Preserve `VERSION_GOOGLECHROME`, `VERSION_FIREFOX`, and `VERSION_NVDA` output lines consumed by the AMI workflow.
- Do not change the Windows Server 2025 base AMI, Chrome behavior, AWS infrastructure, or executable verification paths.
- Use Firefox `/S` and NVDA `--install-silent`; executable installer success is exit code `0` only.
- Every Codex-created commit includes `Co-authored-by: Codex <codex@openai.com>`.

---

## File Structure

- Modify `scripts/windows-a11y/install-software.ps1`: official-source resolution, security checks, silent install orchestration, test seam, and existing version output.
- Create `scripts/windows-a11y/tests/install-software.Tests.ps1`: Pester behavior tests for official URLs, stable-release filtering, retries, signatures, installer commands, and output compatibility.
- Modify `docs/windows-a11y-aws-manual-setup.md`: replace the obsolete Chocolatey outbound-network description with the three official publishers.

### Task 1: Official artifact resolution and validation helpers

**Files:**
- Modify: `scripts/windows-a11y/install-software.ps1:1-87`
- Create: `scripts/windows-a11y/tests/install-software.Tests.ps1`

**Interfaces:**
- Produces: `Get-FirefoxInstallerUri() -> [Uri]`
- Produces: `Get-NvdaStableInstallerUri([string] $Content, [Uri] $BaseUri) -> [Uri]`
- Produces: `Invoke-WebRequestWithRetry([Uri] $Uri, [string] $OutFile, [int] $MaxAttempts = 3) -> response or void`
- Produces: `Assert-AuthenticodePublisher([string] $Path, [string] $ProductName, [string] $PublisherPattern) -> void`
- Produces: `Assert-InstallerExitCode([string] $ProductName, [int] $ExitCode) -> void`

- [ ] **Step 1: Add failing resolver and validation tests**

Create `scripts/windows-a11y/tests/install-software.Tests.ps1` with these initial tests:

```powershell
BeforeAll {
    if (-not (Get-Command Get-AuthenticodeSignature -ErrorAction SilentlyContinue)) {
        function global:Get-AuthenticodeSignature {
            param([string]$FilePath)
            throw "Get-AuthenticodeSignature must be mocked on non-Windows hosts: $FilePath"
        }
    }
    . (Join-Path $PSScriptRoot '..\install-software.ps1') -SkipExecution
}

Describe 'official stable installer resolution' {
    It 'returns the Mozilla latest stable zh-TW win64 endpoint' {
        (Get-FirefoxInstallerUri).AbsoluteUri | Should -BeExactly `
            'https://download.mozilla.org/?product=firefox-latest-ssl&os=win64&lang=zh-TW'
    }

    It 'accepts one numeric NVDA stable patch release' {
        $content = '<a href="nvda_2026.1.1.exe">nvda_2026.1.1.exe</a>'
        $uri = Get-NvdaStableInstallerUri -Content $content
        $uri.AbsoluteUri | Should -BeExactly `
            'https://download.nvaccess.org/releases/stable/nvda_2026.1.1.exe'
    }

    It 'rejects NVDA beta and release-candidate installers' {
        $content = @'
<a href="nvda_2026.2beta7.exe">beta</a>
<a href="nvda_2026.2rc1.exe">rc</a>
'@
        { Get-NvdaStableInstallerUri -Content $content } |
            Should -Throw '*exactly one numeric stable NVDA installer*'
    }

    It 'rejects an ambiguous stable listing' {
        $content = @'
<a href="nvda_2026.1.exe">first</a>
<a href="nvda_2026.1.1.exe">second</a>
'@
        { Get-NvdaStableInstallerUri -Content $content } |
            Should -Throw '*found 2*'
    }
}

Describe 'publisher and exit-code validation' {
    It 'accepts a valid expected publisher' {
        Mock Get-AuthenticodeSignature {
            [pscustomobject]@{
                Status = 'Valid'
                SignerCertificate = [pscustomobject]@{
                    Subject = 'CN=Mozilla Corporation, O=Mozilla Corporation, C=US'
                }
            }
        }
        { Assert-AuthenticodePublisher -Path 'firefox.exe' -ProductName 'Firefox' `
                -PublisherPattern '(^|, )O=Mozilla Corporation(,|$)' } |
            Should -Not -Throw
    }

    It 'rejects an unexpected publisher even when the signature is valid' {
        Mock Get-AuthenticodeSignature {
            [pscustomobject]@{
                Status = 'Valid'
                SignerCertificate = [pscustomobject]@{ Subject = 'CN=Unexpected, O=Unexpected, C=US' }
            }
        }
        { Assert-AuthenticodePublisher -Path 'nvda.exe' -ProductName 'NVDA' `
                -PublisherPattern '(^|, )O=NV Access Limited(,|$)' } |
            Should -Throw '*signature verification failed*'
    }

    It 'rejects an invalid signature from the expected publisher' {
        Mock Get-AuthenticodeSignature {
            [pscustomobject]@{
                Status = 'HashMismatch'
                SignerCertificate = [pscustomobject]@{
                    Subject = 'CN=NV Access Limited, O=NV Access Limited, C=AU'
                }
            }
        }
        { Assert-AuthenticodePublisher -Path 'nvda.exe' -ProductName 'NVDA' `
                -PublisherPattern '(^|, )O=NV Access Limited(,|$)' } |
            Should -Throw '*signature verification failed*'
    }

    It 'rejects a nonzero executable installer exit code' {
        { Assert-InstallerExitCode -ProductName 'Firefox' -ExitCode 1 } |
            Should -Throw '*exit code 1*'
    }
}

Describe 'bounded official download retries' {
    It 'retries twice and succeeds on the third attempt' {
        $script:attempt = 0
        Mock Invoke-WebRequest {
            $script:attempt++
            if ($script:attempt -lt 3) { throw 'temporary failure' }
        }
        Mock Start-Sleep

        Invoke-WebRequestWithRetry -Uri 'https://download.mozilla.org/example.exe' `
            -OutFile "$TestDrive\example.exe"

        Should -Invoke Invoke-WebRequest -Times 3 -Exactly
        Should -Invoke Start-Sleep -Times 1 -ParameterFilter { $Seconds -eq 15 }
        Should -Invoke Start-Sleep -Times 1 -ParameterFilter { $Seconds -eq 30 }
    }
}
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```powershell
pwsh -NoProfile -Command "Invoke-Pester -Path './scripts/windows-a11y/tests/install-software.Tests.ps1' -Output Detailed"
```

Expected: FAIL because `install-software.ps1` has no `SkipExecution` parameter and the resolver, retry, and validation functions do not exist. This workstation currently has neither PowerShell nor Pester. With user approval, install them using `brew install --cask powershell`, followed by `pwsh -NoProfile -Command "Install-Module Pester -MinimumVersion 5.5.0 -Scope CurrentUser -Force"`; do not substitute text-matching tests.

- [ ] **Step 3: Add the test seam and minimal helper implementations**

Change the script parameter and replace the Chocolatey bootstrap/helper with these behaviors:

```powershell
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
```

Implement the retry helper without catching validation or installer errors:

```powershell
function Invoke-WebRequestWithRetry {
    param(
        [Parameter(Mandatory = $true)][Uri]$Uri,
        [string]$OutFile,
        [int]$MaxAttempts = 3
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
```

- [ ] **Step 4: Run tests and verify GREEN**

Run the same `Invoke-Pester` command. Expected: all Task 1 tests PASS with no warnings.

- [ ] **Step 5: Commit Task 1**

```bash
git add scripts/windows-a11y/install-software.ps1 scripts/windows-a11y/tests/install-software.Tests.ps1
git commit -m "test: cover official Windows installer sources" -m "Co-authored-by: Codex <codex@openai.com>"
```

### Task 2: Install Firefox and NVDA from official sources

**Files:**
- Modify: `scripts/windows-a11y/install-software.ps1:18-156`
- Modify: `scripts/windows-a11y/tests/install-software.Tests.ps1`

**Interfaces:**
- Consumes: all Task 1 resolver, retry, signature, and exit-code helpers.
- Produces: `Install-Firefox() -> void`
- Produces: `Install-Nvda() -> void`
- Produces: `Write-InstalledSoftwareVersions([Collections.IDictionary] $SoftwareExecutables) -> void`
- Produces: `Invoke-InstallSoftware() -> void`, called only when `-SkipExecution` is absent.

- [ ] **Step 1: Add failing download retry and product orchestration tests**

Append tests that mock network/process boundaries but assert the real orchestration contract:

```powershell
Describe 'official product installers' {
    BeforeEach {
        Mock New-Item
        Mock Remove-Item
        Mock Invoke-WebRequestWithRetry
        Mock Assert-AuthenticodePublisher
        Mock Start-Process { [pscustomobject]@{ ExitCode = 0 } }
        Mock Assert-InstallerExitCode
    }

    It 'downloads and silently installs Firefox zh-TW win64' {
        Install-Firefox
        Should -Invoke Invoke-WebRequestWithRetry -Times 1 -ParameterFilter {
            $Uri.AbsoluteUri -eq 'https://download.mozilla.org/?product=firefox-latest-ssl&os=win64&lang=zh-TW'
        }
        Should -Invoke Assert-AuthenticodePublisher -Times 1 -ParameterFilter {
            $ProductName -eq 'Firefox' -and
            $PublisherPattern -eq '(^|, )O=Mozilla Corporation(,|$)'
        }
        Should -Invoke Start-Process -Times 1 -ParameterFilter {
            $ArgumentList.Count -eq 1 -and $ArgumentList[0] -eq '/S' -and $Wait -and $PassThru
        }
    }

    It 'resolves and silently installs the official stable NVDA build' {
        Mock Invoke-WebRequestWithRetry {
            [pscustomobject]@{ Content = '<a href="nvda_2026.1.1.exe">download</a>' }
        } -ParameterFilter { -not $OutFile }

        Install-Nvda
        Should -Invoke Invoke-WebRequestWithRetry -Times 1 -ParameterFilter {
            $Uri.AbsoluteUri -eq 'https://download.nvaccess.org/releases/stable/' -and -not $OutFile
        }
        Should -Invoke Assert-AuthenticodePublisher -Times 1 -ParameterFilter {
            $ProductName -eq 'NVDA' -and
            $PublisherPattern -eq '(^|, )O=NV Access Limited(,|$)'
        }
        Should -Invoke Start-Process -Times 1 -ParameterFilter {
            $ArgumentList.Count -eq 1 -and $ArgumentList[0] -eq '--install-silent' -and $Wait -and $PassThru
        }
    }
}
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run the focused Pester command. Expected: the new product tests fail because `Install-Firefox` and `Install-Nvda` do not exist.

- [ ] **Step 3: Implement the minimal official installers**

Implement both functions using the shared temporary directory:

```powershell
function Install-Firefox {
    $downloadDirectory = Join-Path $env:TEMP 'windows-a11y-installers'
    $installerPath = Join-Path $downloadDirectory 'firefox-zh-TW-win64-latest.exe'
    New-Item -ItemType Directory -Path $downloadDirectory -Force | Out-Null
    Invoke-WebRequestWithRetry -Uri (Get-FirefoxInstallerUri) -OutFile $installerPath
    Assert-AuthenticodePublisher -Path $installerPath -ProductName 'Firefox' `
        -PublisherPattern '(^|, )O=Mozilla Corporation(,|$)'
    $process = Start-Process -FilePath $installerPath -ArgumentList @('/S') -Wait -PassThru
    Assert-InstallerExitCode -ProductName 'Firefox' -ExitCode $process.ExitCode
    Remove-Item -LiteralPath $installerPath -Force
}

function Install-Nvda {
    $downloadDirectory = Join-Path $env:TEMP 'windows-a11y-installers'
    New-Item -ItemType Directory -Path $downloadDirectory -Force | Out-Null
    $listing = Invoke-WebRequestWithRetry -Uri $nvdaStableBaseUri
    $installerUri = Get-NvdaStableInstallerUri -Content $listing.Content
    $installerPath = Join-Path $downloadDirectory ([IO.Path]::GetFileName($installerUri.AbsolutePath))
    Invoke-WebRequestWithRetry -Uri $installerUri -OutFile $installerPath
    Assert-AuthenticodePublisher -Path $installerPath -ProductName 'NVDA' `
        -PublisherPattern '(^|, )O=NV Access Limited(,|$)'
    $process = Start-Process -FilePath $installerPath -ArgumentList @('--install-silent') -Wait -PassThru
    Assert-InstallerExitCode -ProductName 'NVDA' -ExitCode $process.ExitCode
    Remove-Item -LiteralPath $installerPath -Force
}
```

Keep `Install-GoogleChrome` behavior unchanged. Replace the Chocolatey package loop with `Install-Firefox`, `Install-Nvda`, and `Install-GoogleChrome` calls inside `Invoke-InstallSoftware`.

- [ ] **Step 4: Add a failing version-output compatibility test**

Append this test, then run it and verify RED because the version loop is not yet extracted:

```powershell
Describe 'workflow version output contract' {
    It 'emits the three existing VERSION keys' {
        Mock Test-Path { $true }
        Mock Get-Item {
            [pscustomobject]@{
                VersionInfo = [pscustomobject]@{
                    ProductVersion = '1.2.3'
                    FileVersion = '1.2.3.0'
                }
            }
        }
        $executables = [ordered]@{
            GOOGLECHROME = 'C:\Google\chrome.exe'
            FIREFOX = 'C:\Mozilla Firefox\firefox.exe'
            NVDA = 'C:\NVDA\nvda.exe'
        }

        $output = @(Write-InstalledSoftwareVersions -SoftwareExecutables $executables)

        $output | Should -Contain 'VERSION_GOOGLECHROME=1.2.3'
        $output | Should -Contain 'VERSION_FIREFOX=1.2.3'
        $output | Should -Contain 'VERSION_NVDA=1.2.3'
        @($output | Where-Object { $_ -like 'VERSION_*=*' }).Count | Should -Be 3
    }
}
```

- [ ] **Step 5: Extract version output and guard execution**

Move the existing executable checks/version loop into this function:

```powershell
function Write-InstalledSoftwareVersions {
    param(
        [Parameter(Mandatory = $true)]
        [Collections.IDictionary]$SoftwareExecutables
    )
    foreach ($software in $SoftwareExecutables.GetEnumerator()) {
        if (-not (Test-Path $software.Value)) {
            throw "$logPrefix $($software.Key) executable was not found at $($software.Value)"
        }
        $versionInfo = (Get-Item $software.Value).VersionInfo
        $version = if ($versionInfo.ProductVersion) {
            $versionInfo.ProductVersion
        } else {
            $versionInfo.FileVersion
        }
        Write-Output "VERSION_$($software.Key)=$version"
    }
}
```

In `Invoke-InstallSoftware`, preserve the existing Chrome wait/log-tail behavior and build the same ordered dictionary:

```powershell
function Invoke-InstallSoftware {
    Install-Firefox
    Install-Nvda
    Install-GoogleChrome

    Write-Output "$logPrefix Installed package versions:"
    $chromeExecutable = Wait-GoogleChromeExecutable
    if (-not $chromeExecutable) {
        $installerLogPath = Join-Path $env:TEMP 'windows-a11y-installers\googlechrome-install.log'
        $logTail = (Get-Content -LiteralPath $installerLogPath -Tail 40 `
            -ErrorAction SilentlyContinue) -join [Environment]::NewLine
        throw "$logPrefix GOOGLECHROME executable was not found after waiting for the MSI installation to settle. MSI log: $installerLogPath`n$logTail"
    }
    $softwareExecutables = [ordered]@{
        GOOGLECHROME = $chromeExecutable
        FIREFOX = 'C:\Program Files\Mozilla Firefox\firefox.exe'
        NVDA = 'C:\Program Files (x86)\NVDA\nvda.exe'
    }
    Write-InstalledSoftwareVersions -SoftwareExecutables $softwareExecutables
}
```

End the script with:

```powershell
if (-not $SkipExecution) {
    Invoke-InstallSoftware
}
```

Delete the Chocolatey bootstrap, `$chocoPath`, `$successfulExitCodes`, `Install-OrUpgradePackage`, and the `@('firefox', 'nvda')` loop.

- [ ] **Step 6: Run the complete Pester file and verify GREEN**

Run:

```powershell
pwsh -NoProfile -Command "Invoke-Pester -Path './scripts/windows-a11y/tests/install-software.Tests.ps1' -Output Detailed"
```

Expected: all tests PASS, with no download or installer launched because all external boundaries are mocked.

- [ ] **Step 7: Commit Task 2**

```bash
git add scripts/windows-a11y/install-software.ps1 scripts/windows-a11y/tests/install-software.Tests.ps1
git commit -m "feat: install official Firefox and NVDA releases" -m "Co-authored-by: Codex <codex@openai.com>"
```

### Task 3: Documentation and end-to-end static verification

**Files:**
- Modify: `docs/windows-a11y-aws-manual-setup.md:27-29`
- Verify: `.github/workflows/build-windows-a11y-ami.yml:94-101`
- Verify: `scripts/windows-a11y/verify-environment.ps1:30-33`

**Interfaces:**
- Consumes: unchanged `VERSION_*` output and executable paths from Task 2.
- Produces: operator documentation that names the actual official outbound services.

- [ ] **Step 1: Update the outbound-network documentation**

Change the security-group explanation from “Windows Update, Chocolatey, and the SSM agent” to “Windows Update, Google, Mozilla, NV Access, and the SSM agent.” Do not change AWS setup instructions or variables.

- [ ] **Step 2: Run all behavioral and syntax verification**

Run the complete Pester file again, then parse the production script:

```powershell
pwsh -NoProfile -Command "Invoke-Pester -Path './scripts/windows-a11y/tests/install-software.Tests.ps1' -Output Detailed"
pwsh -NoProfile -Command '$errors = $null; [void][System.Management.Automation.Language.Parser]::ParseFile((Resolve-Path "./scripts/windows-a11y/install-software.ps1"), [ref]$null, [ref]$errors); if ($errors.Count) { $errors | Out-String | Write-Error; exit 1 }'
```

Expected: Pester reports zero failed tests and the parser command exits `0` without errors.

- [ ] **Step 3: Verify scope and compatibility from the repository root**

```bash
rg -n "choco|chocolatey|Install-OrUpgradePackage" scripts/windows-a11y/install-software.ps1
rg -n "firefox-latest-ssl.*os=win64.*lang=zh-TW|download\.nvaccess\.org/releases/stable" scripts/windows-a11y/install-software.ps1
rg -n "VERSION_GOOGLECHROME|VERSION_FIREFOX|VERSION_NVDA" scripts/windows-a11y/install-software.ps1 .github/workflows/build-windows-a11y-ami.yml
git diff --check
git status --short
```

Expected: the first command has no matches; the official endpoints and all three version keys are present; `git diff --check` exits `0`; only the intended script, test, and documentation files are changed.

- [ ] **Step 4: Commit Task 3**

```bash
git add docs/windows-a11y-aws-manual-setup.md
git commit -m "docs: describe official Windows software sources" -m "Co-authored-by: Codex <codex@openai.com>"
```

- [ ] **Step 5: Perform final verification before reporting completion**

Run the full Pester suite and PowerShell parser command fresh after all commits, then run `git status --short` and `git log -5 --oneline`. Report the exact test count, parser exit status, commits, and any limitation that the real installers were not executed outside an AMI build. Do not claim the live AMI build succeeds until the GitHub Actions workflow has actually built and verified an AMI.
