from pathlib import Path
import sys
import asyncio
import sqlite3
from datetime import datetime

from telethon import TelegramClient
from telethon.tl.functions.messages import GetDialogFiltersRequest
from telethon.tl.types import User, Chat, Channel

OSBB_ROOT = Path(__file__).resolve().parent
PY_ROOT = OSBB_ROOT.parent

if str(PY_ROOT) not in sys.path:
    sys.path.insert(0, str(PY_ROOT))

from config import paths

sys.path.insert(0, str(paths.SECRETS_DIR))

from telegram_osbb import API_ID, API_HASH


SESSION_FILE = paths.OSBB_DB_DIR / "telegram_osbb_session"

OSBB_FOLDER_NAME = "OSBB"


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def entity_type(entity):
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


def entity_title(entity):
    if isinstance(entity, User):
        parts = []

        if entity.first_name:
            parts.append(entity.first_name)

        if entity.last_name:
            parts.append(entity.last_name)

        return " ".join(parts)

    return getattr(entity, "title", None)


def text_to_string(value):
    if value is None:
        return ""

    if isinstance(value, str):
        return value

    if isinstance(value, list):
        parts = []

        for item in value:
            if isinstance(item, str):
                parts.append(item)

            elif isinstance(item, dict):
                parts.append(str(item.get("text", "")))

        return "".join(parts)

    return str(value)


async def main():
    db_file = paths.OSBB_TELEGRAM_DB_FILE

    conn = sqlite3.connect(db_file)
    cur = conn.cursor()

    client = TelegramClient(
        str(SESSION_FILE),
        API_ID,
        API_HASH
    )

    await client.connect()

    if not await client.is_user_authorized():
        print("Not authorized.")
        return

    result = await client(GetDialogFiltersRequest())

    osbb_filter = None

    for f in result.filters:
        title = str(getattr(f, "title", ""))

        if OSBB_FOLDER_NAME.lower() in title.lower():
            osbb_filter = f
            break

    if osbb_filter is None:
        print("OSBB folder not found.")
        return

    peers = list(getattr(osbb_filter, "include_peers", []))

    chats_added = 0
    messages_added = 0

    print(f"Peers found: {len(peers)}")

    for peer in peers:

        try:
            entity = await client.get_entity(peer)

        except Exception as e:
            print("Cannot resolve peer:", e)
            continue

        chat_title = entity_title(entity)
        chat_type = entity_type(entity)

        telegram_chat_id = str(entity.id)

        cur.execute("""
        INSERT INTO telegram_chats (
            chat_name,
            chat_type,
            telegram_chat_id,
            imported_at
        )
        VALUES (?, ?, ?, ?)
        """, (
            chat_title,
            chat_type,
            telegram_chat_id,
            now()
        ))

        chat_db_id = cur.lastrowid

        chats_added += 1

        print(
            f"[{chats_added}/{len(peers)}] "
            f"{chat_title}"
        )

        async for msg in client.iter_messages(
            entity,
            reverse=True
        ):

            text_raw = text_to_string(msg.text)

            cur.execute("""
            INSERT INTO telegram_messages (
                chat_id,
                telegram_message_id,
                message_type,
                message_date,
                sender_name,
                text_raw,
                imported_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                chat_db_id,
                str(msg.id),
                "message",
                str(msg.date) if msg.date else None,
                None,
                text_raw,
                now()
            ))

            messages_added += 1

        conn.commit()

    conn.commit()

    cur.execute("""
    SELECT COUNT(*)
    FROM telegram_chats
    """)

    total_chats = cur.fetchone()[0]

    cur.execute("""
    SELECT COUNT(*)
    FROM telegram_messages
    """)

    total_messages = cur.fetchone()[0]

    conn.close()

    await client.disconnect()

    print()
    print("=" * 70)
    print("IMPORT COMPLETED")
    print("=" * 70)
    print("Chats imported     :", chats_added)
    print("Messages imported  :", messages_added)
    print()
    print("Total chats DB     :", total_chats)
    print("Total messages DB  :", total_messages)


if __name__ == "__main__":
    asyncio.run(main())