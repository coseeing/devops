Describe 'Windows Update workflow runner' {
    BeforeEach {
        $script:originalPath = $env:PATH
        $script:runnerPath = Join-Path $PSScriptRoot '..\run-windows-updates.sh'
        $script:ssmStub = Join-Path $TestDrive 'ssm-stub.sh'
        $script:githubEnv = Join-Path $TestDrive 'github-env'
        Set-Content -LiteralPath $script:ssmStub -Value @'
#!/usr/bin/env bash
echo "[install-updates] Update result: Failed update; code: 4; HRESULT: 0x80240017"
exit 7
'@
        Set-Content -LiteralPath $script:githubEnv -Value '' -NoNewline
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

    It 'retries a transient Windows Update failure and records success after recovery' {
        $stateFile = Join-Path $TestDrive 'retry-attempts'
        Set-Content -LiteralPath $script:ssmStub -Value @'
#!/usr/bin/env bash
attempt=0
if [[ -f "${SSM_STUB_STATE_FILE}" ]]; then
  attempt=$(cat "${SSM_STUB_STATE_FILE}")
fi
attempt=$((attempt + 1))
printf '%s' "${attempt}" > "${SSM_STUB_STATE_FILE}"
if (( attempt == 1 )); then
  echo "[install-updates] COM failure during search; HRESULT: 0x8007045B"
  echo "TRANSIENT_WINDOWS_UPDATE_ERROR=true"
  exit 7
fi
echo "[install-updates] No updates found."
echo "REBOOT_REQUIRED=false"
'@
        $env:SSM_RUN_SCRIPT = $script:ssmStub
        $env:SSM_STUB_STATE_FILE = $stateFile
        $env:WINDOWS_UPDATE_RETRY_DELAY_SECONDS = '0'
        $env:GITHUB_ENV = $script:githubEnv

        $output = @(& bash $script:runnerPath 'i-test' 2>&1)
        $exitCode = $LASTEXITCODE

        $exitCode | Should -Be 0
        (Get-Content -LiteralPath $stateFile -Raw) | Should -Be '2'
        $output | Should -Contain 'Transient Windows Update failure; retrying pass 1 (retry 1/10)...'
        (Get-Content -LiteralPath $script:githubEnv -Raw) | Should -Match '^WINDOWS_UPDATE_DATE=\d{4}-\d{2}-\d{2}\s*$'
    }

    It 'stops after the configured transient retry limit without recording success' {
        $stateFile = Join-Path $TestDrive 'limit-attempts'
        Set-Content -LiteralPath $script:ssmStub -Value @'
#!/usr/bin/env bash
attempt=0
if [[ -f "${SSM_STUB_STATE_FILE}" ]]; then
  attempt=$(cat "${SSM_STUB_STATE_FILE}")
fi
attempt=$((attempt + 1))
printf '%s' "${attempt}" > "${SSM_STUB_STATE_FILE}"
echo "[install-updates] COM failure during search; HRESULT: 0x8024001E"
echo "TRANSIENT_WINDOWS_UPDATE_ERROR=true"
exit 9
'@
        $env:SSM_RUN_SCRIPT = $script:ssmStub
        $env:SSM_STUB_STATE_FILE = $stateFile
        $env:WINDOWS_UPDATE_TRANSIENT_RETRIES = '2'
        $env:WINDOWS_UPDATE_RETRY_DELAY_SECONDS = '0'
        $env:GITHUB_ENV = $script:githubEnv

        $output = @(& bash $script:runnerPath 'i-test' 2>&1)
        $exitCode = $LASTEXITCODE

        $exitCode | Should -Be 9
        (Get-Content -LiteralPath $stateFile -Raw) | Should -Be '3'
        ($output -join "`n") | Should -Match 'Transient Windows Update retry limit reached for pass 1\.'
        (Get-Content -LiteralPath $script:githubEnv -Raw) | Should -BeNullOrEmpty
    }

    It 'waits for SSM to report Online after reboot before starting the next pass' {
        $binDir = Join-Path $TestDrive 'bin'
        $ssmStateFile = Join-Path $TestDrive 'reboot-ssm-attempts'
        $awsStateFile = Join-Path $TestDrive 'ssm-readiness-attempts'
        New-Item -ItemType Directory -Path $binDir | Out-Null
        Set-Content -LiteralPath $script:ssmStub -Value @'
#!/usr/bin/env bash
attempt=0
if [[ -f "${SSM_STUB_STATE_FILE}" ]]; then
  attempt=$(cat "${SSM_STUB_STATE_FILE}")
fi
attempt=$((attempt + 1))
printf '%s' "${attempt}" > "${SSM_STUB_STATE_FILE}"
if (( attempt == 1 )); then
  echo "[install-updates] Install result code: 2; HRESULT: 0x00000000"
  echo "REBOOT_REQUIRED=true"
else
  echo "[install-updates] No updates found."
  echo "REBOOT_REQUIRED=false"
fi
'@
        Set-Content -LiteralPath (Join-Path $binDir 'aws') -Value @'
#!/usr/bin/env bash
if [[ "$1 $2" == "ssm describe-instance-information" ]]; then
  attempt=0
  if [[ -f "${AWS_STUB_STATE_FILE}" ]]; then
    attempt=$(cat "${AWS_STUB_STATE_FILE}")
  fi
  attempt=$((attempt + 1))
  printf '%s' "${attempt}" > "${AWS_STUB_STATE_FILE}"
  if (( attempt == 1 )); then
    echo "ConnectionLost"
  else
    echo "Online"
  fi
fi
'@
        Set-Content -LiteralPath (Join-Path $binDir 'sleep') -Value @'
#!/usr/bin/env bash
exit 0
'@
        & chmod +x (Join-Path $binDir 'aws') (Join-Path $binDir 'sleep')
        $env:PATH = "$binDir$([IO.Path]::PathSeparator)$($env:PATH)"
        $env:SSM_RUN_SCRIPT = $script:ssmStub
        $env:SSM_STUB_STATE_FILE = $ssmStateFile
        $env:AWS_STUB_STATE_FILE = $awsStateFile
        $env:WINDOWS_UPDATE_SSM_POLL_SECONDS = '0'
        $env:GITHUB_ENV = $script:githubEnv

        $output = @(& bash $script:runnerPath 'i-test' 2>&1)
        $exitCode = $LASTEXITCODE

        $exitCode | Should -Be 0
        (Get-Content -LiteralPath $ssmStateFile -Raw) | Should -Be '2'
        (Get-Content -LiteralPath $awsStateFile -Raw) | Should -Be '2'
        $output | Should -Contain 'Waiting for SSM Agent to report Online (attempt 1/60; status: ConnectionLost)...'
    }

    AfterEach {
        $env:PATH = $script:originalPath
        Remove-Item Env:SSM_RUN_SCRIPT -ErrorAction Ignore
        Remove-Item Env:SSM_STUB_STATE_FILE -ErrorAction Ignore
        Remove-Item Env:AWS_STUB_STATE_FILE -ErrorAction Ignore
        Remove-Item Env:WINDOWS_UPDATE_TRANSIENT_RETRIES -ErrorAction Ignore
        Remove-Item Env:WINDOWS_UPDATE_RETRY_DELAY_SECONDS -ErrorAction Ignore
        Remove-Item Env:WINDOWS_UPDATE_SSM_POLL_SECONDS -ErrorAction Ignore
        Remove-Item Env:GITHUB_ENV -ErrorAction Ignore
    }
}
