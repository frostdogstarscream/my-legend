@echo off
setlocal
chcp 65001 >nul
rem Discovery order: explicit override, PATH, per-user install, machine install,
rem Microsoft Store alias, then the portable runtime already used on this host.
if defined LEGEND_PWSH if not exist "%LEGEND_PWSH%" set "LEGEND_PWSH="
if defined LEGEND_PWSH goto :found
for /f "delims=" %%P in ('where pwsh.exe 2^>nul') do if not defined LEGEND_PWSH set "LEGEND_PWSH=%%P"
if not defined LEGEND_PWSH if exist "%LOCALAPPDATA%\Programs\PowerShell\7\pwsh.exe" set "LEGEND_PWSH=%LOCALAPPDATA%\Programs\PowerShell\7\pwsh.exe"
if not defined LEGEND_PWSH if exist "%ProgramFiles%\PowerShell\7\pwsh.exe" set "LEGEND_PWSH=%ProgramFiles%\PowerShell\7\pwsh.exe"
if not defined LEGEND_PWSH if exist "%ProgramFiles(x86)%\PowerShell\7\pwsh.exe" set "LEGEND_PWSH=%ProgramFiles(x86)%\PowerShell\7\pwsh.exe"
if not defined LEGEND_PWSH if exist "%LOCALAPPDATA%\Microsoft\WindowsApps\pwsh.exe" set "LEGEND_PWSH=%LOCALAPPDATA%\Microsoft\WindowsApps\pwsh.exe"
if not defined LEGEND_PWSH if exist "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\native\powershell\pwsh.exe" set "LEGEND_PWSH=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\native\powershell\pwsh.exe"
if not defined LEGEND_PWSH goto :missing

:found
"%LEGEND_PWSH%" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0ops\my-legend\Start-InteractiveFrontend.ps1" %*
set "LEGEND_EXIT=%ERRORLEVEL%"
if not "%LEGEND_EXIT%"=="0" (
    echo.
    echo Startup failed. Review the error above and press any key to close.
    pause
)
exit /b %LEGEND_EXIT%

:missing
echo PowerShell 7.4 or newer was not found.
echo.
echo Install it, then run this file again:
echo     winget install --id Microsoft.PowerShell --source winget
echo.
echo If pwsh.exe lives somewhere else, set LEGEND_PWSH to its full path.
pause
exit /b 1
