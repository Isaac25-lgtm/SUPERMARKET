# Run on a checkout computer (no Administrator needed) after setup, after a reboot of the back-office PC,
# and after its network address changes. It connects exactly as the till does: Windows checks the box's
# certificate against the trust root installed by setup-till.ps1 and checks the PC name.
#   powershell -ExecutionPolicy Bypass -File .\check-till.ps1 -Box BACKOFFICE-PC
param([Parameter(Mandatory)][string]$Box, [int]$HttpsPort = 8443)
$ErrorActionPreference = "Stop"
$url = "https://$($Box):$HttpsPort/api/meta"
try {
    $meta = Invoke-RestMethod $url -TimeoutSec 10
    Write-Host "OK: $url is trusted by Windows and answered as branch '$($meta.branch.name)'." -ForegroundColor Green
    exit 0
} catch {
    Write-Host "FAILED: $url : $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "Check the back-office PC is on and reachable by name, and that setup-till.ps1 was run on this computer."
    exit 1
}
