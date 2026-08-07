@echo off
chcp 65001 >nul
set PYTHONUTF8=1
title MFV - Batch Scan Directory

if "%~1"=="" (
    echo No directory passed. Right-click a folder to use this menu.
    pause
    exit /b 1
)

cd /d "%~1"

echo Scanning directory: %~1
echo.
"F:\ComfyUI-aki-v3\python\python.exe" "D:\projects\model-format-verifier\check_model.py" "%~1"
echo.
echo Scan finished. Press any key to close...
pause >nul
