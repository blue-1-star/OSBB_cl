"""
Экран просмотра и решения заявок на привязку квартиры — Адмін-режим ->
"🔗 Заявки на привязку квартиры".

Раньше эта заявка (apartment_link_requests) не была подключена ни к
какому экрану бота вообще — только к коду в client_portal.py, у
которого не было вызывающей кнопки. Построено по тому же образцу, что
и verification_review.py (журнал согласования).
"""

from __future__ import annotations

from pathlib import Path
import sys
import sqlite3
from datetime import datetime

HANDLERS_DIR = Path(__file__).resolve().parent
BOTS_DIR = HANDLERS_DIR.parent
ROOT = BOTS_DIR.parent
for folder in (ROOT, BOTS_DIR):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))

from config import paths, USE_TEST_DB


ENTRY_BUTTON = "🔗 Заявки на привязку квартиры"
BACK_BUTTON = "⬅️ Назад"
APPROVE_BUTTON = "✅ Одобрить"
REJECT_BUTTON = "❌ Отклонить"
SKIP_NOTE_BUTTON = "➡️ Без комментария"


def _get_db_path():
    return paths.OSBB_TEST_DB_FILE if USE_TEST_DB else paths.OSBB_DB_FILE


def _now_db():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _kb(rows: list[list[str]]):
    from telegram import ReplyKeyboardMarkup
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def _list_open_requests():
    conn = sqlite3.connect(_get_db_path())
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT * FROM apartment_link_requests WHERE status = 'NEW' ORDER BY created_at
    """).fetchall()
    conn.close()
    return rows


def _get_request(request_id: int):
    conn = sqlite3.connect(_get_db_path())
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM apartment_link_requests WHERE id = ?", (request_id,)).fetchone()
    conn.close()
    return row


def _resolve_request(request_id: int, approve: bool, operator_id: str, operator_note: str = None):
    conn = sqlite3.connect(_get_db_path())
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()
        request = cur.execute("SELECT * FROM apartment_link_requests WHERE id = ?", (request_id,)).fetchone()
        if not request:
            return None, "Запрос не найден."
        if request["status"] != "NEW":
            return None, f"Уже обработан (статус: {request['status']})."

        new_status = "APPROVED" if approve else "REJECTED"
        timestamp = _now_db()

        if approve:
            cur.execute("""
                UPDATE resident_accounts
                SET apartment_id = ?, apartment_number = ?, status = 'apartment_confirmed',
                    verified_at = ?, updated_at = ?
                WHERE id = ?
            """, (
                int(request["requested_apartment_id"]), request["requested_apartment_number"],
                timestamp, timestamp, int(request["resident_account_id"]),
            ))

        cur.execute("""
            UPDATE apartment_link_requests
            SET status = ?, operator_id = ?, operator_note = ?, reviewed_at = ?, updated_at = ?
            WHERE id = ?
        """, (new_status, str(operator_id), operator_note, timestamp, timestamp, int(request_id)))
        conn.commit()
        return {"new_status": new_status, "apartment_number": request["requested_apartment_number"]}, None
    finally:
        conn.close()


def _list_text_and_menu():
    requests = _list_open_requests()
    if not requests:
        return "🔗 Заявки на привязку квартиры\n\nОткрытых заявок нет.", [[BACK_BUTTON]]

    lines = [f"🔗 Открыто заявок: {len(requests)}", ""]
    buttons = []
    for r in requests:
        lines.append(f"#{r['id']} telegram_id={r['telegram_user_id']} -> кв.{r['requested_apartment_number']}")
        buttons.append([f"#{r['id']}"])
    lines.append("\nВыберите номер заявки для просмотра.")
    buttons.append([BACK_BUTTON])
    return "\n".join(lines), buttons


def _request_card_text(r) -> str:
    return (
        f"🔗 Заявка #{r['id']}\n\n"
        f"Telegram ID: {r['telegram_user_id']}\n"
        f"Текущая квартира: {r['current_apartment_number'] or '—'}\n"
        f"Запрошенная квартира: {r['requested_apartment_number']}\n"
        f"Комментарий жителя: {r['resident_comment'] or '—'}\n"
        f"Подана: {r['created_at']}"
    )


async def handle_apartment_link_review_text(
    update,
    context,
    user_states: dict,
    user_id: int,
) -> bool:
    text = (update.message.text or "").strip()
    state = user_states.get(user_id)

    if text == ENTRY_BUTTON:
        msg, buttons = _list_text_and_menu()
        await update.message.reply_text(msg, reply_markup=_kb(buttons))
        user_states[user_id] = "admin_link_list"
        return True

    is_ours = (
        (isinstance(state, str) and state == "admin_link_list")
        or (isinstance(state, tuple) and state and state[0] in {"admin_link_card", "admin_link_note"})
    )
    if not is_ours:
        return False

    if state == "admin_link_list":
        if text == BACK_BUTTON:
            user_states.pop(user_id, None)
            return False

        if not text.startswith("#"):
            user_states.pop(user_id, None)
            return False

        try:
            request_id = int(text[1:])
        except ValueError:
            await update.message.reply_text("Не понял номер заявки. Выберите кнопкой из списка.")
            return True

        r = _get_request(request_id)
        if not r or r["status"] != "NEW":
            await update.message.reply_text("Такой открытой заявки уже нет (возможно, обработана).")
            msg, buttons = _list_text_and_menu()
            await update.message.reply_text(msg, reply_markup=_kb(buttons))
            user_states[user_id] = "admin_link_list"
            return True

        await update.message.reply_text(
            _request_card_text(r),
            reply_markup=_kb([[APPROVE_BUTTON], [REJECT_BUTTON], [BACK_BUTTON]]),
        )
        user_states[user_id] = ("admin_link_card", request_id)
        return True

    if isinstance(state, tuple) and state[0] == "admin_link_card":
        request_id = state[1]
        if text == BACK_BUTTON:
            msg, buttons = _list_text_and_menu()
            await update.message.reply_text(msg, reply_markup=_kb(buttons))
            user_states[user_id] = "admin_link_list"
            return True
        if text in {APPROVE_BUTTON, REJECT_BUTTON}:
            approve = text == APPROVE_BUTTON
            await update.message.reply_text(
                "Комментарий (необязательно):",
                reply_markup=_kb([[SKIP_NOTE_BUTTON], [BACK_BUTTON]]),
            )
            user_states[user_id] = ("admin_link_note", request_id, approve)
            return True
        await update.message.reply_text(f"Нажмите «{APPROVE_BUTTON}», «{REJECT_BUTTON}» или «{BACK_BUTTON}».")
        return True

    if isinstance(state, tuple) and state[0] == "admin_link_note":
        request_id, approve = state[1], state[2]
        if text == BACK_BUTTON:
            msg, buttons = _list_text_and_menu()
            await update.message.reply_text(msg, reply_markup=_kb(buttons))
            user_states[user_id] = "admin_link_list"
            return True

        note = None if text == SKIP_NOTE_BUTTON else text
        result, err = _resolve_request(request_id, approve, str(user_id), note)
        if err:
            await update.message.reply_text(f"⚠ {err}")
        else:
            verb = "одобрена" if result["new_status"] == "APPROVED" else "отклонена"
            await update.message.reply_text(f"✅ Заявка #{request_id} {verb} (кв.{result['apartment_number']}).")

        msg, buttons = _list_text_and_menu()
        await update.message.reply_text(msg, reply_markup=_kb(buttons))
        user_states[user_id] = "admin_link_list"
        return True

    return False