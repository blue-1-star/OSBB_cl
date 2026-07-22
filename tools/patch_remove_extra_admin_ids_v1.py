#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
patch_remove_extra_admin_ids_v1.py

Removes temporary OSBB_EXTRA_ADMIN_IDS support from parking_bot.py.

DRY RUN by default. Use --apply to write.
Use only after extra admins are stored in bot_admins.
"""

from __future__ import annotations

import argparse
import re
import shutil
from datetime import datetime
from pathlib import Path

DEFAULT_BOT = r"G:\Programming\Py\OSBB\Bots\parking_bot.py"

def backup(path: Path) -> Path:
    bdir = path.parent / "backups"
    bdir.mkdir(parents=True, exist_ok=True)
    dst = bdir / f"{path.stem}_before_remove_extra_admin_ids_{datetime.now():%Y-%m-%d_%H-%M-%S}{path.suffix}"
    shutil.copy2(path, dst)
    return dst

def patch_text(src: str) -> tuple[str, list[str]]:
    changes = []
    text = src

    lines = []
    removed = 0
    for line in text.splitlines():
        if "OSBB_EXTRA_ADMIN_IDS" in line:
            removed += 1
            continue
        lines.append(line)
    if removed:
        text = "\n".join(lines) + "\n"
        changes.append(f"remove {removed} OSBB_EXTRA_ADMIN_IDS line(s)")

    text2 = re.sub(r"\s*or\s+user_id\s+in\s+EXTRA_ADMIN_IDS", "", text)
    text2 = re.sub(r"\s*or\s+user_id\s+in\s+extra_admin_ids", "", text2)
    if text2 != text:
        text = text2
        changes.append("remove extra admin condition from is_admin_user")

    text2 = re.sub(
        r"\n#\s*OSBB_ADMIN_ACCESS_V1.*?(?=\ndef\s+is_admin_user|\nasync\s+def\s+|\n#\s*=|\Z)",
        "\n",
        text,
        flags=re.S,
    )
    if text2 != text:
        text = text2
        changes.append("remove OSBB_ADMIN_ACCESS_V1 helper/comment block")

    if not changes:
        changes.append("no obvious temporary admin access code found")

    return text, changes

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bot", default=DEFAULT_BOT)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    bot = Path(args.bot)
    if not bot.exists():
        raise SystemExit(f"Bot file not found: {bot}")

    src = bot.read_text(encoding="utf-8")
    dst, changes = patch_text(src)

    print("=" * 100)
    print("Patch remove OSBB_EXTRA_ADMIN_IDS")
    print("=" * 100)
    print("Mode:", "APPLY" if args.apply else "DRY RUN")
    print("Bot:", bot)
    print("Changed:", dst != src)
    print("Changes:")
    for c in changes:
        print(" -", c)

    if not args.apply:
        print("")
        print("DRY RUN COMPLETED. Re-run with --apply to write.")
        return 0

    if dst != src:
        b = backup(bot)
        bot.write_text(dst, encoding="utf-8")
        print("Backup:", b)
    print("APPLY COMPLETED")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
