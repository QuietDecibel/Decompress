<#
Build / test / rebuild Decompress on Windows (PowerShell 5+ or 7+).

  .\scripts\build.ps1 setup     create .venv and install the app in editable mode (+ dev tools, MediaPipe)
  .\scripts\build.ps1 test      run the test suite
  .\scripts\build.ps1 build     build the wheel + sdist + Blender add-on into dist\
  .\scripts\build.ps1 verify    install the built wheel into a clean venv and smoke-test it
  .\scripts\build.ps1 rebuild   clean -> setup -> test -> build -> verify   (the full cycle)
  .\scripts\build.ps1 clean     remove build outputs (keeps .venv and your data)
  .\scripts\build.ps1 exe       build the standalone app: dist\Decompress\Decompress.exe (no Python needed), then smoke-test it
  .\scripts\build.ps1 installer build dist\DecompressSetup-<ver>.exe (Start Menu + Desktop shortcuts, uninstaller)
  .\scripts\build.ps1 shortcut  add Desktop + Start Menu shortcuts for this checkout (to the exe if built, else the venv)
  .\scripts\build.ps1 release   rebuild + exe + installer   (everything)
  .\scripts\build.ps1 run       start the app      (http://127.0.0.1:8000)
  .\scripts\build.ps1 hub       start the hub      (http://127.0.0.1:8765)

Options: -Extras "all,dev" (default "mediapipe,dev"), -Python "py -3.12" (default: py launcher, else python).
If scripts are blocked, run once:  Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
#>
param(
    [Parameter(Position = 0)][ValidateSet("setup", "test", "build", "verify", "rebuild", "clean", "exe", "installer", "shortcut", "release", "run", "hub", "help")]
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
function Exe {
    Step "Building standalone Decompress.exe with PyInstaller"
    if (-not (Test-Path $VPy)) { Setup }
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

switch ($Command) {
    "setup"   { Setup }
    "test"    { Test }
    "build"   { Build }
    "verify"  { Verify }
    "rebuild" { Clean; Setup; Test; Build; Verify; Step "Rebuild complete: dist\ is ready" }
    "clean"   { Clean }
    "exe"     { Exe }
    "installer" { Installer }
    "shortcut" { Shortcut }
    "release" { Clean; Setup; Test; Build; Verify; Exe; Installer; Step "Release ready in dist\" }
    "run"     { & $VPy -m decompress serve }
    "hub"     { & $VPy -m decompress hub }
    default   { Get-Help $PSCommandPath -Detailed | Out-String | Write-Host }
}
