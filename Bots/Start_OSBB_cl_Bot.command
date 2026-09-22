#!/bin/bash
# Start_OSBB_cl_Bot.command
#
# Mac-аналог Start_OSBB_cl_Bot.bat (Windows).
#   Windows: G:\Programming\OSBB_cl        -> Mac: /Users/san/Developer/Projects/OSBB_cl
#   Windows: G:\Prog_secret                -> Mac: /Secret/Soft
#   Windows: .venv\Scripts\python.exe      -> Mac: .venv/bin/python
#
# Перед первым запуском (один раз): chmod +x Start_OSBB_cl_Bot.command

PROJECT_ROOT="/Users/san/Developer/Projects/OSBB_cl"
SECRETS_DIR="/Secret/Soft"
SECRETS_FILE="${SECRETS_DIR}/telegram_osbb.py"
# /Secret is a synthetic link; macOS mounts the APFS volume at this real path.
SECRET_REAL_MOUNT="/Users/san/SecretMount"

STARTED_AT="$(date '+%Y-%m-%d %H:%M:%S')"

echo -ne "\033]0;OSBB_cl Bot - MAIN - started ${STARTED_AT}\007"

echo "============================================================"
echo ""
echo "             OSBB_cl Telegram Bot - MAIN"
echo ""
echo "  Started at : ${STARTED_AT}"
echo "  Mode       : Production code (clean OSBB_cl branch)"
echo "  Bot        : Bots/parking_bot.py"
echo "  Python     : ${PROJECT_ROOT}/.venv/bin/python"
echo ""
echo "============================================================"
echo ""

if ! /sbin/mount | /usr/bin/grep -Fq " on ${SECRET_REAL_MOUNT} "; then
    echo "ОШИБКА: зашифрованный раздел Secret не смонтирован."
    echo "Ожидалась точка монтирования: ${SECRET_REAL_MOUNT} (доступна как /Secret)."
    echo "Выполните secret-on или запустите бота командой osbb-bot."
    read -p "Нажмите Enter для выхода..."
    exit 1
fi

if [ ! -r "$SECRETS_FILE" ]; then
    echo "ОШИБКА: не найден или недоступен файл секретов: $SECRETS_FILE"
    echo "Подключите раздел 'Secret' и запустите заново."
    read -p "Нажмите Enter для выхода..."
    exit 1
fi

echo "  Secret     : mounted (${SECRETS_FILE})"
echo ""

export PYTHONPATH="${SECRETS_DIR}:${PYTHONPATH}"

cd "${PROJECT_ROOT}/Bots" || {
    echo "ОШИБКА: не найдена папка Bots в ${PROJECT_ROOT}"
    read -p "Нажмите Enter для выхода..."
    exit 1
}

"${PROJECT_ROOT}/.venv/bin/python" parking_bot.py

echo ""
echo "============================================================"
echo "Bot finished. Started at: ${STARTED_AT}"
echo "============================================================"
read -p "Нажмите Enter для выхода..."
