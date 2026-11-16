# Removes the branch box from the back-office PC. Run as Administrator:
#   powershell -ExecutionPolicy Bypass -File .\uninstall-box.ps1 [-RemoveData]
#
# Safety: before anything is removed, the box's saved sales are read directly from its database
# (works whether or not the box is running). If any sale, refund or shift is still waiting to upload,
# or was refused by head office and is awaiting review, nothing is removed unless you pass -Force,
# and even then the data folder is KEPT. The data folder is deleted only with -RemoveData when every
# event has been accepted by head office, or with -ForceRemoveData (destroys unsent sales; last resort).
# If the check cannot run (program missing, database unreadable), the data is always kept.
param(
    [switch]$Force,            # remove the program even though events are not yet accepted (data is kept)
    [switch]$RemoveData,       # also delete the data folder, only if everything was accepted by head office
    [switch]$ForceRemoveData,  # delete the data folder whatever it contains (unsent sales are lost)
    [string]$ProgramDir = (Join-Path $env:ProgramFiles "MotionBranchBox"),
    [string]$DataDir = (Join-Path $env:ProgramData "MotionBranchBox"),
    [switch]$SkipSystem        # testing only: leave the startup task, firewall and processes alone
)
$ErrorActionPreference = "Stop"
$TaskName = "Motion Branch Box"
if (-not $SkipSystem) {
    $admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
    if (-not $admin) { throw "Run this script as Administrator." }
}

# 1) What has not reached head office? (read-only; exit 0 = nothing at risk, 3 = pending/needs review, 2 = unreadable)
$exe = Join-Path $ProgramDir "MotionBranchBox.exe"
$state = "unknown"; $info = $null
if (Test-Path $exe) {
    $previous = $env:MOTION_EDGE_DATA
    $env:MOTION_EDGE_DATA = $DataDir
    try {
        $out = & $exe --outbox-status 2>$null
        $code = $LASTEXITCODE
    } finally { $env:MOTION_EDGE_DATA = $previous }
    try { $info = ($out | Select-Object -Last 1) | ConvertFrom-Json } catch { $info = $null }
    if ($code -eq 0) { $state = "safe" } elseif ($code -eq 3) { $state = "at-risk" }
} elseif (-not (Test-Path (Join-Path $DataDir "box.sqlite3"))) {
    $state = "safe"  # no program and no database: nothing to lose
}

switch ($state) {
    "safe" { Write-Host "All sales on this branch box have been accepted by head office." }
    "at-risk" {
        Write-Host "$($info.pending) event(s) are still waiting to upload and $($info.rejected) need review by the branch manager." -ForegroundColor Yellow
        if (-not $Force) {
            throw "Nothing was removed. Connect the internet and let the box upload, resolve the events that need review, then run this again (or use -Force to remove only the program; the data is kept)."
        }
    }
    default {
        Write-Host "Could not check the saved sales (program or database unreadable). The data folder will be kept." -ForegroundColor Yellow
        if (-not $Force) { throw "Nothing was removed. Use -Force to remove only the program; the data folder is kept." }
    }
}

# 2) Stop and remove the program.
if (-not $SkipSystem) {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    }
    Get-Process MotionBranchBox -ErrorAction SilentlyContinue | Stop-Process -Force
    Get-NetFirewallRule -DisplayName "Motion Branch Box*" -ErrorAction SilentlyContinue | Remove-NetFirewallRule
}
if (Test-Path $ProgramDir) { Remove-Item -Recurse -Force $ProgramDir }
Write-Host "The branch box program was removed." -ForegroundColor Green

# 3) The data folder: deleted only when it is safe, or when explicitly forced.
if ($ForceRemoveData) {
    if (Test-Path $DataDir) { Remove-Item -Recurse -Force $DataDir }
    Write-Host "The data folder was deleted (forced)." -ForegroundColor Yellow
} elseif ($RemoveData -and $state -eq "safe") {
    if (Test-Path $DataDir) { Remove-Item -Recurse -Force $DataDir }
    Write-Host "The data folder was deleted."
} else {
    if ($RemoveData) { Write-Host "The data folder was KEPT because not every event was accepted by head office." -ForegroundColor Yellow }
    if (Test-Path $DataDir) { Write-Host "Data kept in $DataDir" }
}
