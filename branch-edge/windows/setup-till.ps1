# Sets up one checkout computer to use the branch box. Run once per till, as Administrator:
#   powershell -ExecutionPolicy Bypass -File .\setup-till.ps1
# It trusts the box's security certificate (after you compare its fingerprint) and puts a
# "Motion Till" shortcut on the desktop that opens the till full-screen and prints receipts
# straight to this computer's default printer (set the USB receipt printer as default).
#Requires -RunAsAdministrator
param(
    [string]$Box,
    [int]$HttpsPort = 8443,
    [int]$HttpPort = 8080,
    [switch]$Kiosk
)
$ErrorActionPreference = "Stop"
if (-not $Box) { $Box = Read-Host "Back-office PC name or address (shown when the branch box was installed)" }
$Box = $Box.Trim()

Write-Host "Contacting the branch box at $Box ..."
try {
    $pem = (Invoke-WebRequest -UseBasicParsing "http://$($Box):$HttpPort/cert" -TimeoutSec 10).Content
} catch {
    throw "Cannot reach the branch box at $Box. Check that the back-office PC is on, the box is installed, and both PCs are on the same shop network."
}
if ($pem -is [byte[]]) { $pem = [Text.Encoding]::ASCII.GetString($pem) }
$tmp = Join-Path $env:TEMP "motion-branch-box.cer"
[IO.File]::WriteAllText($tmp, $pem)
$cert = New-Object Security.Cryptography.X509Certificates.X509Certificate2($tmp)
$sha = [Security.Cryptography.SHA256]::Create()
$fp = (($sha.ComputeHash($cert.RawData) | ForEach-Object { $_.ToString("X2") }) -join ":")

Write-Host ""
Write-Host "Security fingerprint of the branch box:" -ForegroundColor Yellow
Write-Host "  $fp"
$answer = Read-Host "Does it match the fingerprint shown on the back-office PC? (Y/N)"
if ($answer -notmatch "^[Yy]") { Remove-Item $tmp; throw "Stopped: the fingerprint did not match. Do not continue; call support." }

Import-Certificate -FilePath $tmp -CertStoreLocation Cert:\LocalMachine\Root | Out-Null
Remove-Item $tmp

$url = "https://$($Box):$HttpsPort/"
try {
    Invoke-WebRequest -UseBasicParsing "$($url)api/meta" -TimeoutSec 10 | Out-Null
} catch {
    throw "The certificate is installed but the till address $url did not answer: $($_.Exception.Message)"
}

$candidates = @(
    "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
    "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe",
    "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
    "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe"
)
$browser = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $browser) { throw "Microsoft Edge or Google Chrome is needed on this computer." }
$launchArgs = if ($Kiosk) { "--kiosk $url --edge-kiosk-type=fullscreen --kiosk-printing" } else { "--app=$url --start-maximized --kiosk-printing" }

$shell = New-Object -ComObject WScript.Shell
$link = $shell.CreateShortcut((Join-Path $env:Public "Desktop\Motion Till.lnk"))
$link.TargetPath = $browser
$link.Arguments = $launchArgs
$link.Description = "Motion till (branch box, works without internet)"
$link.IconLocation = "$browser,0"
$link.Save()

Write-Host ""
Write-Host "This checkout computer is ready." -ForegroundColor Green
Write-Host "  Open 'Motion Till' on the desktop. The first time, choose this till in the Register list;"
Write-Host "  it is remembered on this computer."
Write-Host "  Make the USB receipt printer the default printer so receipts print without a dialog."
