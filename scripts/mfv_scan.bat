@echo off
chcp 65001 >nul
set PYTHONUTF8=1
title MFV - Batch Scan Directory

rem ================== CONFIG ==================
rem Python interpreter path; leave empty to use PATH python
set PYTHON=F:\ComfyUI-aki-v3\python\python.exe
rem check_model.py path; auto-detect from script location by default
set SCRIPT=%~dp0..\check_model.py
rem ============================================

if not exist "%SCRIPT%" (
    echo [ERROR] check_model.py not found: %SCRIPT%
    pause
    exit /b 1
)

if "%~1"=="" (
    echo No directory passed. Right-click a folder to use this menu.
    pause
    exit /b 1
)

cd /d "%~1"

echo Scanning directory: %~1
echo.
if defined PYTHON (
    "%PYTHON%" "%SCRIPT%" "%~1"
) else (
    python "%SCRIPT%" "%~1"
)
echo.
echo Scan finished. Press any key to close...
pause >nul
