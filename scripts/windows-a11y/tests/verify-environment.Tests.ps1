BeforeAll {
    . (Join-Path $PSScriptRoot '..\verify-environment.ps1') -SkipExecution
}

Describe 'NVDA verification executable resolution' {
    It 'prefers the current 64-bit NVDA executable path' {
        Mock Test-Path { $true } -ParameterFilter { $LiteralPath -eq 'C:\Program Files\NVDA\nvda.exe' }
        Mock Test-Path { $true } -ParameterFilter { $LiteralPath -eq 'C:\Program Files (x86)\NVDA\nvda.exe' }

        Find-NvdaExecutable | Should -BeExactly 'C:\Program Files\NVDA\nvda.exe'
    }

    It 'falls back to the legacy x86 NVDA executable path' {
        Mock Test-Path { $false } -ParameterFilter { $LiteralPath -eq 'C:\Program Files\NVDA\nvda.exe' }
        Mock Test-Path { $true } -ParameterFilter { $LiteralPath -eq 'C:\Program Files (x86)\NVDA\nvda.exe' }

        Find-NvdaExecutable | Should -BeExactly 'C:\Program Files (x86)\NVDA\nvda.exe'
    }
}
