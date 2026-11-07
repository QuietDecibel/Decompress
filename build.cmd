@echo off
rem Double-click or run "build rebuild" from cmd.exe. Forwards to scripts\build.ps1.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\build.ps1" %*
if "%~1"=="" pause
