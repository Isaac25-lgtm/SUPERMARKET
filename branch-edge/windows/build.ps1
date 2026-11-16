# Builds the branch box package on a developer PC (Windows, Python 3.12, Node 20+):
#   powershell -ExecutionPolicy Bypass -File branch-edge\windows\build.ps1
# Output: branch-edge\windows\dist\MotionBranchBox\ (MotionBranchBox.exe + till screen + install scripts)
#         branch-edge\windows\dist\MotionBranchBox-<version>.zip  (copy this to the branch)
param([string]$Python = "python", [switch]$SkipWeb)
$ErrorActionPreference = "Stop"
$here = $PSScriptRoot
$root = (Resolve-Path "$here\..\..").Path
$version = (Select-String -Path "$root\branch-edge\pyproject.toml" -Pattern '^version = "(.+)"').Matches[0].Groups[1].Value

# Native tools write progress to stderr; in Windows PowerShell 5.1 that is not an error. Fail on exit codes only.
function Invoke-Native([string]$Exe, [string[]]$Arguments) {
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { & $Exe @Arguments 2>&1 | ForEach-Object { "$_" } | Out-Host } finally { $ErrorActionPreference = $previous }
    if ($LASTEXITCODE -ne 0) { throw "$Exe failed with exit code $LASTEXITCODE" }
}

if (-not $SkipWeb) {
    Push-Location "$root\web"
    try { Invoke-Native "npm.cmd" @("ci"); Invoke-Native "npm.cmd" @("run", "build") } finally { Pop-Location }
}

$venv = "$here\.build-venv"
if (-not (Test-Path "$venv\Scripts\python.exe")) { Invoke-Native $Python @("-m", "venv", $venv) }
Invoke-Native "$venv\Scripts\python.exe" @("-m", "pip", "install", "--quiet", "--upgrade", "pip")
Invoke-Native "$venv\Scripts\python.exe" @("-m", "pip", "install", "--quiet", "$root\branch-edge", "pyinstaller>=6.10")

$pkg = "$root\branch-edge\motion_edge"
Invoke-Native "$venv\Scripts\pyinstaller.exe" @(
    "--noconfirm", "--clean", "--log-level", "WARN", "--name", "MotionBranchBox", "--console",
    "--distpath", "$here\dist", "--workpath", "$here\.build", "--specpath", "$here\.build",
    "--add-data", "$pkg\till.html;motion_edge", "--add-data", "$pkg\till.js;motion_edge",
    "--collect-submodules", "uvicorn", "--hidden-import", "motion_edge.app", "--hidden-import", "motion_edge.webapi",
    "$here\entry.py")

$out = "$here\dist\MotionBranchBox"
if (Test-Path "$out\web") { Remove-Item -Recurse -Force "$out\web" }
Copy-Item -Recurse "$root\web\dist" "$out\web"
Copy-Item "$here\install-box.ps1", "$here\setup-till.ps1", "$here\uninstall-box.ps1", "$here\README.txt", "$root\branch-edge\tools\check-till.ps1" $out
$zip = "$here\dist\MotionBranchBox-$version.zip"
if (Test-Path $zip) { Remove-Item $zip }
Compress-Archive -Path "$out\*" -DestinationPath $zip
Write-Host "Built $zip"
