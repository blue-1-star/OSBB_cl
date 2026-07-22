"""
Операторский редактор «Касса и платежи v2».

v2 добавляет поверх v1:
- наличные с defaults O / следующий месяц / сумма по точному начислению;
- банк с обязательным ID операции;
- массовую пачку по подъезду;
- уведомления жителей о передаче наличных и банковских платежах;
- выбор услуг из service_catalog / service_items;
- исторические импорты и сводку расхождений.

Старый обработчик cashier_operator.py остаётся только как fallback для
незавершённых сценариев v1 после обновления.
"""

from __future__ import annotations
from core_new.domain.cashbox import Cashbox
from pathlib import Path
import sys
from typing import Any

from telegram import Update, ReplyKeyboardMarkup

HANDLERS_DIR = Path(__file__).resolve().parent
BOTS_DIR = HANDLERS_DIR.parent
OSBB_ROOT = BOTS_DIR.parent
for folder in (OSBB_ROOT, BOTS_DIR):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))

from handlers import cashier_operator as v1
from cashier_v2_core import (
    CASH_CODES,
    BANK_CODE,
    calc_cashbox_balance,
    confirm_bank_notice,
    confirm_cash_notice,
    create_bank_payment,
    create_cash_batch,
    create_cash_receipt,
    create_paper_note,
    get_conn,
    get_notice,
    get_unit_by_id,
    historical_imports,
    list_open_notices,
    money,
    next_month,
    normalize_date,
    normalize_period,
    open_charges,
    parse_amount,
    reconciliation_summary,
    reject_notice,
    resolve_physical_unit,
    schema_ready,
    service_label,
    service_options,
    table_columns,
    text,
    today,
)


V2_MENU = [
    ["💵 Наличные", "🏦 Банк"],
    ["⚡ Пачка подъезда", "🔔 Уведомления жителей"],
    ["🗒️ Бумажка", "📥 Исторические импорты"],
    ["⚖️ Сверка", "🏦 Остатки касс"],
    ["⬅️ Назад", "🏠 Главное меню"],
]

REVIEW_MENU = [
    ["✅ Подтвердить"],
    ["✏️ Сумма", "🏦 Касса"],
    ["📅 Период", "📝 Основание"],
    ["❌ Отменить", "💰 Касса"],
]

BANK_REVIEW_MENU = [
    ["✅ Подтвердить банк"],
    ["✏️ Сумма", "📅 Период"],
    ["🔢 ID банка", "📝 Плательщик/комментарий"],
    ["❌ Отменить", "💰 Касса"],
]

BATCH_REVIEW_MENU = [
    ["✅ Подтвердить пачку"],
    ["➖ Исключить квартиры", "✏️ Сумма квартиры"],
    ["📅 Период", "🏦 Касса"],
    ["❌ Отменить", "💰 Касса"],
]

BACK = "⬅️ К кассе"
HOME = "🏠 Главное меню"


def kb(rows: list[list[str]]) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def _state(user_states: dict, user_id: int, *, create: bool = False) -> dict | None:
    value = user_states.get(user_id)
    if isinstance(value, dict) and value.get("_module") == "cashier_v2":
        return value
    if create:
        value = {"_module": "cashier_v2", "mode": "home"}
        user_states[user_id] = value
        return value
    return None


def _legacy_v1_active(user_states: dict, user_id: int) -> bool:
    value = user_states.get(user_id)
    return (
        isinstance(value, dict)
        and value.get("_module") == "cashier_operator"
    )


def _other_legacy_active(user_states: dict, user_id: int) -> bool:
    value = user_states.get(user_id)
    return value is not None and not (
        isinstance(value, dict)
        and value.get("_module") in {"cashier_v2", "cashier_operator"}
    )


def _can_manage(is_admin: bool) -> bool:
    return bool(is_admin)


def _cashbox_label(code: str) -> str:
    return {
        "O": "🛡 O — касса охраны",
        "K1": "K1 — консьерж 1",
        "K2": "K2 — консьерж 2",
        "K3": "K3 — консьерж 3",
        "K4": "K4 — консьерж 4",
        "K5": "K5 — консьерж 5",
        "K6": "K6 — консьерж 6",
        "C": "🏦 C — центральная касса",
    }.get(code, code)


def _cashbox_buttons(*, selected: str | None = None) -> list[list[str]]:
    labels = []
    for code in CASH_CODES:
        prefix = "✅ " if code == selected else ""
        labels.append(prefix + _cashbox_label(code))
    return [labels[i:i + 2] for i in range(0, len(labels), 2)] + [[BACK]]


def _code_from_cashbox_label(label: str) -> str | None:
    label = text(label)
    for code in CASH_CODES:
        if label == _cashbox_label(code) or label == "✅ " + _cashbox_label(code):
            return code
    return None


def _service_buttons(
    state: dict,
    *,
    period_code: str | None,
    back_label: str = BACK,
) -> list[list[str]]:
    options = service_options(period_code)
    mapping: dict[str, dict] = {}
    buttons: list[list[str]] = []
    labels: list[str] = []
    for item in options:
        base = f"📌 {service_label(item)}"
        label = base
        index = 2
        while label in mapping:
            label = f"{base} [{index}]"
            index += 1
        mapping[label] = item
        labels.append(label)

    for index in range(0, len(labels), 2):
        buttons.append(labels[index:index + 2])
    buttons.append([back_label])
    state["service_buttons"] = mapping
    return buttons


def _entry_summary(state: dict) -> str:
    unit = state.get("unit") or {}
    service = state.get("service") or {}
    charge = state.get("suggested_charge")
    allocation = (
        "Будет автоматически разнесено на точное начисление."
        if charge and abs(float(state.get("amount") or 0) - float(charge["outstanding_amount"])) < 0.00001
        else "Останется неразнесённой оплатой до ручной сверки."
    )
    return "\n".join([
        "💵 Предпросмотр наличного поступления",
        "",
        f"Квартира: {unit.get('apartment_number') or '-'}",
        f"Касса: {_cashbox_label(state.get('cashbox_code') or 'O')}",
        f"Дата: {state.get('receipt_date') or today()}",
        f"Период: {state.get('period_code') or 'не указан'}",
        f"Услуга: {service_label(service)}",
        f"Сумма: {money(state.get('amount'))} грн.",
        f"Основание: {state.get('source_text') or 'Оператор подтвердил получение наличных.'}",
        "",
        allocation,
    ])


def _bank_summary(state: dict) -> str:
    unit = state.get("unit") or {}
    service = state.get("service") or {}
    charge = state.get("suggested_charge")
    allocation = (
        "Будет автоматически разнесено на точное начисление."
        if charge and abs(float(state.get("amount") or 0) - float(charge["outstanding_amount"])) < 0.00001
        else "Останется неразнесённой оплатой до ручной сверки."
    )
    return "\n".join([
        "🏦 Предпросмотр банковской операции",
        "",
        f"Квартира: {unit.get('apartment_number') or '-'}",
        f"Дата выписки: {state.get('receipt_date') or today()}",
        f"ID банковской операции: {state.get('bank_ref') or 'не указан'}",
        f"Период: {state.get('period_code') or 'не указан'}",
        f"Услуга: {service_label(service)}",
        f"Сумма: {money(state.get('amount'))} грн.",
        f"Плательщик / комментарий: {state.get('source_text') or '—'}",
        "",
        allocation,
    ])


def _paper_summary(state: dict) -> str:
    unit = state.get("unit") or {}
    service = state.get("service") or {}
    return "\n".join([
        "🗒️ Предпросмотр бумажки",
        "",
        f"Квартира: {unit.get('apartment_number') or '-'}",
        f"Период: {state.get('period_code') or 'не указан'}",
        f"Услуга: {service_label(service)}",
        f"Сумма на бумажке: {money(state.get('amount'))} грн.",
        f"Текст / номер бумаги: {state.get('source_text') or '—'}",
        "",
        "Платёж и остаток кассы не изменятся.",
    ])


def _prepare_suggested_charge(state: dict) -> None:
    unit = state.get("unit")
    service = state.get("service")
    period = state.get("period_code")
    if not unit or not service or not period:
        state["suggested_charge"] = None
        return
    rows = open_charges(
        apartment_id=int(unit["id"]),
        period_code=period,
        service_code=service["service_code"],
        service_item_code=service.get("service_item_code"),
    )
    if len(rows) == 1:
        state["suggested_charge"] = rows[0]
        state["amount"] = float(rows[0]["outstanding_amount"])
    else:
        state["suggested_charge"] = None
        state.setdefault("amount", None)


async def _ask_unit(update: Update) -> None:
    await update.message.reply_text(
        "Введите номер квартиры.\n\n"
        "Для составного номера потребуется выбор физической квартиры.",
        reply_markup=kb([[BACK], [HOME]]),
    )


async def _show_service_menu(update: Update, state: dict) -> None:
    await update.message.reply_text(
        f"Выберите услугу из справочника.\nПериод по умолчанию: {state.get('period_code')}",
        reply_markup=kb(_service_buttons(state, period_code=state.get("period_code"))),
    )


async def _start_entry(
    update: Update,
    user_states: dict,
    user_id: int,
    *,
    kind: str,
) -> None:
    state = _state(user_states, user_id, create=True)
    state.clear()
    state.update({
        "_module": "cashier_v2",
        "mode": f"entry_{kind}_period",
        "entry_kind": kind,
        "cashbox_code": "O",
        "period_code": next_month(),
        "receipt_date": today(),
        "source_text": "",
        "amount": None,
    })
    title = {
        "cash": "💵 Наличные",
        "bank": "🏦 Банк",
        "paper": "🗒️ Бумажка",
    }[kind]
    await update.message.reply_text(
        f"{title}\n\n"
        f"Период по умолчанию: {next_month()}\n"
        "Выберите период или оставьте следующий месяц.",
        reply_markup=kb([
            [f"📅 Оставить: {next_month()}"],
            ["✏️ Изменить период", "❔ Без периода"],
            [BACK, HOME],
        ]),
    )


async def _show_entry_review(update: Update, state: dict) -> None:
    kind = state.get("entry_kind")
    state["mode"] = f"entry_{kind}_review"
    if kind == "cash":
        await update.message.reply_text(_entry_summary(state), reply_markup=kb(REVIEW_MENU))
    elif kind == "bank":
        await update.message.reply_text(_bank_summary(state), reply_markup=kb(BANK_REVIEW_MENU))
    else:
        await update.message.reply_text(
            _paper_summary(state),
            reply_markup=kb([["✅ Сохранить бумажку"], ["✏️ Сумма", "📅 Период"], ["📝 Основание"], ["❌ Отменить", "💰 Касса"]]),
        )


async def _select_unit(
    update: Update,
    state: dict,
    raw: str,
) -> None:
    kind, units, message = resolve_physical_unit(raw)
    if kind == "UNIT" and units:
        state["unit"] = units[0]
        _prepare_suggested_charge(state)
        if state.get("amount") is None:
            state["mode"] = f"entry_{state['entry_kind']}_amount"
            await update.message.reply_text(
                "Точного единственного начисления для подстановки не найдено.\n"
                "Введите сумму вручную.",
                reply_markup=kb([[BACK], [HOME]]),
            )
        else:
            await _show_entry_review(update, state)
        return

    if kind == "GROUP" and units:
        mapping = {}
        buttons = []
        for unit in units:
            label = f"🏠 кв. {unit.get('apartment_number')}"
            mapping[label] = unit
            buttons.append([label])
        state["unit_member_map"] = mapping
        state["mode"] = f"entry_{state['entry_kind']}_unit_member"
        await update.message.reply_text(
            message,
            reply_markup=kb(buttons + [[BACK], [HOME]]),
        )
        return

    await update.message.reply_text(f"Не удалось выбрать квартиру: {message}")


def _entry_manual_amount(state: dict, raw: str) -> None:
    state["amount"] = parse_amount(raw)
    state["suggested_charge"] = None


async def _create_entry(update: Update, state: dict, user_id: int) -> None:
    kind = state["entry_kind"]
    unit = state["unit"]
    service = state["service"]
    amount = parse_amount(state["amount"])
    source_text = text(state.get("source_text")) or "Оператор подтвердил поступление."

    # ==========================================
    # НОВАЯ ЛОГИКА: используем Cashbox.register_payment()
    # ==========================================

    # Определяем номер квартиры
    apartment_number = unit.get('apartment_number') if unit else None

    # Получаем код услуги из service
    service_code = service.get('service_code') or service.get('base_service_code')

    result = Cashbox.register_payment(
        amount=amount,
        plate=source_text,  # источник/комментарий
        apartment_number=apartment_number,
        service_code=service_code,
        payment_method='cash' if kind == 'cash' else 'bank' if kind == 'bank' else 'cash',
        operator_id=user_id,
        comment=f"{kind}: {source_text}",
        period_code=state.get('period_code'),
    )

    if not result['success']:
        await update.message.reply_text(f"⚠️ Ошибка сохранения: {result['error']}")
        return

    # Формируем ответ
    response_lines = [
        "✅ Операция сохранена!",
        "",
    ]

    if result.get('candidate_created'):
        response_lines.append("🆕 Создан кандидат в автомобили.")
    if result.get('vehicle_created'):
        response_lines.append("🚗 Создан новый автомобиль.")

    response_lines.append(f"💰 Сумма: {money(amount)} грн.")

    if result.get('payment_id'):
        response_lines.append(f"🧾 Платёж: #{result['payment_id']}")

    if result.get('candidate_id'):
        response_lines.append(f"📌 Кандидат: #{result['candidate_id']}")

    await update.message.reply_text("\n".join(response_lines))
    await show_home(update, state)


async def show_home(update: Update, state: dict | None = None) -> None:
    if state is not None:
        state.clear()
        state.update({"_module": "cashier_v2", "mode": "home"})
    await update.message.reply_text(
        "💰 Касса и платежи v2\n\n"
        "O — основная касса охраны.\n"
        "K1–K6 — отдельные кассы консьержей.\n"
        "BANK — банковский канал, не физическая касса.\n\n"
        "Уведомление жителя само по себе не является оплатой.",
        reply_markup=kb(V2_MENU),
    )


def _batch_summary(state: dict) -> str:
    charges = state.get("batch_charges") or []
    exclusions = state.get("batch_exclusions") or set()
    overrides = state.get("batch_overrides") or {}
    included = [item for item in charges if text(item.get("apartment_number")) not in exclusions]
    total = sum(float(overrides.get(text(item.get("apartment_number")), item["outstanding_amount"])) for item in included)
    preview = []
    for item in included[:15]:
        number = text(item.get("apartment_number"))
        value = float(overrides.get(number, item["outstanding_amount"]))
        preview.append(f"кв. {number}: {money(value)} грн.")
    suffix = f"\n… ещё {len(included) - 15} строк." if len(included) > 15 else ""
    return "\n".join([
        "⚡ Предпросмотр массовой пачки",
        "",
        f"Подъезд: {state.get('batch_entrance')}",
        f"Период: {state.get('period_code')}",
        f"Касса: {_cashbox_label(state.get('cashbox_code') or 'O')}",
        f"Услуга: {service_label(state.get('service'))}",
        f"Будет принято: {len(included)} квартир",
        f"Исключено: {len(exclusions)}",
        f"Итого: {money(total)} грн.",
        "",
        *preview,
        suffix,
        "",
        "Каждая квартира создаст отдельные receipt, payment и кассовую операцию.",
        "Разнесение будет сделано только на выбранное точное начисление.",
    ])


async def _refresh_batch(update: Update, state: dict) -> None:
    charges = open_charges(
        entrance_number=text(state.get("batch_entrance")),
        period_code=state.get("period_code"),
        service_code=state["service"]["service_code"],
        service_item_code=state["service"].get("service_item_code"),
    )
    state["batch_charges"] = charges
    valid_numbers = {text(item.get("apartment_number")) for item in charges}
    state["batch_exclusions"] = {
        item for item in state.get("batch_exclusions", set()) if item in valid_numbers
    }
    state["batch_overrides"] = {
        key: value
        for key, value in state.get("batch_overrides", {}).items()
        if key in valid_numbers
    }
    state["mode"] = "batch_review"
    await update.message.reply_text(_batch_summary(state), reply_markup=kb(BATCH_REVIEW_MENU))


async def _start_batch(update: Update, state: dict) -> None:
    state.clear()
    state.update({
        "_module": "cashier_v2",
        "mode": "batch_entrance",
        "period_code": next_month(),
        "cashbox_code": "O",
        "receipt_date": today(),
        "batch_exclusions": set(),
        "batch_overrides": {},
        "source_text": "Массовый ввод по бумажному списку консьержа.",
    })
    await update.message.reply_text(
        "⚡ Массовый ввод по подъезду\n\n"
        f"По умолчанию: период {next_month()}, касса O, "
        "сумма каждой строки = точный остаток начисления.",
        reply_markup=kb([
            ["Подъезд 1", "Подъезд 2", "Подъезд 3"],
            ["Подъезд 4", "Подъезд 5", "Подъезд 6"],
            [BACK, HOME],
        ]),
    )


async def _show_notices(update: Update, state: dict) -> None:
    notices = list_open_notices()
    mapping = {}
    buttons = []
    lines = ["🔔 Уведомления жителей", ""]
    for item in notices:
        label = (
            f"🔔 {item['notice_number']} | кв.{item.get('apartment_number') or '-'} | "
            f"{money(item.get('declared_amount'))}"
        )
        mapping[label] = int(item["id"])
        buttons.append([label])
        lines.append(
            f"{item['notice_number']} | кв. {item.get('apartment_number') or '-'} | "
            f"{item.get('notice_type')} | {money(item.get('declared_amount'))} грн."
        )
    if not notices:
        lines.append("Открытых уведомлений нет.")
    buttons.extend([[BACK], [HOME]])
    state["notice_buttons"] = mapping
    state["mode"] = "notice_list"
    await update.message.reply_text("\n".join(lines), reply_markup=kb(buttons))


async def _show_notice_card(update: Update, state: dict, notice_id: int) -> None:
    notice = get_notice(notice_id)
    if not notice:
        await update.message.reply_text("Уведомление не найдено.")
        return
    state["notice_id"] = int(notice_id)
    state["mode"] = "notice_card"
    notice_type = (
        "💵 Передача наличных" if notice["notice_type"] == "CASH_HANDOVER"
        else "🏦 Банковский перевод"
    )
    lines = [
        f"🔔 {notice['notice_number']}",
        "",
        f"Тип: {notice_type}",
        f"Квартира: {notice.get('apartment_number') or '-'}",
        f"Касса / канал: {notice.get('declared_cashbox_code') or BANK_CODE}",
        f"Период: {notice.get('declared_period_code') or 'не указан'}",
        f"Услуга: {notice.get('declared_service_code') or '-'}",
        f"Сумма: {money(notice.get('declared_amount'))} грн.",
        f"Комментарий жителя: {notice.get('resident_comment') or '—'}",
        "",
        "Это уведомление не является подтверждённой оплатой.",
    ]
    if notice["notice_type"] == "CASH_HANDOVER":
        buttons = [["✅ Подтвердить наличные"], ["❌ Отклонить"], [BACK, HOME]]
    else:
        buttons = [["🏦 Подтвердить банк"], ["❌ Отклонить"], [BACK, HOME]]
    await update.message.reply_text("\n".join(lines), reply_markup=kb(buttons))


async def _show_historical(update: Update, state: dict) -> None:
    rows = historical_imports(30)
    lines = ["📥 Исторические импорты без кассовой карточки", ""]
    for item in rows:
        lines.append(
            f"payment #{item['payment_id']} | {item.get('payment_date') or '-'} | "
            f"кв. {item.get('apartment_number') or '-'} | "
            f"{money(item.get('amount'))} грн. | {item.get('source') or '-'}"
        )
    if not rows:
        lines.append("Таких строк не найдено.")
    state["mode"] = "historical"
    await update.message.reply_text("\n\n".join(lines), reply_markup=kb([[BACK], [HOME]]))


async def _show_reconciliation(update: Update, state: dict) -> None:
    data = reconciliation_summary()
    text_out = "\n".join([
        "⚖️ Очередь сверки",
        "",
        f"Уведомления жителей: {data['resident_notices']}",
        f"Бумажки без движения денег: {data['paper_notes']}",
        f"Неразнесённые наличные: {data['unallocated_cash']}",
        f"Исторические импорты без кассовой карточки: {data['historical_imports']}",
        f"Открытые специальные кейсы: {data['open_cases']}",
        "",
        "Эти числа не равны долгу. Это рабочая очередь для оператора.",
    ])
    state["mode"] = "reconciliation"
    await update.message.reply_text(text_out, reply_markup=kb([
        ["🔔 Уведомления жителей", "📥 Исторические импорты"],
        [BACK, HOME],
    ]))


async def handle_cashier_operator_v2_text(
    update: Update,
    user_states: dict,
    user_id: int,
    message_text: str,
    *,
    is_admin: bool = False,
) -> bool:
    message_text = text(message_text)

    # Keep unfinished v1 session safe rather than resetting it mid-flow.
    if _legacy_v1_active(user_states, user_id):
        return await v1.handle_cashier_operator_text(
            update, user_states, user_id, message_text, is_admin=is_admin
        )

    if _other_legacy_active(user_states, user_id):
        return False

    state = _state(user_states, user_id, create=False)
    mode = text(state.get("mode")) if state else ""

    if message_text == "💰 Касса":
        if not _can_manage(is_admin):
            await update.message.reply_text("Нет доступа к кассовому редактору.")
            return True
        ready, reason = schema_ready()
        if not ready:
            await update.message.reply_text("⚠️ " + reason)
            return True
        state = _state(user_states, user_id, create=True)
        await show_home(update, state)
        return True

    if not mode:
        return False

    if message_text == HOME:
        user_states.pop(user_id, None)
        return False
    if message_text in {BACK, "💰 Касса"}:
        await show_home(update, state)
        return True

    if mode == "home":
        if message_text == "💵 Наличные":
            await _start_entry(update, user_states, user_id, kind="cash")
            return True
        if message_text == "🏦 Банк":
            await _start_entry(update, user_states, user_id, kind="bank")
            return True
        if message_text == "🗒️ Бумажка":
            await _start_entry(update, user_states, user_id, kind="paper")
            return True
        if message_text == "⚡ Пачка подъезда":
            await _start_batch(update, state)
            return True
        if message_text == "🔔 Уведомления жителей":
            await _show_notices(update, state)
            return True
        if message_text == "📥 Исторические импорты":
            await _show_historical(update, state)
            return True
        if message_text == "⚖️ Сверка":
            await _show_reconciliation(update, state)
            return True
        if message_text == "🏦 Остатки касс":
            conn = get_conn()
            try:
                cur = conn.cursor()
                lines = ["🏦 Остатки физических касс", ""]
                for code in CASH_CODES:
                    try:
                        lines.append(f"{_cashbox_label(code)}: {money(calc_cashbox_balance(cur, code))} грн.")
                    except Exception:
                        pass
                lines.extend(["", "BANK не является физической кассой."])
            finally:
                conn.close()
            await update.message.reply_text("\n".join(lines), reply_markup=kb(V2_MENU))
            return True
        await update.message.reply_text("Выберите действие кнопкой.", reply_markup=kb(V2_MENU))
        return True

    # ------------------------------------------------ entry flow
    if mode.endswith("_period"):
        if message_text.startswith("📅 Оставить:"):
            state["period_code"] = next_month()
            state["mode"] = f"entry_{state['entry_kind']}_service"
            await _show_service_menu(update, state)
            return True
        if message_text == "✏️ Изменить период":
            state["mode"] = f"entry_{state['entry_kind']}_period_manual"
            await update.message.reply_text("Введите период: ГГГГ-ММ.", reply_markup=kb([[BACK], [HOME]]))
            return True
        if message_text == "❔ Без периода":
            state["period_code"] = None
            state["mode"] = f"entry_{state['entry_kind']}_service"
            await _show_service_menu(update, state)
            return True
        await update.message.reply_text("Выберите период кнопкой.")
        return True

    if mode.endswith("_period_manual"):
        try:
            state["period_code"] = normalize_period(message_text)
        except Exception as exc:
            await update.message.reply_text(f"Ошибка: {exc}")
            return True
        state["mode"] = f"entry_{state['entry_kind']}_service"
        await _show_service_menu(update, state)
        return True

    # if mode.endswith("_service"):
    #     service = (state.get("service_buttons") or {}).get(message_text)
    #     if not service:
    #         await update.message.reply_text("Выберите услугу кнопкой.")
    #         return True
    #     state["service"] = service
    #     state["mode"] = f"entry_{state['entry_kind']}_unit"
    #     await _ask_unit(update)
    #     return True

    if mode.endswith("_service"):
        service = (state.get("service_buttons") or {}).get(message_text)
        if not service:
            await update.message.reply_text("Выберите услугу кнопкой.")
            return True
        state["service"] = service

        # ==================================================
        # ВРЕМЕННАЯ ПОДСТАНОВКА СУММЫ (для Night и Day)
        # ==================================================
        service_code = service.get('service_code')
        if service_code == 'PARKING_NIGHT':
            state['amount'] = 500.00
        elif service_code == 'PARKING_DAY':
            state['amount'] = 200.00
        else:
            state['amount'] = None

        # if state.get('amount'):
        #     await _show_entry_review(update, state)
        # else:
        #     state["mode"] = f"entry_{state['entry_kind']}_unit"
        #     await _ask_unit(update)
        # Пропускаем вопрос о квартире — если она не указана, будет NULL
        if not state.get('apartment_number'):
            state['apartment_number'] = None

        if state.get('amount'):
            await _show_entry_review(update, state)
        else:
            # Если сумма не подставилась — запрашиваем вручную
            state["mode"] = f"entry_{state['entry_kind']}_amount"
            await update.message.reply_text("Введите сумму вручную:")
        return True


    if mode.endswith("_unit"):
        await _select_unit(update, state, message_text)
        return True

    if mode.endswith("_unit_member"):
        unit = (state.get("unit_member_map") or {}).get(message_text)
        if not unit:
            await update.message.reply_text("Выберите физическую квартиру кнопкой.")
            return True
        state["unit"] = unit
        state.pop("unit_member_map", None)
        _prepare_suggested_charge(state)
        if state.get("amount") is None:
            state["mode"] = f"entry_{state['entry_kind']}_amount"
            await update.message.reply_text("Введите сумму вручную.", reply_markup=kb([[BACK], [HOME]]))
        else:
            await _show_entry_review(update, state)
        return True

    if mode.endswith("_amount") and "_review" not in mode:
        try:
            _entry_manual_amount(state, message_text)
        except Exception as exc:
            await update.message.reply_text(f"Ошибка: {exc}")
            return True
        await _show_entry_review(update, state)
        return True

    if mode.endswith("_review"):
        kind = state["entry_kind"]
        confirm_text = "✅ Подтвердить банк" if kind == "bank" else (
            "✅ Сохранить бумажку" if kind == "paper" else "✅ Подтвердить"
        )
        if message_text == confirm_text:
            if kind == "bank" and not text(state.get("bank_ref")):
                state["mode"] = "entry_bank_ref"
                await update.message.reply_text("Введите ID банковской операции из выписки.", reply_markup=kb([[BACK], [HOME]]))
                return True
            await _create_entry(update, state, user_id)
            return True
        if message_text == "✏️ Сумма":
            state["mode"] = f"entry_{kind}_amount"
            await update.message.reply_text("Введите сумму.", reply_markup=kb([[BACK], [HOME]]))
            return True
        if message_text == "🏦 Касса" and kind == "cash":
            state["mode"] = "entry_cash_cashbox"
            await update.message.reply_text("Выберите кассу.", reply_markup=kb(_cashbox_buttons(selected=state.get("cashbox_code"))))
            return True
        if message_text == "📅 Период":
            state["mode"] = f"entry_{kind}_period_manual"
            await update.message.reply_text("Введите период: ГГГГ-ММ.", reply_markup=kb([[BACK], [HOME]]))
            return True
        if message_text in {"📝 Основание", "📝 Плательщик/комментарий"}:
            state["mode"] = f"entry_{kind}_comment"
            await update.message.reply_text("Введите текст или «-».", reply_markup=kb([[BACK], [HOME]]))
            return True
        if message_text == "🔢 ID банка" and kind == "bank":
            state["mode"] = "entry_bank_ref"
            await update.message.reply_text("Введите ID банковской операции из выписки.", reply_markup=kb([[BACK], [HOME]]))
            return True
        if message_text == "❌ Отменить":
            await show_home(update, state)
            return True
        await update.message.reply_text("Выберите действие кнопкой.")
        return True

    if mode == "entry_cash_cashbox":
        code = _code_from_cashbox_label(message_text)
        if not code:
            await update.message.reply_text("Выберите кассу кнопкой.")
            return True
        state["cashbox_code"] = code
        await _show_entry_review(update, state)
        return True

    if mode == "entry_bank_ref":
        if not text(message_text) or message_text == "-":
            await update.message.reply_text("ID банковской операции обязателен.")
            return True
        state["bank_ref"] = message_text
        await _show_entry_review(update, state)
        return True

    if mode.endswith("_comment"):
        state["source_text"] = "" if message_text == "-" else message_text
        await _show_entry_review(update, state)
        return True

    # ------------------------------------------------ batch flow
    if mode == "batch_entrance":
        if not message_text.startswith("Подъезд "):
            await update.message.reply_text("Выберите подъезд кнопкой.")
            return True
        state["batch_entrance"] = message_text.replace("Подъезд ", "").strip()
        state["mode"] = "batch_service"
        await _show_service_menu(update, state)
        return True

    if mode == "batch_service":
        service = (state.get("service_buttons") or {}).get(message_text)
        if not service:
            await update.message.reply_text("Выберите услугу кнопкой.")
            return True
        state["service"] = service
        await _refresh_batch(update, state)
        return True

    if mode == "batch_review":
        if message_text == "✅ Подтвердить пачку":
            charges = state.get("batch_charges") or []
            if not charges:
                await update.message.reply_text("Нет строк для подтверждения.")
                return True
            try:
                result = create_cash_batch(
                    entrance_number=state["batch_entrance"],
                    period_code=state["period_code"],
                    service=state["service"],
                    cashbox_code=state["cashbox_code"],
                    receipt_date=state["receipt_date"],
                    charges=charges,
                    exclusions=state.get("batch_exclusions") or set(),
                    amount_overrides=state.get("batch_overrides") or {},
                    operator_id=user_id,
                    source_text=state.get("source_text") or "Массовый ввод оператором.",
                )
            except Exception as exc:
                await update.message.reply_text(f"Пачка не сохранена: {exc}")
                return True
            await update.message.reply_text(
                "✅ Массовая пачка сохранена.\n\n"
                f"Пачка: {result['batch_number']}\n"
                f"Принято квартир: {result['included']}\n"
                f"Исключено: {result['excluded']}\n"
                f"Сумма: {money(result['total'])} грн.\n"
                f"Остаток кассы {state['cashbox_code']}: {money(result['cashbox_balance'])} грн."
            )
            await show_home(update, state)
            return True
        if message_text == "➖ Исключить квартиры":
            state["mode"] = "batch_exclusions"
            await update.message.reply_text(
                "Введите номера квартир через запятую.\n"
                "Например: 142, 166, 170\n\n"
                "Это исключения из текущей пачки.",
                reply_markup=kb([[BACK], [HOME]]),
            )
            return True
        if message_text == "✏️ Сумма квартиры":
            state["mode"] = "batch_amount_override"
            await update.message.reply_text(
                "Введите: номер_квартиры сумма\n"
                "Например: 174 350\n\n"
                "Сумма не может быть больше остатка начисления.",
                reply_markup=kb([[BACK], [HOME]]),
            )
            return True
        if message_text == "📅 Период":
            state["mode"] = "batch_period"
            await update.message.reply_text("Введите период: ГГГГ-ММ.", reply_markup=kb([[BACK], [HOME]]))
            return True
        if message_text == "🏦 Касса":
            state["mode"] = "batch_cashbox"
            await update.message.reply_text("Выберите кассу.", reply_markup=kb(_cashbox_buttons(selected=state.get("cashbox_code"))))
            return True
        if message_text == "❌ Отменить":
            await show_home(update, state)
            return True
        await update.message.reply_text("Выберите действие кнопкой.")
        return True

    if mode == "batch_exclusions":
        exclusions = {text(item) for item in message_text.replace(";", ",").split(",") if text(item)}
        state["batch_exclusions"] = exclusions
        state["mode"] = "batch_review"
        await update.message.reply_text("Исключения сохранены.")
        await _refresh_batch(update, state)
        return True

    if mode == "batch_amount_override":
        parts = message_text.replace(",", ".").split()
        if len(parts) != 2:
            await update.message.reply_text("Используйте формат: номер_квартиры сумма")
            return True
        number, raw_amount = parts
        valid = {text(item.get("apartment_number")): item for item in state.get("batch_charges") or []}
        if number not in valid:
            await update.message.reply_text("Эта квартира не входит в текущую пачку.")
            return True
        try:
            amount = parse_amount(raw_amount)
        except Exception as exc:
            await update.message.reply_text(f"Ошибка: {exc}")
            return True
        if amount > float(valid[number]["outstanding_amount"]) + 0.00001:
            await update.message.reply_text(
                f"Нельзя больше остатка {money(valid[number]['outstanding_amount'])} грн."
            )
            return True
        state.setdefault("batch_overrides", {})[number] = amount
        await _refresh_batch(update, state)
        return True

    if mode == "batch_period":
        try:
            state["period_code"] = normalize_period(message_text, required=True)
        except Exception as exc:
            await update.message.reply_text(f"Ошибка: {exc}")
            return True
        await _refresh_batch(update, state)
        return True

    if mode == "batch_cashbox":
        code = _code_from_cashbox_label(message_text)
        if not code:
            await update.message.reply_text("Выберите кассу кнопкой.")
            return True
        state["cashbox_code"] = code
        await _refresh_batch(update, state)
        return True

    # ------------------------------------------------ notices
    if mode == "notice_list":
        notice_id = (state.get("notice_buttons") or {}).get(message_text)
        if not notice_id:
            await update.message.reply_text("Выберите уведомление кнопкой.")
            return True
        await _show_notice_card(update, state, int(notice_id))
        return True

    if mode == "notice_card":
        if message_text == "✅ Подтвердить наличные":
            state["mode"] = "notice_cash_note"
            await update.message.reply_text(
                "Введите заметку оператора или «-».\n"
                "Подтверждайте только когда деньги реально у вас/в кассе.",
                reply_markup=kb([[BACK], [HOME]]),
            )
            return True
        if message_text == "🏦 Подтвердить банк":
            state["mode"] = "notice_bank_ref"
            await update.message.reply_text(
                "Введите ID банковской операции из выписки.",
                reply_markup=kb([[BACK], [HOME]]),
            )
            return True
        if message_text == "❌ Отклонить":
            state["mode"] = "notice_reject"
            await update.message.reply_text(
                "Введите причину отклонения.",
                reply_markup=kb([[BACK], [HOME]]),
            )
            return True
        await update.message.reply_text("Выберите действие кнопкой.")
        return True

    if mode == "notice_cash_note":
        try:
            result = confirm_cash_notice(
                int(state["notice_id"]),
                operator_id=user_id,
                operator_note="" if message_text == "-" else message_text,
            )
        except Exception as exc:
            await update.message.reply_text(f"Не удалось подтвердить: {exc}")
            return True
        await update.message.reply_text(
            "✅ Наличные подтверждены.\n\n"
            f"Квитанция: {result['receipt_number']}\n"
            f"Платёж: #{result['payment_id']}\n"
            "Разнесение при необходимости выполняется отдельно."
        )
        await _show_notices(update, state)
        return True

    if mode == "notice_bank_ref":
        if not text(message_text) or message_text == "-":
            await update.message.reply_text("Нужен ID операции из банковской выписки.")
            return True
        state["notice_bank_ref"] = message_text
        state["mode"] = "notice_bank_note"
        await update.message.reply_text("Введите заметку оператора или «-».", reply_markup=kb([[BACK], [HOME]]))
        return True

    if mode == "notice_bank_note":
        try:
            result = confirm_bank_notice(
                int(state["notice_id"]),
                operator_id=user_id,
                transaction_ref=state["notice_bank_ref"],
                transaction_date=today(),
                operator_note="" if message_text == "-" else message_text,
            )
        except Exception as exc:
            await update.message.reply_text(f"Не удалось подтвердить банк: {exc}")
            return True
        await update.message.reply_text(
            "✅ Банковская оплата подтверждена.\n\n"
            f"Банк: #{result['bank_transaction_id']}\n"
            f"Платёж: #{result['payment_id']}"
        )
        await _show_notices(update, state)
        return True

    if mode == "notice_reject":
        try:
            reject_notice(int(state["notice_id"]), operator_id=user_id, reason=message_text)
        except Exception as exc:
            await update.message.reply_text(f"Не удалось отклонить: {exc}")
            return True
        await update.message.reply_text("✅ Уведомление отклонено с указанием причины.")
        await _show_notices(update, state)
        return True

    # ------------------------------------------------ reports
    if mode in {"historical", "reconciliation"}:
        await update.message.reply_text("Используйте «💰 Касса» для возврата.")
        return True

    await update.message.reply_text("Выберите действие кнопкой.", reply_markup=kb(V2_MENU))
    return True
