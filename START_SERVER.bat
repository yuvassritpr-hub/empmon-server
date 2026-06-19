@echo off
title EmpMon V8 - Central Server
color 0A
echo.
echo  ============================================
echo   W-SAFE REINSURANCE - EmpMon V8 Server
echo  ============================================
echo.
echo  Checking Python...
python --version 2>nul
if errorlevel 1 (
    echo  ERROR: Python not found. Install from https://python.org
    pause
    exit /b 1
)
echo.
echo  Installing dependencies...
pip install flask --quiet
echo.
echo  Starting server...
echo  Open your browser at: http://localhost:5000
echo.
python "%~dp0central_server.py"
pause
