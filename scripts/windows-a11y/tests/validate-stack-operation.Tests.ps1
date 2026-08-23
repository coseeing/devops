Describe 'Windows A11y stack operation validation' {
    BeforeAll {
        $script:validatorPath = Join-Path $PSScriptRoot '..\validate-stack-operation.sh'
    }

    It 'builds the prefixed stack name for a launch request' {
        $output = @(& bash $script:validatorPath 'launch' 'anson-test' '' '1' 'm5.xlarge' '100' 2>&1)
        $exitCode = $LASTEXITCODE

        $exitCode | Should -Be 0
        $output | Should -Be @('windows-a11y-anson-test')
    }

    It 'accepts batch boundaries for launch' -ForEach @(
        @{ Count = '1' }
        @{ Count = '20' }
    ) {
        $output = @(& bash $script:validatorPath 'launch' 'anson-test' '' $Count 'm5.xlarge' '100' 2>&1)

        $LASTEXITCODE | Should -Be 0
        $output | Should -Be @('windows-a11y-anson-test')
    }

    It 'rejects invalid launch counts' -ForEach @(
        @{ Count = '0' }
        @{ Count = '21' }
        @{ Count = '1.5' }
        @{ Count = 'many' }
        @{ Count = '' }
    ) {
        $output = @(& bash $script:validatorPath 'launch' 'anson-test' '' $Count 'm5.xlarge' '100' 2>&1)

        $LASTEXITCODE | Should -Be 1
        ($output -join "`n") | Should -Match 'Instance count must be an integer from 1 through 20\.'
    }

    It 'requires an instance type for launch' {
        $output = @(& bash $script:validatorPath 'launch' 'anson-test' '' '1' '' '100' 2>&1)

        $LASTEXITCODE | Should -Be 1
        ($output -join "`n") | Should -Match 'Instance type is required when action is launch\.'
    }

    It 'requires a positive integer disk size for launch' -ForEach @(
        @{ DiskSize = '0' }
        @{ DiskSize = '-1' }
        @{ DiskSize = '100.5' }
        @{ DiskSize = 'large' }
        @{ DiskSize = '' }
    ) {
        $output = @(& bash $script:validatorPath 'launch' 'anson-test' '' '1' 'm5.xlarge' $DiskSize 2>&1)

        $LASTEXITCODE | Should -Be 1
        ($output -join "`n") | Should -Match 'Disk size must be a positive integer when action is launch\.'
    }

    It 'accepts deletion only when the full prefixed stack name is confirmed' {
        $output = @(& bash $script:validatorPath 'delete' 'anson-test' 'windows-a11y-anson-test' '' '' '' 2>&1)
        $exitCode = $LASTEXITCODE

        $exitCode | Should -Be 0
        $output | Should -Be @('windows-a11y-anson-test')
    }

    It 'rejects deletion when the confirmation does not match the full stack name' {
        $output = @(& bash $script:validatorPath 'delete' 'anson-test' 'anson-test' '' '' '' 2>&1)
        $exitCode = $LASTEXITCODE

        $exitCode | Should -Be 1
        ($output -join "`n") | Should -Match 'enter the full stack name exactly: windows-a11y-anson-test'
    }

    It 'rejects a suffix that already includes the managed prefix' {
        $output = @(& bash $script:validatorPath 'launch' 'windows-a11y-anson-test' '' '1' 'm5.xlarge' '100' 2>&1)
        $exitCode = $LASTEXITCODE

        $exitCode | Should -Be 1
        ($output -join "`n") | Should -Match 'Enter only the suffix, without the windows-a11y- prefix\.'
    }

    It 'rejects suffixes outside the lowercase alphanumeric and hyphen convention' {
        $invalidSuffixes = @('Anson', 'anson_test', '-anson', 'anson-', 'anson test')

        foreach ($suffix in $invalidSuffixes) {
            $output = @(& bash $script:validatorPath 'launch' $suffix '' '1' 'm5.xlarge' '100' 2>&1)
            $exitCode = $LASTEXITCODE

            $exitCode | Should -Be 1 -Because "'$suffix' is not a valid stack suffix"
            ($output -join "`n") | Should -Match 'lowercase letters, numbers, and internal hyphens'
        }
    }

    It 'rejects a suffix that would exceed the CloudFormation stack name limit' {
        $tooLongSuffix = 'a' * 116

        $output = @(& bash $script:validatorPath 'launch' $tooLongSuffix '' '1' 'm5.xlarge' '100' 2>&1)
        $exitCode = $LASTEXITCODE

        $exitCode | Should -Be 1
        ($output -join "`n") | Should -Match '115 characters or fewer'
    }

    It 'rejects unsupported actions' {
        $output = @(& bash $script:validatorPath 'replace' 'anson-test' '' '1' 'm5.xlarge' '100' 2>&1)
        $exitCode = $LASTEXITCODE

        $exitCode | Should -Be 1
        ($output -join "`n") | Should -Match 'Action must be launch or delete\.'
    }
}
