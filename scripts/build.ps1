<#
.SYNOPSIS
Build, test, rebuild and package Decompress on Windows.

.DESCRIPTION
Double-click build.cmd (in the repo's top folder) to get a menu, or run a command directly:

  build setup      install everything into .venv (first time, or after pulling changes)
  build run        start Decompress (opens your browser)
  build test       run the test suite
  build rebuild    clean -> setup -> test -> build -> verify (the full cycle)
  build exe        build dist\Decompress\Decompress.exe (no Python needed) and smoke-test it
  build installer  build dist\DecompressSetup-<ver>.exe (needs Inno Setup)
  build shortcut   add Desktop + Start Menu shortcuts
  build release    rebuild + exe + installer
  build build | verify | clean | hub

Options: -Extras "all,dev" (default "mediapipe,dev"), -Python "py -3.12".
#>
param(
    [Parameter(Position = 0)]
    [ValidateSet("menu", "setup", "test", "build", "verify", "rebuild", "clean", "exe", "installer", "shortcut",
                 "release", "run", "hub", "help")]
    [string]$Command = "",
    [string]$Extras = "mediapipe,dev",
    [string]$Python = "",
    [string]$Choice = "",       # pick a menu item without typing (used by CI)
    [switch]$NoPause
)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

$Venv = ".venv"
$VPy = Join-Path $Venv "Scripts\python.exe"
$Interactive = ($Command -eq "" -or $Command -eq "menu")

function Get-BasePython {
    # Returns the command (string, may contain an argument like "py -3.12") used to create virtual environments.
    if ($script:Python) { return $script:Python }
    $found = @()
    $cands = @()
    if (Get-Command py -ErrorAction SilentlyContinue) { $cands += @("py -3.12", "py -3.11", "py -3.10", "py -3") }
    if (Get-Command python -ErrorAction SilentlyContinue) { $cands += "python" }
    foreach ($c in $cands) {
        $parts = $c.Split(" ")
        try {
            $v = & $parts[0] @($parts | Select-Object -Skip 1) -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        } catch { continue }
        if ($LASTEXITCODE -eq 0 -and $v -match '^3\.(\d+)$') {
            $minor = [int]$Matches[1]
            if ($minor -ge 10 -and $minor -le 12) { $script:Python = $c; Write-Host "Using Python $v ($c)"; return $c }
            if ($minor -ge 10) { $found += , @($c, $v) }
        }
    }
    if ($found.Count) {
        $script:Python = $found[0][0]
        Write-Host "Using Python $($found[0][1]) ($($found[0][0])). Python 3.12 is recommended for MediaPipe." -ForegroundColor Yellow
        return $script:Python
    }
    throw "Python 3.10-3.12 was not found. Install it with:  winget install Python.Python.3.12   (or from python.org), then run this again."
}

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Invoke-Checked {
    param([string]$Exe, [string[]]$Arguments)
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Command failed ($LASTEXITCODE): $Exe $($Arguments -join ' ')" }
}
function New-Venv($path) {
    $parts = (Get-BasePython).Split(" ")
    $exe = $parts[0]; $rest = @($parts | Select-Object -Skip 1)
    Invoke-Checked $exe ($rest + @("-m", "venv", $path))
}

function Setup {
    Step "Creating $Venv and installing decompress[$Extras] (editable)"
    if (-not (Test-Path $VPy)) { New-Venv $Venv }
    Invoke-Checked $VPy @("-m", "pip", "install", "-q", "--upgrade", "pip")
    Invoke-Checked $VPy @("-m", "pip", "install", "-q", "-e", ".[$Extras]")
    Invoke-Checked $VPy @("-m", "decompress", "--version")
}
function Ensure-Venv { if (-not (Test-Path $VPy)) { Setup } }
function Test { Ensure-Venv; Step "Running tests"; Invoke-Checked $VPy @("-m", "pytest", "-q") }
function Build {
    Ensure-Venv
    Step "Building wheel + sdist"
    Invoke-Checked $VPy @("-m", "build", "--outdir", "dist", ".")
    Invoke-Checked $VPy @("-m", "decompress", "blender-addon", "--out", "dist\decompress_blender_addon.zip")
    Get-ChildItem dist | Format-Table Name, Length
}
function Verify {
    Step "Installing the wheel into a clean venv and smoke-testing it"
    if (Test-Path build\verify-venv) { Remove-Item -Recurse -Force build\verify-venv }
    New-Venv "build\verify-venv"
    $py = "build\verify-venv\Scripts\python.exe"
    $wheel = (Get-ChildItem dist\decompress-*.whl | Sort-Object LastWriteTime | Select-Object -Last 1).FullName
    Invoke-Checked $py @("-m", "pip", "install", "-q", "--upgrade", "pip")
    Invoke-Checked $py @("-m", "pip", "install", "-q", $wheel)
    Invoke-Checked $py @("scripts\smoke_test.py")
}
function Exe {
    Step "Building standalone Decompress.exe with PyInstaller"
    Ensure-Venv
    Invoke-Checked $VPy @("-m", "pip", "install", "-q", "-e", ".[$Extras,exe]")
    Invoke-Checked $VPy @("packaging\make_icon.py")
    Invoke-Checked $VPy @("-m", "PyInstaller", "--noconfirm", "--clean", "--log-level", "WARN",
        "--distpath", "dist", "--workpath", "build\pyinstaller", "packaging\decompress.spec")
    Invoke-Checked $VPy @("scripts\smoke_test.py", "--exe", "dist\Decompress\Decompress.exe")
    if (-not (Test-Path dist\decompress_blender_addon.zip)) {
        Invoke-Checked $VPy @("-m", "decompress", "blender-addon", "--out", "dist\decompress_blender_addon.zip")
    }
    Write-Host "Built dist\Decompress\Decompress.exe"
}
function Find-Iscc {
    $cands = @((Get-Command iscc -ErrorAction SilentlyContinue).Source,
               "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
               "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe")
    return $cands | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
}
function Installer {
    Step "Building the Windows installer"
    if (-not (Test-Path dist\Decompress\Decompress.exe)) { Exe }
    $iscc = Find-Iscc
    if (-not $iscc) { throw "Inno Setup 6 not found. Install it with:  winget install JRSoftware.InnoSetup   then run this again." }
    $ver = (& $VPy -c "import decompress; print(decompress.__version__)").Trim()
    Invoke-Checked $iscc @("/Q", "/DAppVersion=$ver", "packaging\installer.iss")
    Write-Host "Built dist\DecompressSetup-$ver.exe"
}
function Shortcut {
    Step "Creating Desktop and Start Menu shortcuts"
    $exe = Resolve-Path "dist\Decompress\Decompress.exe" -ErrorAction SilentlyContinue
    if (-not $exe) {
        if (-not (Test-Path $VPy)) { Setup }
        $exe = Resolve-Path (Join-Path $Venv "Scripts\decompress-launch.exe")
    }
    $icon = (Resolve-Path packaging\decompress.ico).Path
    $shell = New-Object -ComObject WScript.Shell
    $places = @([Environment]::GetFolderPath("Desktop"), (Join-Path ([Environment]::GetFolderPath("Programs")) ""))
    foreach ($dir in $places) {
        $lnk = $shell.CreateShortcut((Join-Path $dir "Decompress.lnk"))
        $lnk.TargetPath = "$exe"
        $lnk.WorkingDirectory = Split-Path "$exe"
        $lnk.IconLocation = "$icon,0"
        $lnk.Description = "Decompress - camera vision and motion capture"
        $lnk.Save()
        Write-Host "  $dir\Decompress.lnk -> $exe"
    }
}
function Clean {
    Step "Cleaning build outputs"
    foreach ($p in @("build", "dist", ".pytest_cache") + @(Get-ChildItem -Filter *.egg-info -Name)) {
        if ($p -and (Test-Path $p)) { Remove-Item -Recurse -Force $p }
    }
    Get-ChildItem -Recurse -Directory -Filter __pycache__ | Where-Object { $_.FullName -notlike "*\$Venv\*" } |
        Remove-Item -Recurse -Force
}

function Run {
    Ensure-Venv
    Step "Starting Decompress (close this window to stop it)"
    & $VPy -m decompress.launcher
}
function Hub {
    Ensure-Venv
    & $VPy -m decompress hub
}
function Show-Help { Get-Help $PSCommandPath -Detailed | Out-String | Write-Host }

$Menu = [ordered]@{
    "1" = @("First-time setup (install everything)", "setup")
    "2" = @("Run Decompress", "run")
    "3" = @("Run the tests", "test")
    "4" = @("Rebuild: clean, setup, test, build, verify", "rebuild")
    "5" = @("Build Decompress.exe", "exe")
    "6" = @("Build the installer (DecompressSetup.exe)", "installer")
    "7" = @("Create Desktop + Start Menu shortcuts", "shortcut")
    "8" = @("Everything: rebuild + exe + installer", "release")
}

function Invoke-Command-Name($name) {
    switch ($name) {
        "setup"     { Setup }
        "test"      { Test }
        "build"     { Build }
        "verify"    { Verify }
        "rebuild"   { Clean; Setup; Test; Build; Verify; Step "Rebuild complete: dist\ is ready" }
        "clean"     { Clean }
        "exe"       { Exe }
        "installer" { Installer }
        "shortcut"  { Shortcut }
        "release"   { Clean; Setup; Test; Build; Verify; Exe; Installer; Step "Release ready in dist\" }
        "run"       { Run }
        "hub"       { Hub }
        default     { Show-Help }
    }
}

$failed = $false
try {
    if ($Interactive) {
        Write-Host "`nDecompress build tools  ($((Get-Location).Path))" -ForegroundColor Cyan
        foreach ($k in $Menu.Keys) { Write-Host ("  {0}) {1}" -f $k, $Menu[$k][0]) }
        Write-Host "  Q) Quit"
        $pick = if ($Choice) { $Choice } else { Read-Host "`nChoose" }
        if ($Menu.Contains($pick)) { Invoke-Command-Name $Menu[$pick][1]; Step "Done" }
        elseif ($pick -notmatch '^[qQ]?$') { Write-Host "Unknown choice '$pick'" -ForegroundColor Yellow }
    } else {
        Invoke-Command-Name $Command
    }
} catch {
    $failed = $true
    Write-Host "`nFAILED: $($_.Exception.Message)" -ForegroundColor Red
}
if ($Interactive -and -not $NoPause -and -not $Choice) { Read-Host "`nPress Enter to close" | Out-Null }
if ($failed) { exit 1 }
