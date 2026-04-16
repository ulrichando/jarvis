@echo off
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0bunw.ps1" %*
exit /b %errorlevel%
