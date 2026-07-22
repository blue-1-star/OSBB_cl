@echo off
title OSBB_cl Telegram Bot - MAIN

echo ============================================================
echo.
echo              OSBB_cl Telegram Bot - MAIN
echo.
echo   Mode   : Production code (чистая ветка OSBB_cl)
echo   Bot    : Bots\parking_bot.py
echo   Python : G:\Programming\Py\venv\Scripts\python.exe
echo.
echo ============================================================
echo.

rem Секреты бота (TOKEN, ADMIN_IDS) лежат вне репозитория — добавляем
rem явно в PYTHONPATH, раз в старом OSBB механизм этого не был найден
rem явно (см. Start_OSBB_Bot.bat), а полагаться на догадки не стоит.
set PYTHONPATH=G:\Prog_secret;%PYTHONPATH%

cd /d G:\Programming\OSBB_cl\Bots

G:\Programming\Py\venv\Scripts\python.exe parking_bot.py

echo.
echo ============================================================
echo Bot finished.
echo ============================================================
pause