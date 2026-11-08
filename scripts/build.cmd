@echo off
rem Same as ..\build.cmd: double-click for a menu.
call "%~dp0..\build.cmd" %*
exit /b %ERRORLEVEL%
