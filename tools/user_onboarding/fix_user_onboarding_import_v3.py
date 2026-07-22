from __future__ import annotations
import argparse
import shutil
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

def backup(path: Path) -> Path:
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    folder = BACKUP_ROOT / f"before_user_onboarding_import_fix_{stamp}"
    folder.mkdir(parents=True, exist_ok=True)
    dst = folder / path.name
    shutil.copy2(path, dst)
    return dst

def remove_existing_import(text: str):
    changed = False
    while True:
        start = text.find("from tools.user_onboarding.admin_ui import (")
        if start == -1:
            break
        end = text.find(")\n", start)
        if end == -1:
            break
        text = text[:start] + text[end + 2:]
        changed = True
    return text, changed

def find_import_position(text: str) -> int:
    marker = "sys.path.insert(0, str(paths.SECRETS_DIR))"
    pos = text.find(marker)
    if pos != -1:
        end = text.find("\n", pos)
        return end + 1 if end != -1 else len(text)
    last = text.rfind("sys.path.insert", 0, 3000)
    if last != -1:
        end = text.find("\n", last)
        return end + 1 if end != -1 else len(text)
    pos = text.find("from telegram.ext import")
    if pos != -1:
        end = text.find("\n", pos)
        return end + 1 if end != -1 else len(text)
    return 0

def main() -> int:
    ap = argparse.ArgumentParser(description="Fix user_onboarding admin import in parking_bot.py.")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    old = BOT.read_text(encoding="utf-8")
    new, removed = remove_existing_import(old)
    pos = find_import_position(new)
    inserted = False
    if "from tools.user_onboarding.admin_ui import" not in new:
        new = new[:pos] + "\n" + IMPORT_BLOCK + new[pos:]
        inserted = True

    print("=" * 90)
    print("FIX USER ONBOARDING ADMIN IMPORT V3")
    print("=" * 90)
    print("Bot:", BOT)
    print("Apply:", args.apply)
    print("Removed old import:", removed)
    print("Inserted import:", inserted)
    print("Changes needed:", new != old)

    if new == old:
        print("Nothing to do.")
        return 0

    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    preview = PROJECT_ROOT / "Recovered" / f"parking_bot_user_onboarding_import_fix_v3_{stamp}.patched.py"
    preview.parent.mkdir(parents=True, exist_ok=True)
    preview.write_text(new, encoding="utf-8")
    print("Preview:", preview)

    if not args.apply:
        print("DRY RUN ONLY - no files changed")
        print("To apply:")
        print("python .\\OSBB\\tools\\user_onboarding\\fix_user_onboarding_import_v3.py --apply")
        return 0

    b = backup(BOT)
    BOT.write_text(new, encoding="utf-8")
    print("Backup:", b)
    print("APPLIED")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
