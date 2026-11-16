# Asks the Windows certificate chain engine (the one Windows and Edge use) to validate a branch box server chain.
#
# On a checkout computer that ran setup-till.ps1 (the box's trust root is installed):
#   powershell -ExecutionPolicy Bypass -File verify-cert-windows.ps1 -Installed -Chain box-server-chain.pem   -> OK
#   powershell -ExecutionPolicy Bypass -File verify-cert-windows.ps1 -Installed -Chain forged.pem             -> INVALID
# (forged.pem comes from forge_test_cert.py: a public-site certificate signed with the box's issuer key.)
#
# Without installing anything (development PC): supply the root with -Ca. Note: Windows then reports only
# "untrusted root" and does not show name-constraint results, so name constraints can only be checked with -Installed.
# Exit code 0 = valid chain, 1 = invalid.
param([string]$Ca, [Parameter(Mandatory)][string]$Chain, [switch]$Installed)
$ErrorActionPreference = "Stop"
if (-not $Installed -and -not $Ca) { throw "Use -Installed on a till, or give the trust root with -Ca." }

function Read-PemCerts([string]$path) {
    $text = [IO.File]::ReadAllText($path)
    $blocks = [regex]::Matches($text, "-----BEGIN CERTIFICATE-----(.+?)-----END CERTIFICATE-----", "Singleline")
    foreach ($b in $blocks) {
        $der = [Convert]::FromBase64String(($b.Groups[1].Value -replace "\s", ""))
        New-Object Security.Cryptography.X509Certificates.X509Certificate2(, $der)
    }
}

$chainCerts = @(Read-PemCerts $Chain)
$leaf = $chainCerts[0]
$chainObj = New-Object Security.Cryptography.X509Certificates.X509Chain
$chainObj.ChainPolicy.RevocationMode = "NoCheck"
foreach ($c in ($chainCerts | Select-Object -Skip 1)) { $chainObj.ChainPolicy.ExtraStore.Add($c) | Out-Null }  # the issuer the box sends
$caCert = $null
if (-not $Installed) {
    $caCert = @(Read-PemCerts $Ca)[0]
    $chainObj.ChainPolicy.ExtraStore.Add($caCert) | Out-Null
    $chainObj.ChainPolicy.VerificationFlags = "AllowUnknownCertificateAuthority"
}
$chainObj.ChainPolicy.ApplicationPolicy.Add((New-Object Security.Cryptography.Oid("1.3.6.1.5.5.7.3.1"))) | Out-Null  # server auth
$built = $chainObj.Build($leaf)

$root = $chainObj.ChainElements[$chainObj.ChainElements.Count - 1].Certificate
$allowed = if ($Installed) { @() } else { @("UntrustedRoot") }
$problems = @($chainObj.ChainStatus | Where-Object { $allowed -notcontains "$($_.Status)" } | ForEach-Object { "$($_.Status)" })
if ($caCert -and $root.Thumbprint -ne $caCert.Thumbprint) { $problems += "DoesNotChainToBoxTrustCertificate" }
if ($Installed -and -not $built) { $problems += "NotTrustedByWindows" }
$problems = $problems | Select-Object -Unique
if ($problems.Count -eq 0) { Write-Output "OK: $($leaf.Subject) chains to $($root.Subject)"; exit 0 }
Write-Output ("INVALID: " + ($problems -join "; ")); exit 1
