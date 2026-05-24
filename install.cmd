@echo off
REM ============================================================================
REM JARVIS Voice Assistant Installer for Windows (CMD wrapper)
REM ============================================================================
REM This batch file launches the PowerShell installer for users in CMD.exe
REM (where the canonical irm|iex one-liner does not work because piping
REM is not the same as PowerShell's iex/irm cmdlets).
REM
REM Usage (from CMD):
REM   curl -fsSL https://raw.githubusercontent.com/ulrichando/jarvis/master/install.cmd -o install.cmd && install.cmd && del install.cmd
REM
REM Or if you are already in PowerShell, use the direct command instead:
REM   iex (irm https://raw.githubusercontent.com/ulrichando/jarvis/master/install.ps1)
REM
REM Flags passed after install.cmd are forwarded to install.ps1. Example:
REM   install.cmd -SkipDesktop -AutoInstall
REM ============================================================================

echo.
echo  JARVIS Voice Assistant Installer
echo  Launching PowerShell installer...
echo.

REM -ExecutionPolicy ByPass lets unsigned scripts run for THIS invocation
REM only, with no persistent change to the user's policy.
REM -NoProfile skips the user's $PROFILE so a custom profile cannot
REM interfere with paths or output encoding.
REM We expand %* into the iex one-liner so users can pass flags
REM (e.g. install.cmd -SkipDesktop) through to install.ps1.
powershell -ExecutionPolicy ByPass -NoProfile -Command "iex (irm https://raw.githubusercontent.com/ulrichando/jarvis/master/install.ps1) %*"

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo  Installation failed. Please try running PowerShell directly:
    echo    powershell -ExecutionPolicy ByPass -NoProfile -Command "iex (irm https://raw.githubusercontent.com/ulrichando/jarvis/master/install.ps1)"
    echo.
    pause
    exit /b 1
)
