@echo off
setlocal

set STARTED_AT=%DATE% %TIME%
title OSBB_cl Bot - MAIN - started %STARTED_AT%

echo ============================================================
echo.
echo              OSBB_cl Telegram Bot - MAIN
echo.
echo   Started at : %STARTED_AT%
echo   Mode       : Production code ((clean OSBB_cl branch))
echo   Bot        : Bots\parking_bot.py
echo   Python     : G:\Programming\OSBB_cl\.venv\Scripts\python.exe
echo.
echo ============================================================
echo.

rem Секреты бота (TOKEN, ADMIN_IDS) лежат вне репозитория.
set PYTHONPATH=G:\Prog_secret;%PYTHONPATH%

cd /d G:\Programming\OSBB_cl\Bots

G:\Programming\OSBB_cl\.venv\Scripts\python.exe parking_bot.py

echo.
echo ============================================================
echo Bot finished. Started at:  %STARTED_AT%
echo ============================================================
pause