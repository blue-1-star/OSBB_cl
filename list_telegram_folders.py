from pathlib import Path
import sys
import asyncio

from telethon import TelegramClient
from telethon.tl.functions.messages import GetDialogFiltersRequest

OSBB_ROOT = Path(__file__).resolve().parent
PY_ROOT = OSBB_ROOT.parent

if str(PY_ROOT) not in sys.path:
    sys.path.insert(0, str(PY_ROOT))

from config import paths

sys.path.insert(0, str(paths.SECRETS_DIR))

from telegram_osbb import API_ID, API_HASH


SESSION_FILE = paths.OSBB_DB_DIR / "telegram_osbb_session"


async def main():
    client = TelegramClient(str(SESSION_FILE), API_ID, API_HASH)

    await client.connect()

    if not await client.is_user_authorized():
        print("Not authorized.")
        await client.disconnect()
        return

    result = await client(GetDialogFiltersRequest())

    print("=" * 60)
    print("RAW RESULT")
    print("=" * 60)
    print(type(result))
    print(result)

    print("=" * 60)
    print("TELEGRAM FOLDERS")
    print("=" * 60)

    folder_list = getattr(result, "filters", None)

    if folder_list is None:
        print("No .filters attribute found.")
    else:
        for f in folder_list:
            folder_id = getattr(f, "id", None)
            title = getattr(f, "title", None)
            print(f"ID: {folder_id} | Title: {title}")

    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())