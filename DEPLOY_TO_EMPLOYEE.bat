@echo off
title EmpMon V8 - Employee Agent Deploy
color 0B
echo.
echo  ============================================
echo   EmpMon V8 - Deploy to Employee PC
echo  ============================================
echo.

:: ── CHECK PYTHON ──────────────────────────────────────────
python --version 2>nul
if errorlevel 1 (
    echo  ERROR: Python not installed.
    echo  Download from: https://python.org
    echo  Make sure to check "Add Python to PATH" during install.
    pause
    exit /b 1
)

:: ── INSTALL DEPENDENCIES ──────────────────────────────────
echo  Installing required packages...
pip install requests pywin32 psutil --quiet
echo  Packages installed.
echo.

:: ── COPY AGENT ────────────────────────────────────────────
echo  Copying agent to C:\EmpMonitor...
if not exist "C:\EmpMonitor" mkdir "C:\EmpMonitor"
copy /Y "%~dp0employee_agent.py" "C:\EmpMonitor\employee_agent.py" >nul
echo  Agent copied.
echo.

:: ── CREATE TASK SCHEDULER ENTRY ───────────────────────────
echo  Creating Windows Task Scheduler entry...
echo  (Agent will start automatically at login)
echo.

schtasks /create /tn "EmpMonV8Agent" /tr "pythonw C:\EmpMonitor\employee_agent.py" /sc ONLOGON /rl HIGHEST /f >nul 2>&1
if errorlevel 1 (
    echo  Note: Task Scheduler entry may need admin rights.
    echo  Running agent directly now...
) else (
    echo  Task scheduled: EmpMonV8Agent
)
echo.

:: ── START AGENT NOW ───────────────────────────────────────
echo  Starting agent now (silent background)...
start /b pythonw "C:\EmpMonitor\employee_agent.py"
echo  Agent started.
echo.

echo  ============================================
echo   DONE! Employee PC is now being monitored.
echo  ============================================
echo.
echo  The agent runs silently in the background.
echo  Data is sent to the central server automatically.
echo.
pause
