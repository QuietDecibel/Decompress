@echo off
rem Double-click for a menu, or run e.g.  build rebuild   from cmd / PowerShell in this folder.
rem Forwards to scripts\build.ps1 (bypassing the PowerShell script policy for this run only).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\build.ps1" %*
exit /b %ERRORLEVEL%
