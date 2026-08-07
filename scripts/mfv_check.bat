@echo off
chcp 65001 >nul
set PYTHONUTF8=1
title MFV - Model Format Check

if "%~1"=="" (
    echo No file passed. Right-click a .safetensors file to use this menu.
    pause
    exit /b 1
)

cd /d "%~dp1"

:loop
if "%~1"=="" goto done
echo.
echo Checking: %~nx1
echo.
echo.
"F:\ComfyUI-aki-v3\python\python.exe" "D:\projects\model-format-verifier\check_model.py" "%~1"
shift
goto loop

:done
echo.
echo All done. Press any key to close...
pause >nul
