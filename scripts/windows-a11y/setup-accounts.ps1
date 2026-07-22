[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$logPrefix = '[setup-accounts]'

function New-RandomPassword {
    $chars = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789!@#$%'
    $length = 24
    $rng = [System.Security.Cryptography.RNGCryptoServiceProvider]::new()
    try {
        $result = New-Object System.Text.StringBuilder
        $byte = [byte[]]::new(1)
        while ($result.Length -lt $length) {
            $rng.GetBytes($byte)
            # Reject bytes that would introduce modulo bias for this charset over a 256-value byte range.
            if ($byte[0] -lt (256 - (256 % $chars.Length))) {
                [void]$result.Append($chars[$byte[0] % $chars.Length])
            }
        }
        $result.ToString()
    } finally {
        $rng.Dispose()
    }
}

function Set-LocalAccount {
    param(
        [string]$Name,
        [switch]$IsAdmin
    )

    $password = New-RandomPassword
    $securePassword = ConvertTo-SecureString $password -AsPlainText -Force

    $existing = Get-LocalUser -Name $Name -ErrorAction SilentlyContinue
    if ($existing) {
        Write-Output "$logPrefix Account $Name exists, resetting password."
        Set-LocalUser -Name $Name -Password $securePassword -PasswordNeverExpires:$true
    } else {
        Write-Output "$logPrefix Creating account $Name."
        New-LocalUser -Name $Name -Password $securePassword -PasswordNeverExpires:$true -AccountNeverExpires | Out-Null
    }

    if ($IsAdmin) {
        if (-not (Get-LocalGroupMember -Group 'Administrators' -Member $Name -ErrorAction SilentlyContinue)) {
            Add-LocalGroupMember -Group 'Administrators' -Member $Name
        }
    } else {
        if (Get-LocalGroupMember -Group 'Administrators' -Member $Name -ErrorAction SilentlyContinue) {
            Remove-LocalGroupMember -Group 'Administrators' -Member $Name
        }
    }

    Write-Output "ACCOUNT_PASSWORD_$($Name.ToUpper())=$password"
}

Set-LocalAccount -Name 'coseeing' -IsAdmin
Set-LocalAccount -Name 'user'
