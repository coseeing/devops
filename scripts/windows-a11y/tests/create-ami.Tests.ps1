Describe 'AMI creation polling' {
    BeforeEach {
        $script:runnerPath = Join-Path $PSScriptRoot '..\create-ami.sh'
        $script:binDirectory = Join-Path $TestDrive 'bin'
        $script:stateFile = Join-Path $TestDrive 'describe-count'
        $script:githubOutput = Join-Path $TestDrive 'github-output'
        $script:originalPath = $env:PATH

        New-Item -ItemType Directory -Path $script:binDirectory -Force | Out-Null
        Remove-Item -LiteralPath $script:stateFile -Force -ErrorAction SilentlyContinue
        Set-Content -LiteralPath $script:githubOutput -Value '' -NoNewline -Force

        $awsStub = Join-Path $script:binDirectory 'aws'
        Set-Content -LiteralPath $awsStub -Value @'
#!/usr/bin/env bash
set -euo pipefail

if [[ "$1 $2" == "ec2 create-image" ]]; then
  echo "ami-test123"
  exit 0
fi

if [[ "$1 $2" == "ec2 describe-images" ]]; then
  count=0
  if [[ -f "${AWS_STUB_STATE_FILE}" ]]; then
    count=$(<"${AWS_STUB_STATE_FILE}")
  fi
  count=$((count + 1))
  printf '%s' "${count}" > "${AWS_STUB_STATE_FILE}"

  if [[ "${AWS_STUB_MODE}" == "not-found-then-available" && "${count}" == "1" ]]; then
    echo "An error occurred (InvalidAMIID.NotFound) when calling DescribeImages" >&2
    exit 255
  elif [[ "${AWS_STUB_MODE}" == "failed" ]]; then
    printf '%s\n' '{"Images":[{"ImageId":"ami-test123","State":"failed","StateReason":{"Message":"snapshot failed"}}]}'
  elif (( count >= AWS_STUB_AVAILABLE_AFTER )); then
    printf '%s\n' '{"Images":[{"ImageId":"ami-test123","State":"available"}]}'
  else
    printf '%s\n' '{"Images":[{"ImageId":"ami-test123","State":"pending"}]}'
  fi
  exit 0
fi

echo "unexpected aws invocation: $*" >&2
exit 64
'@
        & chmod +x $awsStub

        $env:PATH = "$script:binDirectory$([IO.Path]::PathSeparator)$script:originalPath"
        $env:GITHUB_OUTPUT = $script:githubOutput
        $env:AWS_STUB_STATE_FILE = $script:stateFile
        $env:AMI_POLL_INTERVAL_SECONDS = '0'
        Remove-Item Env:AMI_MAX_ATTEMPTS -ErrorAction SilentlyContinue
    }

    AfterEach {
        $env:PATH = $script:originalPath
        Remove-Item Env:GITHUB_OUTPUT -ErrorAction SilentlyContinue
        Remove-Item Env:AWS_STUB_STATE_FILE -ErrorAction SilentlyContinue
        Remove-Item Env:AWS_STUB_MODE -ErrorAction SilentlyContinue
        Remove-Item Env:AWS_STUB_AVAILABLE_AFTER -ErrorAction SilentlyContinue
        Remove-Item Env:AMI_POLL_INTERVAL_SECONDS -ErrorAction SilentlyContinue
        Remove-Item Env:AMI_MAX_ATTEMPTS -ErrorAction SilentlyContinue
    }

    It 'continues polling beyond forty checks until the AMI is available' {
        $env:AWS_STUB_MODE = 'available'
        $env:AWS_STUB_AVAILABLE_AFTER = '41'

        $output = @(& bash $script:runnerPath 'i-test' 'windows-a11y-test' 2>&1)
        $exitCode = $LASTEXITCODE

        $exitCode | Should -Be 0
        $output | Should -Contain '[create-ami] Created AMI ami-test123.'
        $output | Should -Contain '[create-ami] AMI ami-test123 state: pending (attempt 40 of 120).'
        $output | Should -Contain '[create-ami] AMI ami-test123 state: available (attempt 41 of 120).'
        (Get-Content -LiteralPath $script:stateFile -Raw) | Should -BeExactly '41'
        (Get-Content -LiteralPath $script:githubOutput -Raw).Trim() |
            Should -BeExactly 'ami_id=ami-test123'
    }

    It 'retries when a newly created AMI is not visible yet' {
        $env:AWS_STUB_MODE = 'not-found-then-available'
        $env:AWS_STUB_AVAILABLE_AFTER = '2'

        $output = @(& bash $script:runnerPath 'i-test' 'windows-a11y-test' 2>&1)
        $exitCode = $LASTEXITCODE

        $exitCode | Should -Be 0
        $output | Should -Contain '[create-ami] AMI ami-test123 is not visible yet (attempt 1 of 120).'
        $output | Should -Contain '[create-ami] AMI ami-test123 state: available (attempt 2 of 120).'
        (Get-Content -LiteralPath $script:stateFile -Raw) | Should -BeExactly '2'
    }

    It 'fails immediately and reports StateReason when the AMI enters failed state' {
        $env:AWS_STUB_MODE = 'failed'
        $env:AWS_STUB_AVAILABLE_AFTER = '999'

        $output = @(& bash $script:runnerPath 'i-test' 'windows-a11y-test' 2>&1)
        $exitCode = $LASTEXITCODE

        $exitCode | Should -Be 1
        $output | Should -Contain '[create-ami] AMI ami-test123 state: failed (attempt 1 of 120); reason: snapshot failed.'
        (Get-Content -LiteralPath $script:stateFile -Raw) | Should -BeExactly '1'
    }
}
