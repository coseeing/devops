Describe 'Windows A11y stack operation validation' {
    BeforeAll {
        $script:validatorPath = Join-Path $PSScriptRoot '..\validate-stack-operation.sh'
    }

    It 'builds the prefixed stack name for a launch request' {
        $output = @(& bash $script:validatorPath 'launch' 'anson-test' '' '2026-08-15' 2>&1)
        $exitCode = $LASTEXITCODE

        $exitCode | Should -Be 0
        $output | Should -Be @('windows-a11y-anson-test')
    }

    It 'requires an AMI name for a launch request' {
        $output = @(& bash $script:validatorPath 'launch' 'anson-test' '' '' 2>&1)
        $exitCode = $LASTEXITCODE

        $exitCode | Should -Be 1
        ($output -join "`n") | Should -Match 'AMI name is required when action is launch\.'
    }

    It 'accepts deletion only when the full prefixed stack name is confirmed' {
        $output = @(& bash $script:validatorPath 'delete' 'anson-test' 'windows-a11y-anson-test' '' 2>&1)
        $exitCode = $LASTEXITCODE

        $exitCode | Should -Be 0
        $output | Should -Be @('windows-a11y-anson-test')
    }

    It 'rejects deletion when the confirmation does not match the full stack name' {
        $output = @(& bash $script:validatorPath 'delete' 'anson-test' 'anson-test' '' 2>&1)
        $exitCode = $LASTEXITCODE

        $exitCode | Should -Be 1
        ($output -join "`n") | Should -Match 'enter the full stack name exactly: windows-a11y-anson-test'
    }

    It 'rejects a suffix that already includes the managed prefix' {
        $output = @(& bash $script:validatorPath 'launch' 'windows-a11y-anson-test' '' '2026-08-15' 2>&1)
        $exitCode = $LASTEXITCODE

        $exitCode | Should -Be 1
        ($output -join "`n") | Should -Match 'Enter only the suffix, without the windows-a11y- prefix\.'
    }

    It 'rejects suffixes outside the lowercase alphanumeric and hyphen convention' {
        $invalidSuffixes = @('Anson', 'anson_test', '-anson', 'anson-', 'anson test')

        foreach ($suffix in $invalidSuffixes) {
            $output = @(& bash $script:validatorPath 'launch' $suffix '' '2026-08-15' 2>&1)
            $exitCode = $LASTEXITCODE

            $exitCode | Should -Be 1 -Because "'$suffix' is not a valid stack suffix"
            ($output -join "`n") | Should -Match 'lowercase letters, numbers, and internal hyphens'
        }
    }

    It 'rejects a suffix that would exceed the CloudFormation stack name limit' {
        $tooLongSuffix = 'a' * 116

        $output = @(& bash $script:validatorPath 'launch' $tooLongSuffix '' '2026-08-15' 2>&1)
        $exitCode = $LASTEXITCODE

        $exitCode | Should -Be 1
        ($output -join "`n") | Should -Match '115 characters or fewer'
    }

    It 'rejects unsupported actions' {
        $output = @(& bash $script:validatorPath 'replace' 'anson-test' '' '2026-08-15' 2>&1)
        $exitCode = $LASTEXITCODE

        $exitCode | Should -Be 1
        ($output -join "`n") | Should -Match 'Action must be launch or delete\.'
    }
}
