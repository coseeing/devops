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
                $node.Name -in @(
                    'ConvertTo-HResultHex',
                    'Test-TransientWindowsUpdateHResult',
                    'Write-WindowsUpdateComExceptionDiagnostics',
                    'Invoke-WindowsUpdateComOperation',
                    'Write-InstallationDiagnostics'
                )
            }, $true)

        @($functionAsts).Count | Should -Be 5
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

    It 'marks shutdown-in-progress COM exceptions as transient using a numeric HRESULT' {
        $exception = [System.Runtime.InteropServices.COMException]::new(
            'A system shutdown is in progress.',
            [int]0x8007045B
        )

        $output = @(& {
                Write-WindowsUpdateComExceptionDiagnostics -Operation 'search' -Exception $exception
            } 6>&1).ForEach({ $_.ToString() })

        $output | Should -Contain '[install-updates] COM failure during search; HRESULT: 0x8007045B'
        $output | Should -Contain 'TRANSIENT_WINDOWS_UPDATE_ERROR=true'
    }

    It 'emits transient diagnostics outside an operation result assignment' {
        $output = @(& {
                try {
                    $ignored = Invoke-WindowsUpdateComOperation -Operation 'search' -Action {
                        throw [System.Runtime.InteropServices.COMException]::new(
                            'A system shutdown is in progress.',
                            [int]0x8007045B
                        )
                    }
                } catch {
                    # The production script lets the original COM exception terminate the SSM command.
                }
            } 6>&1)

        @($output.ForEach({ $_.ToString() })) | Should -Contain '[install-updates] COM failure during search; HRESULT: 0x8007045B'
        @($output.ForEach({ $_.ToString() })) | Should -Contain 'TRANSIENT_WINDOWS_UPDATE_ERROR=true'
    }

    It 'marks a transient per-update HRESULT so the runner can retry the pass' {
        $updateResult = [pscustomobject]@{
            ResultCode = 4
            HResult = [int]0x80240016
            RebootRequired = $true
        }
        $installResult = [pscustomobject]@{
            ResultCode = 4
            HResult = [int]0x80240022
            RebootRequired = $true
            UpdateResults = @($updateResult)
        }
        $installResult | Add-Member -MemberType ScriptMethod -Name GetUpdateResult -Value {
            param($index)
            $this.UpdateResults[$index]
        }

        $output = @(Write-InstallationDiagnostics `
                -InstallResult $installResult `
                -Updates @([pscustomobject]@{ Title = 'Busy update' }))

        $output | Should -Contain 'TRANSIENT_WINDOWS_UPDATE_ERROR=true'
    }
}
