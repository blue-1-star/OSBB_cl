from pathlib import Path
import sys
import asyncio
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
FOLDER_TITLE = "OSBB"


def now_ts():
    return datetime.now().strftime("%Y-%m-%d_%H-%M-%S")


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
        parts = [
            getattr(entity, "first_name", None),
            getattr(entity, "last_name", None),
        ]
        title = " ".join(p for p in parts if p)
        return title or getattr(entity, "username", None) or str(entity.id)

    return getattr(entity, "title", None) or getattr(entity, "username", None) or str(entity.id)


async def main():
    report_dir = paths.OSBB_EXPORTS_DIR / "telegram"
    report_dir.mkdir(parents=True, exist_ok=True)

    report_file = report_dir / f"osbb_included_peers_{now_ts()}.txt"

    client = TelegramClient(str(SESSION_FILE), API_ID, API_HASH)
    await client.connect()

    if not await client.is_user_authorized():
        print("Not authorized.")
        await client.disconnect()
        return

    result = await client(GetDialogFiltersRequest())

    osbb_filter = None

    for f in result.filters:
        title = str(getattr(f, "title", ""))

        if FOLDER_TITLE.lower() in title.lower():
            osbb_filter = f
            break

    if osbb_filter is None:
        print(f"Folder not found: {FOLDER_TITLE}")
        await client.disconnect()
        return

    include_peers = list(getattr(osbb_filter, "include_peers", []))

    lines = []
    lines.append("=" * 80)
    lines.append("OSBB INCLUDED TELEGRAM PEERS")
    lines.append("=" * 80)
    lines.append(f"Generated     : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"Folder title  : {getattr(osbb_filter, 'title', None)}")
    lines.append(f"Folder id     : {getattr(osbb_filter, 'id', None)}")
    lines.append(f"Peers count   : {len(include_peers)}")
    lines.append("")

    resolved_count = 0
    failed_count = 0

    for peer in include_peers:
        lines.append("-" * 80)

        try:
            entity = await client.get_entity(peer)

            dtype = entity_type(entity)
            title = entity_title(entity)
            entity_id = getattr(entity, "id", None)
            username = getattr(entity, "username", None)
            phone = getattr(entity, "phone", None)

            lines.append(f"Title     : {title}")
            lines.append(f"Type      : {dtype}")
            lines.append(f"ID        : {entity_id}")
            lines.append(f"Username  : {username}")
            lines.append(f"Phone     : {phone}")
            lines.append(f"Peer raw  : {peer}")

            resolved_count += 1

        except Exception as e:
            lines.append(f"FAILED TO RESOLVE PEER")
            lines.append(f"Peer raw : {peer}")
            lines.append(f"Error    : {type(e).__name__}: {e}")

            failed_count += 1

    lines.append("")
    lines.append("=" * 80)
    lines.append("SUMMARY")
    lines.append("=" * 80)
    lines.append(f"Resolved: {resolved_count}")
    lines.append(f"Failed  : {failed_count}")

    report_file.write_text("\n".join(lines), encoding="utf-8")

    await client.disconnect()

    print("OSBB included peers exported.")
    print("Resolved:", resolved_count)
    print("Failed:", failed_count)
    print("Report:")
    print(report_file)


if __name__ == "__main__":
    asyncio.run(main())