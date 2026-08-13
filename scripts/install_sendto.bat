@echo off
chcp 65001 >nul
title MFV - Install SendTo Menu

rem ============================================================
rem  Install "Send To" integration: create shortcuts in SendTo
rem  Result: right-click .safetensors / .gguf -> Send To -> Check Model
rem          right-click a folder -> Send To -> Scan Models
rem  Requires: PowerShell (built-in on Windows)
rem  Uninstall: delete Check Model.lnk / Scan Models.lnk in SendTo
rem ============================================================

set SENDTO=%APPDATA%\Microsoft\Windows\SendTo
if not exist "%SENDTO%" mkdir "%SENDTO%"

powershell -NoProfile -Command ^
  "$ws = New-Object -ComObject WScript.Shell;" ^
  "$l1 = $ws.CreateShortcut('%SENDTO%\Check Model.lnk');" ^
  "$l1.TargetPath = '%~dp0mfv_check.bat';" ^
  "$l1.WorkingDirectory = '%~dp0';" ^
  "$l1.Save();" ^
  "$l2 = $ws.CreateShortcut('%SENDTO%\Scan Models.lnk');" ^
  "$l2.TargetPath = '%~dp0mfv_scan.bat';" ^
  "$l2.WorkingDirectory = '%~dp0';" ^
  "$l2.Save();"

if errorlevel 1 (
    echo [ERROR] failed to create shortcuts
    pause
    exit /b 1
)

echo.
echo Installed successfully!
echo   Right-click a model file -> Send To -> Check Model
echo   Right-click a folder    -> Send To -> Scan Models
echo.
echo Uninstall: delete these files:
echo   %SENDTO%\Check Model.lnk
echo   %SENDTO%\Scan Models.lnk
pause >nul
