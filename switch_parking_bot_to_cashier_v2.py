"""
Переключает bot с кассы/кабинета v1 на v2 без удаления v1.

Что меняется в Bots/parking_bot.py:
- handlers.client_portal -> handlers.client_portal_v2
- handlers.cashier_operator -> handlers.cashier_operator_v2
- вызов handle_cashier_operator_text -> handle_cashier_operator_v2_text

Старые файлы остаются рядом как резерв и fallback.
Patcher создаёт backup parking_bot.py перед --apply.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import argparse
import shutil


ROOT = Path(__file__).resolve().parent
BOT_FILE = ROOT / "Bots" / "parking_bot.py"
FILES = [
    ROOT / "Bots" / "handlers" / "client_portal_v2.py",
    ROOT / "Bots" / "handlers" / "cashier_operator_v2.py",
    ROOT / "cashier_v2_core.py",
]


def backup_name(path: Path) -> Path:
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    return path.with_name(f"{path.stem}_before_cashier_v2_{stamp}{path.suffix}")


def patch(source: str) -> tuple[str, list[str]]:
    changes: list[str] = []

    old = "from handlers.client_portal import ("
    new = "from handlers.client_portal_v2 import ("
    if old in source:
        source = source.replace(old, new, 1)
        changes.append("client_portal -> client_portal_v2")

    old = "from handlers.cashier_operator import handle_cashier_operator_text"
    new = "from handlers.cashier_operator_v2 import handle_cashier_operator_v2_text"
    if old in source:
        source = source.replace(old, new, 1)
        changes.append("cashier_operator import -> v2")

    old = "handle_cashier_operator_text("
    new = "handle_cashier_operator_v2_text("
    if old in source:
        source = source.replace(old, new)
        changes.append("cashier handler call -> v2")

    # Idempotence checks: report current state even on rerun.
    client_v2 = "from handlers.client_portal_v2 import (" in source
    cashier_v2 = (
        "from handlers.cashier_operator_v2 import handle_cashier_operator_v2_text"
        in source
    )
    if client_v2 and not any("client_portal" in item for item in changes):
        changes.append("client_portal already v2")
    if cashier_v2 and not any("cashier_operator import" in item for item in changes):
        changes.append("cashier operator already v2")

    if not client_v2:
        raise RuntimeError(
            "Не найден импорт client_portal. "
            "В текущем parking_bot.py нет ожидаемого подключения клиентского кабинета."
        )
    if not cashier_v2:
        raise RuntimeError(
            "Не найден импорт cashier_operator. "
            "В текущем parking_bot.py нет ожидаемого подключения кассы v1."
        )

    return source, changes


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    print("=" * 100)
    print("SWITCH PARKING BOT TO CASHIER V2")
    print("=" * 100)
    print("Bot:", BOT_FILE)
    print("Apply:", args.apply)

    missing = [str(path) for path in FILES if not path.exists()]
    if missing:
        raise SystemExit(
            "Сначала разместите файлы v2:\n" + "\n".join(f"  {path}" for path in missing)
        )
    if not BOT_FILE.exists():
        raise SystemExit(f"Не найден parking_bot.py: {BOT_FILE}")

    original = BOT_FILE.read_text(encoding="utf-8")
    patched, changes = patch(original)

    try:
        compile(patched, str(BOT_FILE), "exec")
    except SyntaxError as exc:
        raise SystemExit(
            f"После переключения код не компилируется: {exc}\n"
            "Исходный файл не изменён."
        )

    print("Changes:")
    for item in changes:
        print(" -", item)
    print("Changes needed:", patched != original)

    if not args.apply:
        print()
        print("DRY RUN COMPLETED - NO CHANGES SAVED")
        return

    if patched == original:
        print()
        print("ALREADY SWITCHED - NO FILE CHANGE")
        return

    backup = backup_name(BOT_FILE)
    shutil.copy2(BOT_FILE, backup)
    BOT_FILE.write_text(patched, encoding="utf-8")
    print("Backup:", backup)
    print("APPLIED")


if __name__ == "__main__":
    main()
