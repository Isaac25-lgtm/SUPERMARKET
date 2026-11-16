# Removes the branch box from the back-office PC. Run as Administrator.
# It refuses while sales are still waiting to upload, unless you pass -Force.
# The data folder (sales history and settings) is kept unless you pass -RemoveData.
#Requires -RunAsAdministrator
param([switch]$Force, [switch]$RemoveData, [int]$HttpsPort = 8443)
$ErrorActionPreference = "Stop"
$TaskName = "Motion Branch Box"
$dest = Join-Path $env:ProgramFiles "MotionBranchBox"
$data = Join-Path $env:ProgramData "MotionBranchBox"

try {
    [Net.ServicePointManager]::ServerCertificateValidationCallback = { $true }
    $status = Invoke-RestMethod "https://localhost:$HttpsPort/edge/status" -TimeoutSec 5
    if ($status.pending -gt 0 -and -not $Force) {
        throw "$($status.pending) sales are still waiting to upload to head office. Connect the internet and wait, or use -Force."
    }
} catch [System.Net.WebException] {
    Write-Host "The branch box is not running; continuing."
} finally {
    [Net.ServicePointManager]::ServerCertificateValidationCallback = $null
}

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}
Get-Process MotionBranchBox -ErrorAction SilentlyContinue | Stop-Process -Force
Get-NetFirewallRule -DisplayName "Motion Branch Box*" -ErrorAction SilentlyContinue | Remove-NetFirewallRule
if (Test-Path $dest) { Remove-Item -Recurse -Force $dest }
if ($RemoveData -and (Test-Path $data)) { Remove-Item -Recurse -Force $data }
Write-Host "The branch box was removed." -ForegroundColor Green
if (-not $RemoveData) { Write-Host "Its data was kept in $data" }
