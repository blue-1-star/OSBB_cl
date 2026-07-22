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

    result = await client(GetDialogFiltersRequest())

    for f in result.filters:
        title = str(getattr(f, "title", ""))
        folder_id = getattr(f, "id", None)

        if "OSBB" in title:
            print("=" * 70)
            print("FOUND OSBB FILTER")
            print("=" * 70)
            print("ID:", folder_id)
            print("TITLE:", title)
            print("TYPE:", type(f))
            print()

            print("include_peers:")
            for p in getattr(f, "include_peers", []):
                print(" ", p)

            print()
            print("exclude_peers:")
            for p in getattr(f, "exclude_peers", []):
                print(" ", p)

    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())