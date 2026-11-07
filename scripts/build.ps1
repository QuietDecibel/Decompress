<#
Build / test / rebuild Decompress on Windows (PowerShell 5+ or 7+).

  .\scripts\build.ps1 setup     create .venv and install the app in editable mode (+ dev tools, MediaPipe)
  .\scripts\build.ps1 test      run the test suite
  .\scripts\build.ps1 build     build the wheel + sdist + Blender add-on into dist\
  .\scripts\build.ps1 verify    install the built wheel into a clean venv and smoke-test it
  .\scripts\build.ps1 rebuild   clean -> setup -> test -> build -> verify   (the full cycle)
  .\scripts\build.ps1 clean     remove build outputs (keeps .venv and your data)
  .\scripts\build.ps1 run       start the app      (http://127.0.0.1:8000)
  .\scripts\build.ps1 hub       start the hub      (http://127.0.0.1:8765)

Options: -Extras "all,dev" (default "mediapipe,dev"), -Python "py -3.12" (default: py launcher, else python).
If scripts are blocked, run once:  Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
#>
param(
    [Parameter(Position = 0)][ValidateSet("setup", "test", "build", "verify", "rebuild", "clean", "run", "hub", "help")]
    [string]$Command = "help",
    [string]$Extras = "mediapipe,dev",
    [string]$Python = ""
)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

if (-not $Python) { $Python = if (Get-Command py -ErrorAction SilentlyContinue) { "py -3" } else { "python" } }
$Venv = ".venv"
$VPy = Join-Path $Venv "Scripts\python.exe"

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Invoke-Checked {
    param([string]$Exe, [string[]]$Arguments)
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Command failed ($LASTEXITCODE): $Exe $($Arguments -join ' ')" }
}
function New-Venv($path) {
    $parts = $Python.Split(" ")
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
function Test { Step "Running tests"; Invoke-Checked $VPy @("-m", "pytest", "-q") }
function Build {
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
function Clean {
    Step "Cleaning build outputs"
    foreach ($p in @("build", "dist", ".pytest_cache") + @(Get-ChildItem -Filter *.egg-info -Name)) {
        if ($p -and (Test-Path $p)) { Remove-Item -Recurse -Force $p }
    }
    Get-ChildItem -Recurse -Directory -Filter __pycache__ | Where-Object { $_.FullName -notlike "*\$Venv\*" } |
        Remove-Item -Recurse -Force
}

switch ($Command) {
    "setup"   { Setup }
    "test"    { Test }
    "build"   { Build }
    "verify"  { Verify }
    "rebuild" { Clean; Setup; Test; Build; Verify; Step "Rebuild complete: dist\ is ready" }
    "clean"   { Clean }
    "run"     { & $VPy -m decompress serve }
    "hub"     { & $VPy -m decompress hub }
    default   { Get-Help $PSCommandPath -Detailed | Out-String | Write-Host }
}
