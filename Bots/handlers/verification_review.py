"""
Экран просмотра и решения журнала согласования — Адмін-режим ->
"📋 Журнал согласования".

Показывает открытые записи verification_journal, позволяет пометить
решённой (с необязательным комментарием) или оставить открытой.
Роли/адресация пока не нужны — админ один и тот же, что суперадмин.
"""

from __future__ import annotations

from pathlib import Path
import sys

HANDLERS_DIR = Path(__file__).resolve().parent
BOTS_DIR = HANDLERS_DIR.parent
ROOT = BOTS_DIR.parent
for folder in (ROOT, BOTS_DIR):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))

from query_lib.queries import list_open_verification_tasks, resolve_verification_task


ENTRY_BUTTON = "📋 Журнал согласования"
BACK_BUTTON = "⬅️ Назад"
RESOLVE_BUTTON = "✅ Решено"
SKIP_NOTE_BUTTON = "➡️ Без комментария"

ISSUE_TYPE_LABELS = {
    "VEHICLE_UNLINKED": "🚗 Авто/квартира не та",
    "MISSING_VEHICLE": "🆕 Авто нет в базе",
    "CHECK_PLATE": "🔢 Номер неполный/некорректный",
    "MISSING_PARKING_MODE": "🅿️ Режим парковки не задан",
    "AMOUNT_MISMATCH": "💰 Сумма не сходится с тарифом",
    "OTHER": "❓ Другое",
}


def _kb(rows: list[list[str]]):
    from telegram import ReplyKeyboardMarkup
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def _list_text_and_menu():
    tasks = list_open_verification_tasks()
    if not tasks:
        return "📋 Журнал согласования\n\nОткрытых записей нет.", [[BACK_BUTTON]]

    lines = [f"📋 Открыто записей: {len(tasks)}", ""]
    buttons = []
    for t in tasks:
        label = ISSUE_TYPE_LABELS.get(t["issue_type"], t["issue_type"])
        apt = t["apartment_number"] or "—"
        lines.append(f"#{t['id']} кв.{apt} | {label}")
        buttons.append([f"#{t['id']}"])
    lines.append("\nВыберите номер записи для просмотра.")
    buttons.append([BACK_BUTTON])
    return "\n".join(lines), buttons


def _task_card_text(task) -> str:
    label = ISSUE_TYPE_LABELS.get(task["issue_type"], task["issue_type"])
    apt = task["apartment_number"] or "—"
    return (
        f"📋 Запись #{task['id']}\n\n"
        f"Квартира: {apt}\n"
        f"Тип: {label}\n"
        f"Описание: {task['description'] or '—'}\n"
        f"Кем отмечено: {task['raised_by'] or '—'}\n"
        f"Когда: {task['created_at']}"
    )


async def handle_verification_review_text(
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
        user_states[user_id] = "admin_verify_list"
        return True

    is_ours = (
        (isinstance(state, str) and state == "admin_verify_list")
        or (isinstance(state, tuple) and state and state[0] in {"admin_verify_card", "admin_verify_note"})
    )
    if not is_ours:
        return False

    # --- список: выбор конкретной записи по "#N" ---
    if state == "admin_verify_list":
        if text == BACK_BUTTON:
            user_states.pop(user_id, None)
            return False  # пусть верхний уровень покажет ADMIN_MENU

        if not text.startswith("#"):
            user_states.pop(user_id, None)
            return False  # не наш текст — отдаём выше

        try:
            task_id = int(text[1:])
        except ValueError:
            await update.message.reply_text("Не понял номер записи. Выберите кнопкой из списка.")
            return True

        tasks = {t["id"]: t for t in list_open_verification_tasks()}
        task = tasks.get(task_id)
        if not task:
            await update.message.reply_text("Такой открытой записи уже нет (возможно, решена).")
            msg, buttons = _list_text_and_menu()
            await update.message.reply_text(msg, reply_markup=_kb(buttons))
            user_states[user_id] = "admin_verify_list"
            return True

        await update.message.reply_text(
            _task_card_text(task),
            reply_markup=_kb([[RESOLVE_BUTTON], [BACK_BUTTON]]),
        )
        user_states[user_id] = ("admin_verify_card", task_id)
        return True

    # --- карточка записи ---
    if isinstance(state, tuple) and state[0] == "admin_verify_card":
        task_id = state[1]
        if text == BACK_BUTTON:
            msg, buttons = _list_text_and_menu()
            await update.message.reply_text(msg, reply_markup=_kb(buttons))
            user_states[user_id] = "admin_verify_list"
            return True
        if text == RESOLVE_BUTTON:
            await update.message.reply_text(
                "Комментарий к решению (необязательно):",
                reply_markup=_kb([[SKIP_NOTE_BUTTON], [BACK_BUTTON]]),
            )
            user_states[user_id] = ("admin_verify_note", task_id)
            return True
        await update.message.reply_text(f"Нажмите «{RESOLVE_BUTTON}» или «{BACK_BUTTON}».")
        return True

    # --- ввод комментария к решению ---
    if isinstance(state, tuple) and state[0] == "admin_verify_note":
        task_id = state[1]
        if text == BACK_BUTTON:
            msg, buttons = _list_text_and_menu()
            await update.message.reply_text(msg, reply_markup=_kb(buttons))
            user_states[user_id] = "admin_verify_list"
            return True

        note = "" if text == SKIP_NOTE_BUTTON else text
        ok = resolve_verification_task(task_id, str(user_id), note)
        if ok:
            await update.message.reply_text(f"✅ Запись #{task_id} отмечена решённой.")
        else:
            await update.message.reply_text(f"⚠ Не удалось отметить #{task_id} (возможно, уже решена кем-то ещё).")

        msg, buttons = _list_text_and_menu()
        await update.message.reply_text(msg, reply_markup=_kb(buttons))
        user_states[user_id] = "admin_verify_list"
        return True

    return False