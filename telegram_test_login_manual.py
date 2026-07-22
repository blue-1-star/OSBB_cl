from pathlib import Path
import sys
import asyncio

from telethon import TelegramClient

OSBB_ROOT = Path(__file__).resolve().parent
PY_ROOT = OSBB_ROOT.parent

if str(PY_ROOT) not in sys.path:
    sys.path.insert(0, str(PY_ROOT))

from config import paths

sys.path.insert(0, str(paths.SECRETS_DIR))

from telegram_osbb import API_ID, API_HASH, PHONE


SESSION_FILE = paths.OSBB_DB_DIR / "telegram_osbb_session"


async def main():
    print("Session:", SESSION_FILE)
    print("Phone:", PHONE)

    client = TelegramClient(str(SESSION_FILE), API_ID, API_HASH)

    await client.connect()

    if await client.is_user_authorized():
        me = await client.get_me()
        print("Already authorized")
        print("User ID:", me.id)
        print("Username:", me.username)
        print("Name:", me.first_name, me.last_name)
        await client.disconnect()
        return

    print("Sending code request...")
    sent = await client.send_code_request(PHONE)

    print("Code request sent.")
    print("phone_code_hash received:", bool(sent.phone_code_hash))

    code = input("Enter Telegram login code: ").strip()

    await client.sign_in(
        phone=PHONE,
        code=code,
        phone_code_hash=sent.phone_code_hash,
    )

    me = await client.get_me()

    print("Logged in successfully")
    print("User ID:", me.id)
    print("Username:", me.username)
    print("Name:", me.first_name, me.last_name)

    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())