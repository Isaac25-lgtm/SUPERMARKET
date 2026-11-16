# Installs the Motion branch box on the branch's back-office PC. Run once, as Administrator:
#   Right-click PowerShell > Run as administrator, then:
#   powershell -ExecutionPolicy Bypass -File .\install-box.ps1
# You need: the head-office address and a branch box token (System > Branch devices > Register).
#Requires -RunAsAdministrator
param(
    [string]$CentralUrl,
    [string]$DeviceToken,
    [int]$HttpsPort = 8443,
    [int]$HttpPort = 8080
)
$ErrorActionPreference = "Stop"
$TaskName = "Motion Branch Box"
$src = $PSScriptRoot
$dest = Join-Path $env:ProgramFiles "MotionBranchBox"
$data = Join-Path $env:ProgramData "MotionBranchBox"

if (-not (Test-Path (Join-Path $src "MotionBranchBox.exe"))) { throw "Run this script from the unzipped MotionBranchBox folder." }
if (-not $CentralUrl) { $CentralUrl = Read-Host "Head office address (for example https://motion-supermarket.onrender.com)" }
$CentralUrl = $CentralUrl.Trim().TrimEnd("/")
if (-not $CentralUrl.StartsWith("https://")) { throw "The head office address must start with https://" }
if (-not $DeviceToken) {
    $secure = Read-Host "Branch box token (starts with edge_)" -AsSecureString
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { $DeviceToken = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) } finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
}
$DeviceToken = $DeviceToken.Trim()
if (-not $DeviceToken.StartsWith("edge_")) { throw "That is not a branch box token (it starts with edge_)." }

# Stop a running copy before replacing its files (sales already saved stay in the data folder).
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Get-Process MotionBranchBox -ErrorAction SilentlyContinue | Stop-Process -Force
    Start-Sleep -Seconds 2
}
New-Item -ItemType Directory -Force $dest | Out-Null
robocopy $src $dest /MIR /XF config.json /NFL /NDL /NJH /NJS /NP | Out-Null
if ($LASTEXITCODE -ge 8) { throw "Copying the program files failed (robocopy $LASTEXITCODE)." }

# Settings, readable only by the system and administrators (the token is a secret).
New-Item -ItemType Directory -Force $data | Out-Null
$config = @{ central_url = $CentralUrl; device_token = $DeviceToken; https_port = $HttpsPort; http_port = $HttpPort } | ConvertTo-Json
[IO.File]::WriteAllText((Join-Path $data "config.json"), $config, (New-Object Text.UTF8Encoding($false)))
icacls $data /inheritance:r /grant:r "*S-1-5-18:(OI)(CI)F" "*S-1-5-32-544:(OI)(CI)F" | Out-Null

# Tills reach the box over the shop network only (private/domain networks).
Get-NetFirewallRule -DisplayName "Motion Branch Box*" -ErrorAction SilentlyContinue | Remove-NetFirewallRule
New-NetFirewallRule -DisplayName "Motion Branch Box (tills)" -Direction Inbound -Protocol TCP `
    -LocalPort $HttpsPort, $HttpPort -Profile Private, Domain -Action Allow | Out-Null

# Runs in the background as a service-like task: starts with Windows, restarts if it stops.
$action = New-ScheduledTaskAction -Execute (Join-Path $dest "MotionBranchBox.exe") -WorkingDirectory $dest
$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName

Write-Host "Starting the branch box..."
$fp = $null
for ($i = 0; $i -lt 60; $i++) {
    Start-Sleep -Seconds 2
    try { $fp = (Invoke-WebRequest -UseBasicParsing "http://localhost:$HttpPort/fingerprint" -TimeoutSec 3).Content; break } catch { }
}
if (-not $fp) { throw "The branch box did not start. See $data\box.log" }

$ips = (Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | Where-Object { $_.IPAddress -notlike "127.*" -and $_.IPAddress -notlike "169.254.*" }).IPAddress
Write-Host ""
Write-Host "The branch box is installed and running." -ForegroundColor Green
Write-Host "  This PC's name:       $env:COMPUTERNAME"
Write-Host "  Shop network address: $($ips -join ', ')"
Write-Host "  Till address:         https://$($env:COMPUTERNAME):$HttpsPort/"
Write-Host "  Security fingerprint: $fp"
Write-Host ""
Write-Host "Next: on each checkout computer, run setup-till.ps1 as Administrator and enter '$env:COMPUTERNAME'."
Write-Host "Compare the fingerprint it shows with the one above."
Write-Host "Keep this PC switched on during trading hours (set Sleep to 'Never') and on a UPS."
