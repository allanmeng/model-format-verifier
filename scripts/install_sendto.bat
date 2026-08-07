@echo off
chcp 65001 >nul
title MFV - Install SendTo Menu

rem ============================================================
rem  安装「发送到」方案: 在系统 SendTo 目录创建快捷方式
rem  效果: 右键 .safetensors / .gguf -> 发送到 -> Check Model
rem        右键文件夹 -> 发送到 -> Scan Models
rem  依赖: PowerShell (Windows 自带)
rem  卸载: 删除 SendTo 目录下的 Check Model.lnk / Scan Models.lnk
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
