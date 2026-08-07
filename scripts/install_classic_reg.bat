@echo off
chcp 65001 >nul
title MFV - Install Classic Context Menu

rem ============================================================
rem  安装「经典右键菜单」方案 (HKCU 用户级, 无需管理员)
rem  效果: 右键 .safetensors / .gguf 直接出现 "Check Model"
rem        右键文件夹出现 "Scan Models"
rem  Win11 提示: 传统注册表菜单显示在 "显示更多选项" 里
rem  卸载: 运行本脚本并加参数 uninstall
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
