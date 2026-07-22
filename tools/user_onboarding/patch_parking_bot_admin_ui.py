#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import shutil
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BOT = PROJECT_ROOT / "Bots" / "parking_bot.py"
BACKUP_ROOT = PROJECT_ROOT / "Data" / "backups" / "source_code"


IMPORT_BLOCK = """
from tools.user_onboarding.admin_ui import (
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
    folder = BACKUP_ROOT / f"before_user_onboarding_admin_ui_{stamp}"
    folder.mkdir(parents=True, exist_ok=True)
    dst = folder / path.name
    shutil.copy2(path, dst)
    return dst


def insert_import(text: str) -> tuple[str, bool]:
    if "handle_user_onboarding_admin_text" in text:
        return text, False

    marker = "from telegram.ext import"
    pos = text.find(marker)
    if pos == -1:
        return text + "\n" + IMPORT_BLOCK.strip() + "\n", True

    line_end = text.find("\n", pos)
    return text[:line_end + 1] + IMPORT_BLOCK + text[line_end + 1:], True


def add_settings_button(text: str) -> tuple[str, bool]:
    if "👥 Пользователи и роли" in text or "BTN_USERS_ROLES" in text:
        return text, False

    # Conservative text injection: replace visible settings placeholder if it exists.
    candidates = [
        "Здесь будут настройки",
        "Тут будут настройки",
        "Раздел настроек",
    ]
    for c in candidates:
        if c in text:
            return text.replace(c, c + "\\n\\n👥 Пользователи и роли"), True

    # If no obvious placeholder, do not guess.
    return text, False


def insert_router(text: str) -> tuple[str, bool]:
    if "OSBB user onboarding / access roles admin workspace" in text:
        return text, False

    anchors = [
        "if text == \"⚙️ Настройки\"",
        "if text == '⚙️ Настройки'",
        "elif text == \"⚙️ Настройки\"",
        "elif text == '⚙️ Настройки'",
    ]
    for anchor in anchors:
        pos = text.find(anchor)
        if pos != -1:
            line_start = text.rfind("\n", 0, pos) + 1
            return text[:line_start] + ROUTER_BLOCK + text[line_start:], True

    # Safer fallback: before unknown text fallback phrases
    fallbacks = [
        "await update.message.reply_text(\"Не понял",
        "await update.message.reply_text('Не понял",
    ]
    for anchor in fallbacks:
        pos = text.find(anchor)
        if pos != -1:
            line_start = text.rfind("\n", 0, pos) + 1
            return text[:line_start] + ROUTER_BLOCK + text[line_start:], True

    return text, False


def main() -> int:
    ap = argparse.ArgumentParser(description="Patch parking_bot.py with user onboarding admin UI.")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    if not BOT.exists():
        raise SystemExit(f"Bot not found: {BOT}")

    old = BOT.read_text(encoding="utf-8")
    new = old
    changes = []

    new, changed = insert_import(new)
    if changed:
        changes.append("import user_onboarding.admin_ui")

    new, changed = add_settings_button(new)
    if changed:
        changes.append("settings placeholder mentions users/roles")

    new, changed = insert_router(new)
    if changed:
        changes.append("route user onboarding admin text")

    print("=" * 90)
    print("PATCH USER ONBOARDING ADMIN UI")
    print("=" * 90)
    print("Bot:", BOT)
    print("Apply:", args.apply)
    print("Changes:", "; ".join(changes) if changes else "no changes")
    print("Changes needed:", new != old)

    if new == old:
        print("Nothing to do.")
        return 0

    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    preview = PROJECT_ROOT / "Recovered" / f"parking_bot_user_onboarding_admin_ui_{stamp}.patched.py"
    preview.parent.mkdir(parents=True, exist_ok=True)
    preview.write_text(new, encoding="utf-8")
    print("Preview:", preview)

    if not args.apply:
        print("DRY RUN ONLY - no files changed")
        print("To apply:")
        print("python .\\OSBB\\tools\\user_onboarding\\patch_parking_bot_admin_ui.py --apply")
        return 0

    b = backup(BOT)
    BOT.write_text(new, encoding="utf-8")
    print("Backup:", b)
    print("APPLIED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
