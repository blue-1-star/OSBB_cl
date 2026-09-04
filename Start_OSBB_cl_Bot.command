#!/bin/bash
PROJECT_ROOT="/Users/san/Developer/Projects/OSBB_cl"
SECRETS_DIR="/Volumes/Secret/Soft"

STARTED_AT="$(date '+%Y-%m-%d %H:%M:%S')"

echo -ne "\033]0;OSBB_cl Bot - MAIN - started ${STARTED_AT}\007"

echo "============================================================"
echo ""
echo "             OSBB_cl Telegram Bot - MAIN"
echo ""
echo "  Started at : ${STARTED_AT}"
echo "  Mode       : Production code (clean OSBB_cl branch)"
echo "  Bot        : Bots/parking_bot.py"
echo "  Python     : ${PROJECT_ROOT}/venv/bin/python"
echo ""
echo "============================================================"
echo ""

if [ ! -d "$SECRETS_DIR" ]; then
    echo "ОШИБКА: не найден раздел с секретами: $SECRETS_DIR"
    echo "Подключите раздел 'Secret' и запустите заново."
    read -p "Нажмите Enter для выхода..."
    exit 1
fi

export PYTHONPATH="${SECRETS_DIR}:${PYTHONPATH}"

cd "${PROJECT_ROOT}/Bots" || {
    echo "ОШИБКА: не найдена папка Bots в ${PROJECT_ROOT}"
    read -p "Нажмите Enter для выхода..."
    exit 1
}

"${PROJECT_ROOT}/venv/bin/python" parking_bot.py

echo ""
echo "============================================================"
echo "Bot finished. Started at: ${STARTED_AT}"
echo "============================================================"
read -p "Нажмите Enter для выхода..."
