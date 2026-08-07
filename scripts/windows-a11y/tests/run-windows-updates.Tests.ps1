Describe 'Windows Update workflow runner' {
    BeforeEach {
        $script:runnerPath = Join-Path $PSScriptRoot '..\run-windows-updates.sh'
        $script:ssmStub = Join-Path $TestDrive 'ssm-stub.sh'
        $script:githubEnv = Join-Path $TestDrive 'github-env'
        Set-Content -LiteralPath $script:ssmStub -Value @'
#!/usr/bin/env bash
echo "[install-updates] Update result: Failed update; code: 4; HRESULT: 0x80240017"
exit 7
'@
        New-Item -ItemType File -Path $script:githubEnv | Out-Null
    }

    It 'prints captured SSM stdout and preserves a failed exit status' {
        $env:SSM_RUN_SCRIPT = $script:ssmStub
        $env:GITHUB_ENV = $script:githubEnv

        $output = @(& bash $script:runnerPath 'i-test' 2>&1)
        $exitCode = $LASTEXITCODE

        $exitCode | Should -Be 7
        $output | Should -Contain '[install-updates] Update result: Failed update; code: 4; HRESULT: 0x80240017'
        (Get-Content -LiteralPath $script:githubEnv -Raw) | Should -BeNullOrEmpty
    }
}
