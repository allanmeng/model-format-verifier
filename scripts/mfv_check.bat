@echo off
chcp 65001 >nul
set PYTHONUTF8=1
title MFV - Model Format Check

rem ================== CONFIG ==================
rem Python 解释器路径; 留空则使用系统 PATH 中的 python
set PYTHON=F:\ComfyUI-aki-v3\python\python.exe
rem check_model.py 路径; 默认自动定位到本脚本上级目录, 一般无需修改
set SCRIPT=%~dp0..\check_model.py
rem ============================================

if not exist "%SCRIPT%" (
    echo [ERROR] check_model.py not found: %SCRIPT%
    pause
    exit /b 1
)

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
if defined PYTHON (
    "%PYTHON%" "%SCRIPT%" "%~1"
) else (
    python "%SCRIPT%" "%~1"
)
shift
goto loop

:done
echo.
echo All done. Press any key to close...
pause >nul
