"""Telegram inbox for an assigned mobile cash collector (KAS slot)."""

from __future__ import annotations

from telegram import ReplyKeyboardMarkup, Update

from cash_claim_points_core import active_collector_for_telegram
from service_cash_claims_core import confirm_claim_cash
from service_orders_core import get_conn


ENTRY = "💵 Мои поступления"
BACK = "⬅️ К поступлениям"
EXACT = "✅ Получена указанная сумма"
OTHER = "✏️ Другая сумма"
CONFIRM = "✅ Подтвердить фактический приём"


def _kb(rows: list[list[str]]) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def _open_claims(point_code: str) -> list[dict]:
    conn = get_conn()
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(service_interest_intake)")}
        amount_field = "x.claimed_amount" if "claimed_amount" in columns else "NULL AS claimed_amount"
        return [dict(row) for row in conn.execute(
            f"""SELECT i.id,i.interest_number,i.apartment_number,i.service_name_snapshot,
                       i.quantity,i.amount_due_snapshot,x.claimed_cashbox,{amount_field}
                FROM service_order_interests i JOIN service_interest_intake x ON x.interest_id=i.id
                WHERE i.interest_status='INTEREST' AND i.payment_id IS NULL
                  AND x.claimed_cash_handover=1 AND x.verification_status='UNVERIFIED'
                  AND x.claimed_cashbox=? ORDER BY i.id LIMIT 40""", (point_code,),
        )]
    finally:
        conn.close()


async def _show_queue(update: Update, state: dict, user_id: int) -> None:
    collector = active_collector_for_telegram(user_id)
    if not collector:
        await update.message.reply_text("Нет действующего назначения сборщика для вашего Telegram ID.")
        return
    rows = _open_claims(collector["point_code"])
    buttons = {}
    for row in rows:
        label = f"📦 {row['interest_number']} · кв.{row['apartment_number']} · {row['amount_due_snapshot']:.2f} грн"
        buttons[label] = int(row["id"])
    state.clear()
    state.update(_module="mobile_cash_claims", screen="queue", buttons=buttons,
                 assignment_id=int(collector["id"]), point_code=collector["point_code"])
    await update.message.reply_text(
        f"💵 Поступления: {collector['person_name']} ({collector['point_code']})\n\n"
        + ("Выберите заявление для сверки. Слова жителя ещё не являются оплатой." if rows else
           "Ожидающих заявлений нет."),
        reply_markup=_kb([[label] for label in buttons] + [["🏠 Главное меню"]]),
    )


async def _show_card(update: Update, state: dict, row: dict) -> None:
    state.update(screen="card", interest_id=int(row["id"]), actual_amount=None)
    claimed = float(row["claimed_amount"] if row["claimed_amount"] is not None else row["amount_due_snapshot"])
    await update.message.reply_text(
        f"📦 {row['interest_number']} · кв. {row['apartment_number']}\n"
        f"{row['quantity']} × {row['service_name_snapshot']}\n"
        f"К оплате: {float(row['amount_due_snapshot']):.2f} грн\n"
        f"Заявлено к передаче: {claimed:.2f} грн\n\n"
        "Подтверждайте только фактически полученные деньги. Если сумма иная, укажите её: "
        "касса учтёт приём, но оплаченный заказ при расхождении не создастся.",
        reply_markup=_kb([[EXACT], [OTHER], [BACK]]),
    )


async def handle_mobile_cash_claim_text(update: Update, user_states: dict,
                                        user_id: int, message_text: str) -> bool:
    state = user_states.get(user_id)
    active = isinstance(state, dict) and state.get("_module") == "mobile_cash_claims"
    if not active and message_text != ENTRY:
        return False
    collector = active_collector_for_telegram(user_id)
    if not collector:
        if active or message_text == ENTRY:
            user_states.pop(user_id, None)
            await update.message.reply_text("Нет действующего назначения сборщика для вашего Telegram ID.")
            return True
    if message_text == ENTRY or message_text == BACK:
        state = {} if not active else state
        user_states[user_id] = state
        await _show_queue(update, state, user_id)
        return True
    if not active:
        return False
    if int(state.get("assignment_id", -1)) != int(collector["id"]):
        user_states.pop(user_id, None)
        await update.message.reply_text("Назначение изменилось. Откройте поступления заново.")
        return True
    if message_text == "🏠 Главное меню":
        user_states.pop(user_id, None)
        return False
    rows = _open_claims(collector["point_code"])
    by_id = {int(row["id"]): row for row in rows}
    if state.get("screen") == "queue":
        selected_id = state.get("buttons", {}).get(message_text)
        if selected_id in by_id:
            await _show_card(update, state, by_id[selected_id])
        else:
            await _show_queue(update, state, user_id)
        return True
    row = by_id.get(int(state.get("interest_id") or 0))
    if not row:
        await update.message.reply_text("Заявление уже обработано или снято с очереди.")
        await _show_queue(update, state, user_id)
        return True
    if state.get("screen") == "card":
        if message_text == OTHER:
            state["screen"] = "amount"
            await update.message.reply_text("Введите фактически полученную сумму в грн, например 900.00:",
                                            reply_markup=_kb([[BACK]]))
            return True
        if message_text == EXACT:
            state["actual_amount"] = float(row["claimed_amount"] if row["claimed_amount"] is not None else row["amount_due_snapshot"])
    elif state.get("screen") == "amount":
        try:
            amount = round(float(message_text.replace(",", ".")), 2)
            if amount <= 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text("Введите положительную сумму числом, например 900.00.")
            return True
        state["actual_amount"] = amount
    elif state.get("screen") == "confirm" and message_text == CONFIRM:
        try:
            result = confirm_claim_cash(
                interest_id=int(row["id"]), receiving_point=collector["point_code"],
                actor=str(user_id), collector_telegram_id=user_id,
                actual_amount=float(state["actual_amount"]),
                evidence=f"Telegram ID {user_id}, сообщение {update.message.message_id}",
            )
        except Exception as exc:
            await update.message.reply_text(f"Не удалось учесть получение: {exc}")
            return True
        await update.message.reply_text(
            f"✅ Учтено {result['amount']:.2f} грн на вашем балансе. "
            f"Квитанция {result['receipt_number']}.\n"
            + (f"Оплаченный заказ: {result['order_number']}." if result["order_number"] else
               "Сумма отличается от стоимости: заказ не создан, требуется сверка администратора."),
        )
        await _show_queue(update, state, user_id)
        return True
    else:
        await _show_card(update, state, row)
        return True
    state["screen"] = "confirm"
    await update.message.reply_text(
        f"Фактически получили {float(state['actual_amount']):.2f} грн по {row['interest_number']}?\n"
        "Подтверждение создаст кассовую квитанцию и увеличит ваш баланс.",
        reply_markup=_kb([[CONFIRM], [BACK]]),
    )
    return True
