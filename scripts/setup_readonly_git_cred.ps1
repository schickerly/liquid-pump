param(
    [string]$GitHubUser = "x-access-token",
    [string]$Host = "github.com"
)

$ErrorActionPreference = "Stop"

function Require-Command {
    param([string]$Name)
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Required command not found: $Name"
    }
}

Require-Command git
Require-Command cmdkey

Write-Host "Configuring Git credential helper for this machine account..."
git config --global credential.helper manager-core

if ($LASTEXITCODE -ne 0) {
    throw "Failed to configure git credential helper."
}

$tokenSecure = Read-Host "Enter read-only GitHub fine-grained token" -AsSecureString
$tokenBstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($tokenSecure)
$tokenPlain = [Runtime.InteropServices.Marshal]::PtrToStringAuto($tokenBstr)

try {
    if ([string]::IsNullOrWhiteSpace($tokenPlain)) {
        throw "No token entered."
    }

    # Stores a machine-local credential only for github.com.
    cmdkey /generic:"git:https://$Host" /user:"$GitHubUser" /pass:"$tokenPlain" | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to store credential in Windows Credential Manager."
    }

    Write-Host ""
    Write-Host "Credential stored successfully."
    Write-Host "Run this as the dedicated shared-PC account only."
}
finally {
    if ($tokenBstr -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($tokenBstr)
    }
}
