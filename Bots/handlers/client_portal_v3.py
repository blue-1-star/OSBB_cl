"""
Клиентский кабинет v3.

Сохраняет весь функционал client_portal_v2, но заменяет два раздельных входа:
  🔑 Пульты
  📞 Открытие по телефону
на один понятный раздел:
  🔑 Пульты и доступ

Сам процесс находится в service_orders_workspace.py и подключается
runtime-патчем parking_bot, поэтому этот модуль не создаёт заявки сам.
"""

from __future__ import annotations

from pathlib import Path
import sys

HANDLERS_DIR = Path(__file__).resolve().parent
BOTS_DIR = HANDLERS_DIR.parent
ROOT = BOTS_DIR.parent
for folder in (BOTS_DIR, ROOT):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))

from handlers import client_portal as base
from handlers import client_portal_v2 as v2


SERVICE_LABEL = {
    "ru": "🔑 Пульты и доступ",
    "uk": "🔑 Пульти та доступ",
    "en": "🔑 Remotes and access",
}


def client_menu_keyboard(lang: str) -> list[list[str]]:
    """
    Preserve the localized v2 menu and replace only the legacy remote/phone
    entry points. No payment or parking buttons are altered.
    """
    lang = lang if lang in SERVICE_LABEL else "ru"
    legacy_remote = base.tr(lang, "remotes")
    legacy_phone = base.tr(lang, "phone")
    result: list[list[str]] = []
    placed = False

    for source_row in v2.client_menu_keyboard(lang):
        row: list[str] = []
        for item in source_row:
            if item == legacy_phone:
                continue
            if item == legacy_remote:
                if not placed:
                    row.append(SERVICE_LABEL[lang])
                    placed = True
                continue
            row.append(item)
        if row:
            result.append(row)

    if not placed:
        insert_at = min(3, len(result))
        result.insert(insert_at, [SERVICE_LABEL[lang]])

    # A resident's "Main menu" remains the resident menu. Switching to guard
    # or operator is explicit and does not change the account's authorization.
    result.append(["🔄 Сменить режим"])
    return result


def client_welcome_text(lang: str) -> str:
    return v2.client_welcome_text(lang)


async def handle_client_portal_text(
    update,
    user_states,
    user_id,
    message_text,
    *,
    lang: str,
    user_mode: str | None,
    is_admin: bool = False,
) -> bool:
    # The combined service button is handled before this module by
    # service_orders_workspace. All other v2 behaviour remains unchanged.
    return await v2.handle_client_portal_text(
        update,
        user_states,
        user_id,
        message_text,
        lang=lang,
        user_mode=user_mode,
        is_admin=is_admin,
    )
