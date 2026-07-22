from pathlib import Path
import sys

from telethon import TelegramClient

OSBB_ROOT = Path(__file__).resolve().parent
PY_ROOT = OSBB_ROOT.parent

if str(PY_ROOT) not in sys.path:
    sys.path.insert(0, str(PY_ROOT))

from config import paths
# from telegram_secrets import API_ID, API_HASH, PHONE
import sys

sys.path.insert(0, str(paths.SECRETS_DIR))

from telegram_osbb import (
    API_ID,
    API_HASH,
    PHONE,
)

SESSION_FILE = paths.OSBB_DB_DIR / "telegram_osbb_session"


async def main():
    client = TelegramClient(str(SESSION_FILE), API_ID, API_HASH)

    await client.start(phone=PHONE)

    me = await client.get_me()

    print("Logged in successfully")
    print("User ID:", me.id)
    print("Username:", me.username)
    print("Name:", me.first_name, me.last_name)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())