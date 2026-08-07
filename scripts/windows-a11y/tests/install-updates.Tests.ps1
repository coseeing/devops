Describe 'Windows Update installation diagnostics' {
    BeforeAll {
        $scriptPath = Join-Path $PSScriptRoot '..\install-updates.ps1'
        $tokens = $null
        $errors = $null
        $ast = [System.Management.Automation.Language.Parser]::ParseFile(
            $scriptPath,
            [ref]$tokens,
            [ref]$errors
        )
        $functionAsts = $ast.FindAll({
                param($node)
                $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
                $node.Name -in @('ConvertTo-HResultHex', 'Write-InstallationDiagnostics')
            }, $true)

        @($functionAsts).Count | Should -Be 2
        foreach ($functionAst in $functionAsts) {
            Invoke-Expression $functionAst.Extent.Text
        }
    }

    It 'reports aggregate and per-update result codes and HRESULT values' {
        $firstResult = [pscustomobject]@{
            ResultCode = 2
            HResult = 0
            RebootRequired = $false
        }
        $secondResult = [pscustomobject]@{
            ResultCode = 4
            HResult = -2145124329
            RebootRequired = $true
        }
        $installResult = [pscustomobject]@{
            ResultCode = 4
            HResult = -2145124329
            RebootRequired = $true
            UpdateResults = @($firstResult, $secondResult)
        }
        $installResult | Add-Member -MemberType ScriptMethod -Name GetUpdateResult -Value {
            param($index)
            $this.UpdateResults[$index]
        }
        $updates = @(
            [pscustomobject]@{ Title = 'Successful update' },
            [pscustomobject]@{ Title = 'Failed update' }
        )

        $output = @(Write-InstallationDiagnostics -InstallResult $installResult -Updates $updates)

        $output | Should -Contain '[install-updates] Install result code: 4; HRESULT: 0x80240017'
        $output | Should -Contain '[install-updates] Update result: Successful update; code: 2; HRESULT: 0x00000000; reboot required: False'
        $output | Should -Contain '[install-updates] Update result: Failed update; code: 4; HRESULT: 0x80240017; reboot required: True'
    }
}
