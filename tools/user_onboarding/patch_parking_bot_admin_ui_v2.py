from __future__ import annotations
import argparse, shutil
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BOT = PROJECT_ROOT / "Bots" / "parking_bot.py"
BACKUP_ROOT = PROJECT_ROOT / "Data" / "backups" / "source_code"

IMPORT_BLOCK = """from tools.user_onboarding.admin_ui import (
    BTN_USERS_ROLES,
    handle_user_onboarding_admin_text,
    show_users_roles,
)
"""

ROUTER_BLOCK = """
    # OSBB user onboarding / access roles admin workspace
    if await handle_user_onboarding_admin_text(update, context, user_states, user_id):
        return
"""

def backup(path: Path) -> Path:
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    folder = BACKUP_ROOT / f"before_user_onboarding_admin_ui_v2_{stamp}"
    folder.mkdir(parents=True, exist_ok=True)
    dst = folder / path.name
    shutil.copy2(path, dst)
    return dst

def remove_existing_import(text: str) -> tuple[str, bool]:
    start = text.find("from tools.user_onboarding.admin_ui import (")
    if start == -1:
        return text, False
    end = text.find(")\n", start)
    if end == -1:
        return text, False
    return text[:start] + text[end + 2:], True

def find_insert_position(text: str) -> int:
    marker = "sys.path.insert(0, str(p))"
    pos = text.find(marker)
    if pos != -1:
        end = text.find("\n", pos)
        return end + 1 if end != -1 else len(text)
    last = text.rfind("sys.path.insert")
    if last != -1:
        end = text.find("\n", last)
        return end + 1 if end != -1 else len(text)
    for marker in ["PY_ROOT =", "OSBB_ROOT =", "BOT_DIR ="]:
        pos = text.find(marker)
        if pos != -1:
            end = text.find("\n", pos)
            return end + 1 if end != -1 else len(text)
    pos = text.find("from telegram.ext import")
    if pos != -1:
        end = text.find("\n", pos)
        return end + 1 if end != -1 else len(text)
    return 0

def insert_import_after_sys_path(text: str) -> tuple[str, bool]:
    if "handle_user_onboarding_admin_text" in text:
        return text, False
    pos = find_insert_position(text)
    return text[:pos] + "\n" + IMPORT_BLOCK + text[pos:], True

def insert_router(text: str) -> tuple[str, bool]:
    if "OSBB user onboarding / access roles admin workspace" in text:
        return text, False
    anchors = [
        'if text == "⚙️ Настройки"',
        "if text == '⚙️ Настройки'",
        'elif text == "⚙️ Настройки"',
        "elif text == '⚙️ Настройки'",
        'await update.message.reply_text("Не понял',
        "await update.message.reply_text('Не понял",
    ]
    for anchor in anchors:
        pos = text.find(anchor)
        if pos != -1:
            line_start = text.rfind("\n", 0, pos) + 1
            return text[:line_start] + ROUTER_BLOCK + text[line_start:], True
    return text, False

def main() -> int:
    ap = argparse.ArgumentParser(description="Patch parking_bot.py with user onboarding admin UI v2.")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    old = BOT.read_text(encoding="utf-8")
    new = old
    changes = []
    new, changed = remove_existing_import(new)
    if changed:
        changes.append("remove misplaced import")
    new, changed = insert_import_after_sys_path(new)
    if changed:
        changes.append("insert import after sys.path setup")
    new, changed = insert_router(new)
    if changed:
        changes.append("route user onboarding admin text")
    print("=" * 90)
    print("PATCH USER ONBOARDING ADMIN UI V2")
    print("=" * 90)
    print("Bot:", BOT)
    print("Apply:", args.apply)
    print("Changes:", "; ".join(changes) if changes else "no changes")
    print("Changes needed:", new != old)
    if new == old:
        print("Nothing to do.")
        return 0
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    preview = PROJECT_ROOT / "Recovered" / f"parking_bot_user_onboarding_admin_ui_v2_{stamp}.patched.py"
    preview.parent.mkdir(parents=True, exist_ok=True)
    preview.write_text(new, encoding="utf-8")
    print("Preview:", preview)
    if not args.apply:
        print("DRY RUN ONLY - no files changed")
        print("To apply:")
        print("python .\\OSBB\\tools\\user_onboarding\\patch_parking_bot_admin_ui_v2.py --apply")
        return 0
    b = backup(BOT)
    BOT.write_text(new, encoding="utf-8")
    print("Backup:", b)
    print("APPLIED")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
