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
