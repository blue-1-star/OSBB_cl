@echo off
setlocal EnableExtensions
chcp 65001 >nul
title OSBB - Create Test Payment for Open Service Order

set "PROJECT=G:\Programming\Py\OSBB"
set "PYTHON=G:\Programming\Py\venv\Scripts\python.exe"
set "SCRIPT=%PROJECT%\CREATE_TEST_PAYMENT_FOR_OPEN_SERVICE_ORDER.py"

echo.
echo This creates ONE clearly marked TEST payment in the LIVE SERVICES SANDBOX only.
echo It does not touch osbb.db.
echo Run it only when the live-services launcher reports:
echo matching confirmed payments for this order: 0
echo.

if not exist "%PYTHON%" (
    echo [ERROR] Python was not found:
    echo %PYTHON%
    echo.
    pause
    exit /b 1
)

if not exist "%SCRIPT%" (
    echo [ERROR] Script was not found:
    echo %SCRIPT%
    echo.
    pause
    exit /b 1
)

"%PYTHON%" "%SCRIPT%"
set "RESULT=%ERRORLEVEL%"

echo.
pause
exit /b %RESULT%
