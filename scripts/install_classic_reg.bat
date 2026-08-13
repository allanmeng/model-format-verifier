@echo off
chcp 65001 >nul
title MFV - Install Classic Context Menu

rem ============================================================
rem  Install "classic context menu" integration (HKCU, no admin)
rem  Result: right-click .safetensors / .gguf -> "Check Model"
rem          right-click a folder -> "Scan Models"
rem  Note: on Windows 11 these items appear under "Show more options"
rem  Uninstall: run with "uninstall" argument
rem        install_classic_reg.bat uninstall
rem ============================================================

set CMD_CHECK="%~dp0mfv_check.bat" "%%1"
set CMD_SCAN="%~dp0mfv_scan.bat" "%%V"

if /i "%~1"=="uninstall" goto uninstall

reg add "HKCU\Software\Classes\SystemFileAssociations\.safetensors\shell\MFVCheck" /ve /d "Check Model" /f >nul
reg add "HKCU\Software\Classes\SystemFileAssociations\.safetensors\shell\MFVCheck\command" /ve /d "%CMD_CHECK%" /f >nul
reg add "HKCU\Software\Classes\SystemFileAssociations\.gguf\shell\MFVCheck" /ve /d "Check Model" /f >nul
reg add "HKCU\Software\Classes\SystemFileAssociations\.gguf\shell\MFVCheck\command" /ve /d "%CMD_CHECK%" /f >nul
reg add "HKCU\Software\Classes\Directory\shell\MFVScan" /ve /d "Scan Models" /f >nul
reg add "HKCU\Software\Classes\Directory\shell\MFVScan\command" /ve /d "%CMD_SCAN%" /f >nul

echo.
echo Installed successfully!
echo   Right-click .safetensors / .gguf -> Check Model
echo   Right-click folder -> Scan Models
echo.
echo Note: on Windows 11, these items appear under "Show more options".
pause >nul
exit /b 0

:uninstall
reg delete "HKCU\Software\Classes\SystemFileAssociations\.safetensors\shell\MFVCheck" /f >nul 2>&1
reg delete "HKCU\Software\Classes\SystemFileAssociations\.gguf\shell\MFVCheck" /f >nul 2>&1
reg delete "HKCU\Software\Classes\Directory\shell\MFVScan" /f >nul 2>&1
echo Uninstalled.
pause >nul
exit /b 0
