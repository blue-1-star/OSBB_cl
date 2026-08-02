"""
Экран управления тарифами — Адмін-режим -> "💵 Тарифы".

Позволяет оператору (не программисту) посмотреть действующие тарифы и
изменить сумму без вызова разработчика. Логика расчёта/записи — общая
с query_lib (set_tariff), сам экран лишь тонкий диалоговый фасад поверх
неё, как и предполагает архитектура (core_new/query_lib -> presentation).

Паттерн вызова — как handle_cashier_admin_text: функция сама решает,
относится ли к ней текущий текст, возвращает True если обработала.
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

from query_lib.queries import set_tariff
from query_lib.core import get_conn


ENTRY_BUTTON = "💵 Тарифы"
BACK_BUTTON = "⬅️ Назад"
CONFIRM_BUTTON = "✅ Подтвердить"
CANCEL_BUTTON = "❌ Отмена"


def _kb(rows: list[list[str]]):
    from telegram import ReplyKeyboardMarkup
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def _current_tariffs() -> list[dict]:
    """Действующие сейчас тарифы — одна (последняя по valid_from) запись
    на каждый service_code."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT service_code, amount, valid_from
        FROM service_tariffs t
        WHERE valid_from = (
            SELECT MAX(valid_from) FROM service_tariffs
            WHERE service_code = t.service_code
        )
        ORDER BY service_code
    """)
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def _tariffs_text_and_menu() -> tuple[str, list[list[str]]]:
    tariffs = _current_tariffs()
    if not tariffs:
        lines = ["💵 Действующие тарифы:\n", "(пока ни одного тарифа не задано)"]
    else:
        lines = ["💵 Действующие тарифы:\n"]
        for t in tariffs:
            lines.append(f"{t['service_code']:<15} — {t['amount']:.0f} грн (с {t['valid_from']})")
        lines.append("\nВыберите тариф для изменения:")
    buttons = [[t["service_code"]] for t in tariffs] + [[BACK_BUTTON]]
    return "\n".join(lines), buttons


async def handle_tariff_editor_text(
    update,
    context,
    user_states: dict,
    user_id: int,
) -> bool:
    text = (update.message.text or "").strip()
    state = user_states.get(user_id)

    # --- вход в раздел ---
    if text == ENTRY_BUTTON:
        msg, buttons = _tariffs_text_and_menu()
        await update.message.reply_text(msg, reply_markup=_kb(buttons))
        user_states[user_id] = "admin_tariff_menu"
        return True

    if not (isinstance(state, str) and state == "admin_tariff_menu") and not (
        isinstance(state, tuple) and state and state[0] == "admin_tariff_waiting_amount"
    ) and not (
        isinstance(state, tuple) and state and state[0] == "admin_tariff_confirm"
    ):
        return False

    # --- выбор кода услуги из списка ---
    if state == "admin_tariff_menu":
        if text == BACK_BUTTON:
            user_states.pop(user_id, None)
            return False  # пусть верхний уровень покажет ADMIN_MENU как обычно

        known_codes = {t["service_code"] for t in _current_tariffs()}
        if text not in known_codes:
            # это не про нас — не свой текст в этом состоянии, отдаём выше
            user_states.pop(user_id, None)
            return False

        service_code = text
        current = next((t for t in _current_tariffs() if t["service_code"] == service_code), None)
        current_amount = current["amount"] if current else None
        label = f"{current_amount:.0f} грн" if current_amount is not None else "не задан"
        await update.message.reply_text(
            f"{service_code}: сейчас {label}.\n"
            f"Введите новую сумму, или \"-\", чтобы просто продлить тот же тариф на новый период:",
            reply_markup=_kb([[BACK_BUTTON]]),
        )
        user_states[user_id] = ("admin_tariff_waiting_amount", service_code)
        return True

    # --- ввод суммы ---
    if isinstance(state, tuple) and state[0] == "admin_tariff_waiting_amount":
        service_code = state[1]
        if text == BACK_BUTTON:
            msg, buttons = _tariffs_text_and_menu()
            await update.message.reply_text(msg, reply_markup=_kb(buttons))
            user_states[user_id] = "admin_tariff_menu"
            return True

        if text == "-":
            amount = None
        else:
            try:
                amount = float(text.replace(",", "."))
            except ValueError:
                await update.message.reply_text(
                    "Не понял сумму. Введите число (например 220) или \"-\" для продления прежней:"
                )
                return True

        amount_label = f"{amount:.0f} грн" if amount is not None else "(та же сумма, что и раньше)"
        await update.message.reply_text(
            f"Новый тариф: {service_code} = {amount_label}, действует с сегодняшнего дня.\n"
            f"Прежний период будет закрыт автоматически.\n\n"
            f"Подтвердить?",
            reply_markup=_kb([[CONFIRM_BUTTON, CANCEL_BUTTON]]),
        )
        user_states[user_id] = ("admin_tariff_confirm", service_code, amount)
        return True

    # --- подтверждение ---
    if isinstance(state, tuple) and state[0] == "admin_tariff_confirm":
        _, service_code, amount = state

        if text == CONFIRM_BUTTON:
            try:
                new_id = set_tariff(service_code, amount=amount)
                await update.message.reply_text(f"Готово, id={new_id}.")
            except ValueError as e:
                await update.message.reply_text(f"Ошибка: {e}")
            msg, buttons = _tariffs_text_and_menu()
            await update.message.reply_text(msg, reply_markup=_kb(buttons))
            user_states[user_id] = "admin_tariff_menu"
            return True

        if text == CANCEL_BUTTON:
            msg, buttons = _tariffs_text_and_menu()
            await update.message.reply_text("Отменено.\n\n" + msg, reply_markup=_kb(buttons))
            user_states[user_id] = "admin_tariff_menu"
            return True

        await update.message.reply_text(f"Нажмите \"{CONFIRM_BUTTON}\" или \"{CANCEL_BUTTON}\".")
        return True

    return False