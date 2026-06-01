@echo off
setlocal enabledelayedexpansion

:: Production Startup Script for QuantX API Server
echo ============================================
echo Starting QuantX API Server (Production Mode)
echo ============================================
echo [%DATE% %TIME%] Starting QuantX API Server...

:: Set script directory
cd /d "%~dp0"

:: Create logs directory if it doesn't exist
if not exist "logs" mkdir logs

:: Set log file path
set PID_FILE=quantx.pid

:: Check if already running
if exist "%PID_FILE%" (
    echo [%DATE% %TIME%] Checking for existing process...
    for /f "tokens=*" %%i in (%PID_FILE%) do (
        tasklist /FI "PID eq %%i" 2>NUL | find /I "python.exe" >NUL
        if !errorlevel! equ 0 (
            echo [%DATE% %TIME%] ERROR: QuantX API Server is already running (PID: %%i)
            echo [%DATE% %TIME%] Use stop.bat to stop the service first
            goto :error
        )
        if !errorlevel! neq 0 (
            echo [%DATE% %TIME%] Removing stale PID file...
            del "%PID_FILE%" 2>NUL
        )
    )
)

:: Select Python executable. Set QUANTX_PYTHON_EXE to override.
if not defined QUANTX_PYTHON_EXE set QUANTX_PYTHON_EXE=python

:: Check Python availability
"%QUANTX_PYTHON_EXE%" --version >NUL 2>&1
if %errorlevel% neq 0 (
    echo [%DATE% %TIME%] ERROR: Python executable is not available: %QUANTX_PYTHON_EXE%
    goto :error
)

echo [%DATE% %TIME%] Python executable: %QUANTX_PYTHON_EXE%

:: Set production environment variables
echo [%DATE% %TIME%] Setting production environment variables...
set DEBUG=False
set ENV=production
set TZ=Asia/Shanghai
set TIMEZONE=Asia/Shanghai
set TRADING_TIMEZONE=Asia/Shanghai
set TASK_NAME=QuantX-API-Server
echo [%DATE% %TIME%] Environment variables set: DEBUG=%DEBUG%, ENV=%ENV%, TZ=%TZ%

:: Check if scheduled task already exists and clean up
schtasks /query /tn "%TASK_NAME%" >NUL 2>&1
if !errorlevel! equ 0 (
    echo [%DATE% %TIME%] Removing existing scheduled task...
    schtasks /delete /tn "%TASK_NAME%" /f >NUL 2>&1
)

:: Clean up any existing service runner script
if exist "quantx_service_runner.bat" (
    del "quantx_service_runner.bat" 2>NUL
)

:: Create the service runner script (separate file to avoid self-deletion)
echo [%DATE% %TIME%] Creating service runner script...

(
echo @echo off
echo cd /d "%%~dp0"
echo if not exist logs mkdir logs
echo set DEBUG=False
echo set ENV=production
echo set TZ=Asia/Shanghai
echo set TIMEZONE=Asia/Shanghai
echo set TRADING_TIMEZONE=Asia/Shanghai
echo set PYTHONIOENCODING=utf-8
echo "%QUANTX_PYTHON_EXE%" main.py ^>^> logs\server.log 2^>^&1
) > quantx_service_runner.bat

:: Calculate future time for scheduled task (1 minute from now)
for /f "tokens=1-2 delims=:." %%a in ("%TIME%") do (
    :: Remove leading zeros to avoid octal interpretation
    set hour_str=%%a
    set minute_str=%%b

    :: Remove leading zeros
    set hour_str=!hour_str: =!
    set minute_str=!minute_str: =!
    if "!hour_str:~0,1!"=="0" set hour_str=!hour_str:~1!
    if "!minute_str:~0,1!"=="0" set minute_str=!minute_str:~1!
    if "!hour_str!"=="" set hour_str=0
    if "!minute_str!"=="" set minute_str=0

    set /a hour=!hour_str!
    set /a minute=!minute_str!+1
    if !minute! geq 60 (
        set /a minute-=60
        set /a hour+=1
    )
    if !hour! geq 24 set /a hour-=24
    if !hour! lss 10 set hour=0!hour!
    if !minute! lss 10 set minute=0!minute!
    set TASK_TIME=!hour!:!minute!
)

:: Create the scheduled task
echo [%DATE% %TIME%] Creating Windows scheduled task...
schtasks /create /tn "%TASK_NAME%" /tr "\"%~dp0quantx_service_runner.bat\"" /sc once /st !TASK_TIME! /rl HIGHEST /f >NUL 2>&1

if !errorlevel! neq 0 (
    echo [%DATE% %TIME%] WARNING: Failed to create task with elevated privileges, trying with current user...
    schtasks /create /tn "%TASK_NAME%" /tr "\"%~dp0quantx_service_runner.bat\"" /sc once /st !TASK_TIME! /f >NUL 2>&1
    if !errorlevel! neq 0 (
        echo [%DATE% %TIME%] ERROR: Failed to create scheduled task
        del "quantx_service_runner.bat" 2>NUL
        goto :error
    )
)

:: Start the scheduled task immediately
echo [%DATE% %TIME%] Starting QuantX API Server via scheduled task...
schtasks /run /tn "%TASK_NAME%" >NUL 2>&1

if !errorlevel! neq 0 (
    echo [%DATE% %TIME%] ERROR: Failed to start the scheduled task
    schtasks /delete /tn "%TASK_NAME%" /f >NUL 2>&1
    del "quantx_service_runner.bat" 2>NUL
    goto :error
)

:: Wait for the service to start (retry loop with timeout)
echo [%DATE% %TIME%] Waiting for service to start...
set /a MAX_RETRIES=12
set /a RETRY_COUNT=0

:retry_loop
%SystemRoot%\System32\timeout.exe /t 5 /nobreak >NUL 2>&1
set /a RETRY_COUNT+=1

:: Find the python process running main.py (check command line)
for /f "tokens=2" %%i in ('wmic process where "name='python.exe' and commandline like '%%main.py%%'" get processid /format:value 2^>NUL ^| %SystemRoot%\System32\find.exe "ProcessId"') do (
    set FOUND_PID=%%i
    if defined FOUND_PID (
        echo !FOUND_PID! > "%PID_FILE%"
        echo [%DATE% %TIME%] QuantX API Server started successfully (PID: !FOUND_PID!)
        goto :success
    )
)

:: Check if any python process is listening on port 8000
netstat -ano | %SystemRoot%\System32\find.exe ":8000" | %SystemRoot%\System32\find.exe "LISTENING" >NUL
if %errorlevel% equ 0 (
    for /f "tokens=5" %%p in ('netstat -ano ^| %SystemRoot%\System32\find.exe ":8000" ^| %SystemRoot%\System32\find.exe "LISTENING"') do (
        echo %%p > "%PID_FILE%"
        echo [%DATE% %TIME%] QuantX API Server started successfully (PID: %%p)
        goto :success
    )
)

:: Retry if max retries not reached
if !RETRY_COUNT! lss !MAX_RETRIES! (
    echo [%DATE% %TIME%] Retry !RETRY_COUNT!/!MAX_RETRIES!: Service not ready yet...
    goto :retry_loop
)

echo [%DATE% %TIME%] ERROR: Failed to start QuantX API Server after !MAX_RETRIES! retries
echo [%DATE% %TIME%] Check logs\server.log for details
goto :error

:: If PID not found, check if process started
tasklist /FI "IMAGENAME eq python.exe" 2>NUL | %SystemRoot%\System32\find.exe /I "python.exe" >NUL
if %errorlevel% equ 0 (
    echo [%DATE% %TIME%] QuantX API Server appears to be running (PID detection failed)
    goto :success
) else (
    echo [%DATE% %TIME%] ERROR: Failed to start QuantX API Server
    goto :error
)

:success
echo ============================================
echo QuantX API Server started successfully!
echo ============================================
echo The service is running as a Windows Scheduled Task
echo and will survive terminal closures and system reboots.
echo.
echo Task Name: %TASK_NAME%
echo PID File: %PID_FILE%
echo.
echo To stop the service, run: stop.bat
echo ============================================
goto :end

:error
echo ============================================
echo FAILED TO START QUANTX API SERVER
echo ============================================
echo ============================================
:: Clean up on error
schtasks /delete /tn "%TASK_NAME%" /f >NUL 2>&1
del "quantx_service_runner.bat" >NUL 2>&1
exit /b 1

:end
endlocal
