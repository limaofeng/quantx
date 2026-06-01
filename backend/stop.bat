@echo off
setlocal enabledelayedexpansion

:: Production Stop Script for QuantX API Server
echo ============================================
echo Stopping QuantX API Server
echo ============================================
echo [%DATE% %TIME%] Stopping QuantX API Server...

:: Set script directory
cd /d "%~dp0"

:: Set PID file path and task name
set PID_FILE=quantx.pid
set TASK_NAME=QuantX-API-Server

:: Check if scheduled task exists and stop it
schtasks /query /tn "%TASK_NAME%" >NUL 2>&1
if !errorlevel! equ 0 (
    echo [%DATE% %TIME%] Stopping scheduled task: %TASK_NAME%
    schtasks /end /tn "%TASK_NAME%" >NUL 2>&1
    schtasks /delete /tn "%TASK_NAME%" /f >NUL 2>&1
    echo [%DATE% %TIME%] Scheduled task removed
)

:: Clean up service runner script
if exist "quantx_service_runner.bat" (
    del "quantx_service_runner.bat" 2>NUL
    echo [%DATE% %TIME%] Cleaned up service runner script
)

:: Check if PID file exists
if not exist "%PID_FILE%" (
    echo [%DATE% %TIME%] ERROR: PID file not found. Service may not be running.
    goto :error
)

:: Read PID from file
set /p PID=<"%PID_FILE%"
echo [%DATE% %TIME%] Found PID: %PID%

:: Check if process is running
tasklist /FI "PID eq %PID%" 2>NUL | find /I "python.exe" >NUL
if %errorlevel% neq 0 (
    echo [%DATE% %TIME%] Process with PID %PID% is not running or not a Python process
    del "%PID_FILE%" 2>NUL
    goto :success
)

:: Kill the process
echo [%DATE% %TIME%] Terminating process (PID: %PID%)...
taskkill /PID %PID% /F >NUL 2>&1
if %errorlevel% neq 0 (
    echo [%DATE% %TIME%] ERROR: Failed to terminate process
    goto :error
)

:: Wait a moment and verify
timeout /t 2 /nobreak >NUL
tasklist /FI "PID eq %PID%" 2>NUL | find /I "python.exe" >NUL
if %errorlevel% equ 0 (
    echo [%DATE% %TIME%] WARNING: Process may still be running
) else (
    echo [%DATE% %TIME%] Process terminated successfully
)

:: Clean up PID file
del "%PID_FILE%" 2>NUL

:success
echo ============================================
echo QuantX API Server stopped successfully!
echo ============================================
echo Windows Scheduled Task has been removed
echo All service processes have been terminated
echo Temporary files have been cleaned up
echo ============================================
goto :end

:error
echo ============================================
echo FAILED TO STOP QUANTX API SERVER
echo ============================================
exit /b 1

:end
endlocal
