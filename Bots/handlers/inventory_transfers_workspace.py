"""Telegram workspace for accountable transfers of physical stock (CS, O, K)."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import sys

from telegram import ReplyKeyboardMarkup, Update

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from access_control import has_permission
from inventory_transfer_core import LOCATIONS, confirm_transfer, report_discrepancy, send_transfer, stock_snapshot
from service_orders_core import get_conn

ENTRY = "📦 Передачи товаров"
BACK = "⬅️ К передачам"


@contextmanager
def inventory_conn():
    conn = get_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def kb(rows: list[list[str]]) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def can_send(user_id: int) -> bool:
    return has_permission(user_id, "inventory_transfers", "SEND")


def can_confirm(user_id: int, location: str) -> bool:
    return has_permission(user_id, "inventory_transfers", "CONFIRM",
                          scope_type="STOCK_LOCATION", scope_value=location)


def has_inventory_access(user_id: int) -> bool:
    return can_send(user_id) or any(can_confirm(user_id, p) for p in LOCATIONS)


async def show_inventory_workspace(update: Update, states: dict, user_id: int) -> None:
    if not has_inventory_access(user_id):
        await update.message.reply_text("Нет доступа к учёту передач товаров.")
        return
    states[user_id] = {"_module": "inventory_transfers", "mode": "home"}
    with inventory_conn() as conn:
        balances, pending = stock_snapshot(conn)
    lines = ["📦 Остатки и передачи", "", "В наличии:"]
    lines += [f"• {LOCATIONS[b['location_code']]} · партия {b['batch_number'] or b['batch_id']}: {b['quantity']} шт."
              for b in balances] or ["• Нет товаров"]
    lines += ["", "В пути (ещё не приняты):"]
    lines += [f"• #{p['id']} · {p['from_location_code']} → {p['to_location_code']} · партия {p['batch_number'] or p['batch_id']} · {p['quantity']} шт."
              + (f" ⚠️ расхождение: фактически {p['reported_quantity']}" if p['transfer_status'] == "DISPUTED" else "")
              for p in pending] or ["• Нет передач"]
    buttons: list[list[str]] = []
    if can_send(user_id) and balances:
        buttons.append(["📤 Передать товар"])
    if any(p["transfer_status"] == "SENT" and can_confirm(user_id, p["to_location_code"]) for p in pending):
        buttons.append(["📥 Подтвердить приём"])
    buttons += [["🔄 Обновить"], ["🏠 Главное меню"]]
    await update.message.reply_text("\n".join(lines), reply_markup=kb(buttons))


async def handle_inventory_text(update: Update, states: dict, user_id: int, value: str) -> bool:
    state = states.get(user_id)
    if value != ENTRY and not (isinstance(state, dict) and state.get("_module") == "inventory_transfers"):
        return False
    if value == ENTRY:
        await show_inventory_workspace(update, states, user_id)
        return True
    if not has_inventory_access(user_id):
        states.pop(user_id, None)
        await update.message.reply_text("Доступ к передачам отозван.")
        return True
    if value in {BACK, "🔄 Обновить"}:
        await show_inventory_workspace(update, states, user_id)
        return True
    mode = state["mode"]
    if mode == "home" and value == "📤 Передать товар" and can_send(user_id):
        with inventory_conn() as conn:
            balances, _ = stock_snapshot(conn)
        choices = {}
        for b in balances:
            label = f"{b['location_code']} · партия {b['batch_number'] or b['batch_id']} · {b['quantity']} шт."
            choices[label] = (int(b["batch_id"]), b["location_code"])
        if not choices:
            await show_inventory_workspace(update, states, user_id)
            return True
        state.update(mode="choose_batch", choices=choices)
        await update.message.reply_text("Выберите партию для передачи:", reply_markup=kb([[x] for x in choices] + [[BACK]]))
        return True
    if mode == "choose_batch" and value in state.get("choices", {}):
        batch_id, source = state["choices"][value]
        state.update(mode="choose_destination", batch_id=batch_id, source=source)
        destinations = {f"{code} · {name}": code for code, name in LOCATIONS.items() if code != source}
        state["destinations"] = destinations
        await update.message.reply_text("Куда передать?", reply_markup=kb([[label] for label in destinations] + [[BACK]]))
        return True
    if mode == "choose_destination" and value in state.get("destinations", {}):
        state.update(mode="quantity", destination=state["destinations"][value])
        await update.message.reply_text("Сколько штук фактически передаёте?", reply_markup=kb([[BACK]]))
        return True
    if mode == "quantity":
        if not value.isdecimal() or int(value) <= 0:
            await update.message.reply_text("Введите положительное целое количество.")
            return True
        state.update(mode="confirm_send", quantity=int(value))
        await update.message.reply_text(
            f"Передать {value} шт. из партии #{state['batch_id']}: {state['source']} → {state['destination']}? "
            "Количество сразу уйдёт с остатка отправителя и станет «в пути» до подтверждения получателем.",
            reply_markup=kb([["✅ Отправить"], [BACK]]),
        )
        return True
    if mode == "confirm_send" and value == "✅ Отправить" and can_send(user_id):
        try:
            with inventory_conn() as conn:
                transfer_id = send_transfer(conn, batch_id=state["batch_id"], from_location=state["source"],
                                            to_location=state["destination"], quantity=state["quantity"], actor_id=user_id)
            await update.message.reply_text(f"Передача #{transfer_id} отправлена. Получатель должен подтвердить приём.")
        except Exception as exc:
            await update.message.reply_text(f"⚠️ Передача не записана: {exc}")
        await show_inventory_workspace(update, states, user_id)
        return True
    if mode == "home" and value == "📥 Подтвердить приём":
        with inventory_conn() as conn:
            _, pending = stock_snapshot(conn)
        choices = {}
        for p in pending:
            if p["transfer_status"] == "SENT" and can_confirm(user_id, p["to_location_code"]) and str(p["sent_by"]) != str(user_id):
                label = f"#{p['id']} · {p['from_location_code']} → {p['to_location_code']} · {p['quantity']} шт."
                choices[label] = int(p["id"])
        if not choices:
            await update.message.reply_text("Передач для вашего подтверждения нет.")
            return True
        state.update(mode="choose_receipt", choices=choices)
        await update.message.reply_text("Выберите реально полученную передачу:", reply_markup=kb([[x] for x in choices] + [[BACK]]))
        return True
    if mode == "choose_receipt" and value in state.get("choices", {}):
        state.update(mode="confirm_receipt", transfer_id=state["choices"][value])
        await update.message.reply_text(
            "Пересчитайте пульты. Подтверждайте только при полном совпадении количества. "
            "Если не сходится, оставьте передачу в пути и сообщите оператору.",
            reply_markup=kb([["✅ Количество совпало"], ["⚠️ Есть расхождение"], [BACK]]),
        )
        return True
    if mode == "confirm_receipt" and value == "⚠️ Есть расхождение":
        state["mode"] = "discrepancy_count"
        await update.message.reply_text("Сколько штук фактически пересчитано? Введите число, включая 0.", reply_markup=kb([[BACK]]))
        return True
    if mode == "discrepancy_count":
        if not value.isdecimal():
            await update.message.reply_text("Введите целое неотрицательное число.")
            return True
        try:
            with inventory_conn() as conn:
                row = conn.execute("SELECT to_location_code FROM inventory_transfers WHERE id=? AND transfer_status='SENT'",
                                   (state["transfer_id"],)).fetchone()
                if row is None or not can_confirm(user_id, row[0]):
                    raise ValueError("Нет доступной передачи для вашего пункта.")
                report_discrepancy(conn, transfer_id=state["transfer_id"], actual_quantity=int(value), actor_id=user_id)
            await update.message.reply_text(
                f"Расхождение по передаче #{state['transfer_id']} записано. Товар не зачислен на остаток пункта; нужна отдельная сверка."
            )
        except Exception as exc:
            await update.message.reply_text(f"⚠️ Расхождение не записано: {exc}")
        await show_inventory_workspace(update, states, user_id)
        return True
    if mode == "confirm_receipt" and value == "✅ Количество совпало":
        try:
            with inventory_conn() as conn:
                row = conn.execute("SELECT to_location_code FROM inventory_transfers WHERE id=? AND transfer_status='SENT'",
                                   (state["transfer_id"],)).fetchone()
                if row is None or not can_confirm(user_id, row[0]):
                    raise ValueError("Нет доступной передачи для вашего пункта.")
                confirm_transfer(conn, transfer_id=state["transfer_id"], actor_id=user_id)
            await update.message.reply_text(f"Приём передачи #{state['transfer_id']} подтверждён; остаток пункта увеличен.")
        except Exception as exc:
            await update.message.reply_text(f"⚠️ Приём не записан: {exc}")
        await show_inventory_workspace(update, states, user_id)
        return True
    await update.message.reply_text("Выберите кнопку на экране или вернитесь к списку передач.", reply_markup=kb([[BACK]]))
    return True
