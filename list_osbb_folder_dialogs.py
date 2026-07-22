from pathlib import Path
import sys
import asyncio
from datetime import datetime

from telethon import TelegramClient
from telethon.tl.types import User, Chat, Channel

OSBB_ROOT = Path(__file__).resolve().parent
PY_ROOT = OSBB_ROOT.parent

if str(PY_ROOT) not in sys.path:
    sys.path.insert(0, str(PY_ROOT))

from config import paths

sys.path.insert(0, str(paths.SECRETS_DIR))

from telegram_osbb import API_ID, API_HASH


SESSION_FILE = paths.OSBB_DB_DIR / "telegram_osbb_session"
OSBB_FOLDER_ID = 6


def now_ts():
    return datetime.now().strftime("%Y-%m-%d_%H-%M-%S")


def dialog_type(entity):
    if isinstance(entity, User):
        if entity.bot:
            return "bot"
        return "personal_chat"

    if isinstance(entity, Channel):
        if entity.broadcast:
            return "channel"
        if entity.megagroup:
            return "supergroup"
        return "channel_or_group"

    if isinstance(entity, Chat):
        return "group"

    return "unknown"


async def main():
    report_dir = paths.OSBB_EXPORTS_DIR / "telegram"
    report_dir.mkdir(parents=True, exist_ok=True)

    report_file = report_dir / f"osbb_folder_dialogs_{now_ts()}.txt"

    client = TelegramClient(str(SESSION_FILE), API_ID, API_HASH)

    await client.connect()

    if not await client.is_user_authorized():
        print("Not authorized.")
        await client.disconnect()
        return

    lines = []
    lines.append("=" * 80)
    lines.append("OSBB TELEGRAM FOLDER DIALOGS")
    lines.append("=" * 80)
    lines.append(f"Folder ID : {OSBB_FOLDER_ID}")
    lines.append(f"Generated : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("")

    count = 0

    async for dialog in client.iter_dialogs(folder=OSBB_FOLDER_ID):
        entity = dialog.entity

        dtype = dialog_type(entity)

        title = dialog.name or "-"
        entity_id = getattr(entity, "id", None)
        username = getattr(entity, "username", None)

        lines.append("-" * 80)
        lines.append(f"Title     : {title}")
        lines.append(f"Type      : {dtype}")
        lines.append(f"ID        : {entity_id}")
        lines.append(f"Username  : {username}")
        lines.append(f"Unread    : {dialog.unread_count}")
        lines.append(f"Pinned    : {dialog.pinned}")

        count += 1

    lines.insert(3, f"Dialogs count: {count}")

    report_file.write_text("\n".join(lines), encoding="utf-8")

    await client.disconnect()

    print("OSBB folder dialogs exported.")
    print("Count:", count)
    print("Report:")
    print(report_file)


if __name__ == "__main__":
    asyncio.run(main())