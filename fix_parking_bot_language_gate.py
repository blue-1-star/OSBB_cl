"""
Исправляет ошибочную вставку строгого контроля языка в show_mode_menu().

Ошибка проявляется так:
  NameError: name 'user_id' is not defined
  в функции show_mode_menu()

Причина:
  первая версия patch_parking_bot_client_portal.py нашла первое `t = TEXTS[lang]`
  в файле — оно было в show_mode_menu(), а не в message_handler().

Этот fixer:
- удаляет ошибочный блок откуда бы он ни был вставлен;
- добавляет этот блок только в message_handler(), после обработки кнопок выбора языка;
- делает backup parking_bot.py;
- не меняет БД.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import argparse
import re
import shutil


ROOT = Path(__file__).resolve().parent
BOT_FILE = ROOT / "Bots" / "parking_bot.py"

STRICT_GATE = """    # =========================
    # Строгий выбор языка для клиентского кабинета
    # =========================
    # После перезапуска язык выбирается заново: это намеренно.
    # До выбора языка не допускаем переход в русское меню «по умолчанию».
    if user_id not in user_languages:
        await update.message.reply_text(
            TEXTS["ru"]["choose_language"],
            reply_markup=kb(LANG_MENU),
        )
        return

"""


def backup_name(path: Path) -> Path:
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    return path.with_name(f"{path.stem}_before_language_gate_fix_{stamp}{path.suffix}")


def remove_all_strict_gates(source: str) -> tuple[str, int]:
    pattern = re.compile(
        r"    # =========================\n"
        r"    # Строгий выбор языка для клиентского кабинета\n"
        r"    # =========================\n"
        r"    # После перезапуска язык выбирается заново: это намеренно\.\n"
        r"    # До выбора языка не допускаем переход в русское меню «по умолчанию»\.\n"
        r"    if user_id not in user_languages:\n"
        r"        await update\.message\.reply_text\(\n"
        r'            TEXTS\["ru"\]\["choose_language"\],\n'
        r"            reply_markup=kb\(LANG_MENU\),\n"
        r"        \)\n"
        r"        return\n\n"
    )
    return pattern.subn("", source)


def insert_gate_only_in_message_handler(source: str) -> tuple[str, bool]:
    handler_start = source.find("async def message_handler(")
    if handler_start < 0:
        raise RuntimeError("Не найдена функция message_handler().")

    next_handler = source.find("\n\nasync def ", handler_start + 1)
    if next_handler < 0:
        next_handler = len(source)

    before = source[:handler_start]
    handler = source[handler_start:next_handler]
    after = source[next_handler:]

    if "Строгий выбор языка для клиентского кабинета" in handler:
        return source, False

    marker = "    t = TEXTS[lang]\n"
    if marker not in handler:
        raise RuntimeError("В message_handler() не найдено `t = TEXTS[lang]`.")

    # В message_handler этот marker расположен после трёх обработчиков
    # кнопок языка, поэтому выбранный язык не блокируется.
    handler = handler.replace(marker, STRICT_GATE + marker, 1)
    return before + handler + after, True


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    if not BOT_FILE.exists():
        raise SystemExit(f"Не найден файл: {BOT_FILE}")

    original = BOT_FILE.read_text(encoding="utf-8")
    patched, removed_count = remove_all_strict_gates(original)
    patched, inserted = insert_gate_only_in_message_handler(patched)

    try:
        compile(patched, str(BOT_FILE), "exec")
    except SyntaxError as exc:
        raise SystemExit(
            f"После исправления получился синтаксический конфликт: {exc}\n"
            "Исходный файл не изменён."
        )

    print("=" * 96)
    print("FIX LANGUAGE GATE")
    print("=" * 96)
    print("Bot:", BOT_FILE)
    print("Apply:", args.apply)
    print("Wrong blocks removed:", removed_count)
    print("Correct block inserted in message_handler:", inserted)
    print("Changes needed:", patched != original)

    if not args.apply:
        print()
        print("DRY RUN COMPLETED - NO CHANGES SAVED")
        return

    if patched == original:
        print()
        print("ALREADY CORRECT - NO FILE CHANGE")
        return

    backup = backup_name(BOT_FILE)
    shutil.copy2(BOT_FILE, backup)
    BOT_FILE.write_text(patched, encoding="utf-8")

    print("Backup:", backup)
    print("APPLIED")


if __name__ == "__main__":
    main()
