"""
Клиентский кабинет v2: электронные уведомления о передаче денег.

Работает поверх действующего client_portal.py:
- сохраняет язык, проверенную привязку квартиры, машины, парковку и пульты;
- добавляет в раздел парковки:
    💵 Сообщить о передаче наличных
    🏦 Сообщить о банковской оплате
    🧾 Мои уведомления
- уведомление не является оплатой, пока оператор не подтвердит факт.

Этот модуль экспортирует те же три функции, что и прежний client_portal.py:
  client_menu_keyboard
  client_welcome_text
  handle_client_portal_text
"""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any

from telegram import ReplyKeyboardMarkup, Update

HANDLERS_DIR = Path(__file__).resolve().parent
BOTS_DIR = HANDLERS_DIR.parent
OSBB_ROOT = BOTS_DIR.parent
for folder in (BOTS_DIR, OSBB_ROOT):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))

# Existing safe-linking client portal remains as base.
from handlers import client_portal as base
from cashier_v2_core import (
    CASH_CODES,
    create_payment_notice,
    list_resident_notices,
    money,
    next_month,
    normalize_period,
    open_charges,
    parse_amount,
    service_label,
    service_options,
    text,
)


VT = {
    "ru": {
        "cash_notice": "💵 Сообщить о передаче наличных",
        "bank_notice": "🏦 Сообщить о банковской оплате",
        "my_notices": "🧾 Мои уведомления",
        "notice_title": "💵 Электронное уведомление",
        "notice_start": (
            "Это уведомление для кассы, а не подтверждённая оплата.\n\n"
            "Оно не изменит баланс, кассу или доступ к шлагбауму, "
            "пока оператор не подтвердит фактическое поступление."
        ),
        "choose_type": "Выберите, о чём вы сообщаете.",
        "cash_type": "💵 Передал наличные",
        "bank_type": "🏦 Сделал банковский перевод",
        "default_period": "Период по умолчанию: {period}",
        "period_keep": "📅 Оставить: {period}",
        "period_change": "✏️ Изменить период",
        "choose_charge": "Выберите начисление, по которому передавались деньги.",
        "no_charge": (
            "За выбранный период открытых начислений не найдено.\n"
            "Выберите услугу и введите сумму вручную."
        ),
        "choose_service": "Выберите услугу из справочника.",
        "amount_manual": "Введите сумму, которую вы передали / перевели.",
        "cashbox_default": "Касса по умолчанию: O — охрана.",
        "comment": "Комментарий необязателен. Введите текст или «-».",
        "review": "Предпросмотр уведомления",
        "send": "✅ Отправить уведомление",
        "edit_amount": "✏️ Сумма",
        "edit_cashbox": "🏦 Касса",
        "edit_period": "📅 Период",
        "edit_comment": "📝 Комментарий",
        "cancel": "❌ Отменить",
        "back_parking": "⬅️ К парковке",
        "home": "🏠 Главное меню",
        "notice_sent": (
            "✅ Уведомление {number} зарегистрировано.\n\n"
            "Статус: ожидает подтверждения оператором.\n"
            "Это ещё не подтверждённая оплата."
        ),
        "notices_title": "🧾 Мои уведомления о платежах",
        "none": "Уведомлений пока нет.",
        "status_NEW": "🟡 Ожидает кассу / банк",
        "status_CONFIRMED": "✅ Подтверждено оператором",
        "status_REJECTED": "❌ Отклонено оператором",
        "status_CANCELLED": "⚪ Отменено",
        "cashbox_choose": "Выберите кассу, куда вы передали деньги.",
        "period_prompt": "Введите период ГГГГ-ММ, например 2026-07.",
        "wrong": "Выберите действие кнопкой.",
        "bank_no_ref": (
            "Для уведомления жителя ID банка пока не обязателен. "
            "Оператор подтвердит оплату только по банковской выписке."
        ),
    },
    "uk": {
        "cash_notice": "💵 Повідомити про передачу готівки",
        "bank_notice": "🏦 Повідомити про банківську оплату",
        "my_notices": "🧾 Мої повідомлення",
        "notice_title": "💵 Електронне повідомлення",
        "notice_start": (
            "Це повідомлення для каси, а не підтверджена оплата.\n\n"
            "Воно не змінить баланс, касу чи доступ до шлагбаума, "
            "доки оператор не підтвердить фактичне надходження."
        ),
        "choose_type": "Оберіть, про що ви повідомляєте.",
        "cash_type": "💵 Передав готівку",
        "bank_type": "🏦 Зробив банківський переказ",
        "default_period": "Період за замовчуванням: {period}",
        "period_keep": "📅 Залишити: {period}",
        "period_change": "✏️ Змінити період",
        "choose_charge": "Оберіть нарахування, за яким передавались гроші.",
        "no_charge": (
            "За обраний період відкритих нарахувань не знайдено.\n"
            "Оберіть послугу та введіть суму вручну."
        ),
        "choose_service": "Оберіть послугу з довідника.",
        "amount_manual": "Введіть суму, яку ви передали / переказали.",
        "cashbox_default": "Каса за замовчуванням: O — охорона.",
        "comment": "Коментар необов’язковий. Введіть текст або «-».",
        "review": "Попередній перегляд повідомлення",
        "send": "✅ Надіслати повідомлення",
        "edit_amount": "✏️ Сума",
        "edit_cashbox": "🏦 Каса",
        "edit_period": "📅 Період",
        "edit_comment": "📝 Коментар",
        "cancel": "❌ Скасувати",
        "back_parking": "⬅️ До паркування",
        "home": "🏠 Головне меню",
        "notice_sent": (
            "✅ Повідомлення {number} зареєстровано.\n\n"
            "Статус: очікує підтвердження оператором.\n"
            "Це ще не підтверджена оплата."
        ),
        "notices_title": "🧾 Мої повідомлення про платежі",
        "none": "Повідомлень поки немає.",
        "status_NEW": "🟡 Очікує касу / банк",
        "status_CONFIRMED": "✅ Підтверджено оператором",
        "status_REJECTED": "❌ Відхилено оператором",
        "status_CANCELLED": "⚪ Скасовано",
        "cashbox_choose": "Оберіть касу, куди ви передали гроші.",
        "period_prompt": "Введіть період ГГГГ-ММ, наприклад 2026-07.",
        "wrong": "Оберіть дію кнопкою.",
        "bank_no_ref": (
            "Для повідомлення жителя ID банку поки не обов’язковий. "
            "Оператор підтвердить оплату лише за банківською випискою."
        ),
    },
    "en": {
        "cash_notice": "💵 Report cash handover",
        "bank_notice": "🏦 Report bank payment",
        "my_notices": "🧾 My notices",
        "notice_title": "💵 Electronic notice",
        "notice_start": (
            "This is a notice for the cashier, not a confirmed payment.\n\n"
            "It does not change balance, cashbox, or gate access until the "
            "operator confirms actual receipt."
        ),
        "choose_type": "Choose what you are reporting.",
        "cash_type": "💵 I handed over cash",
        "bank_type": "🏦 I made a bank transfer",
        "default_period": "Default period: {period}",
        "period_keep": "📅 Keep: {period}",
        "period_change": "✏️ Change period",
        "choose_charge": "Choose the charge for which money was handed over.",
        "no_charge": (
            "No open charge was found for the selected period.\n"
            "Choose a service and enter the amount manually."
        ),
        "choose_service": "Choose a service from the catalogue.",
        "amount_manual": "Enter the amount you handed over / transferred.",
        "cashbox_default": "Default cashbox: O — security.",
        "comment": "Comment is optional. Enter text or “-”.",
        "review": "Notice preview",
        "send": "✅ Send notice",
        "edit_amount": "✏️ Amount",
        "edit_cashbox": "🏦 Cashbox",
        "edit_period": "📅 Period",
        "edit_comment": "📝 Comment",
        "cancel": "❌ Cancel",
        "back_parking": "⬅️ Back to parking",
        "home": "🏠 Main menu",
        "notice_sent": (
            "✅ Notice {number} has been registered.\n\n"
            "Status: awaiting operator confirmation.\n"
            "This is not a confirmed payment yet."
        ),
        "notices_title": "🧾 My payment notices",
        "none": "There are no notices yet.",
        "status_NEW": "🟡 Awaiting cashier / bank",
        "status_CONFIRMED": "✅ Confirmed by operator",
        "status_REJECTED": "❌ Rejected by operator",
        "status_CANCELLED": "⚪ Cancelled",
        "cashbox_choose": "Choose the cashbox where you handed over money.",
        "period_prompt": "Enter period YYYY-MM, for example 2026-07.",
        "wrong": "Choose an action using the buttons.",
        "bank_no_ref": (
            "A bank reference is not required for a resident notice yet. "
            "The operator will confirm payment only from the bank statement."
        ),
    },
}


def t(lang: str, key: str, **kwargs: Any) -> str:
    return VT.get(lang, VT["ru"])[key].format(**kwargs)


def kb(rows: list[list[str]]) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


# These two functions are used by parking_bot.py after the import path is swapped.
def client_menu_keyboard(lang: str) -> list[list[str]]:
    return base.client_menu_keyboard(lang)


def client_welcome_text(lang: str) -> str:
    return base.client_welcome_text(lang)


def _state(user_states: dict, user_id: int, *, create: bool = False) -> dict | None:
    value = user_states.get(user_id)
    if isinstance(value, dict) and value.get("_module") == "client_notice_v2":
        return value
    if create:
        value = {"_module": "client_notice_v2", "mode": "parking"}
        user_states[user_id] = value
        return value
    return None


def _base_portal_active(user_states: dict, user_id: int) -> bool:
    value = user_states.get(user_id)
    return isinstance(value, dict) and value.get("_module") == "client_portal"


def _parking_keyboard(lang: str) -> list[list[str]]:
    return [
        [base.tr(lang, "parking_balance")],
        [base.tr(lang, "parking_charges"), base.tr(lang, "parking_payments")],
        [t(lang, "cash_notice"), t(lang, "bank_notice")],
        [t(lang, "my_notices")],
        [base.tr(lang, "parking_how")],
        [t(lang, "back_parking"), t(lang, "home")],
    ]


def _cashbox_label(code: str, lang: str = "ru") -> str:
    labels = {
        "ru": {
            "O": "🛡 O — касса охраны",
            "K1": "K1 — консьерж 1",
            "K2": "K2 — консьерж 2",
            "K3": "K3 — консьерж 3",
            "K4": "K4 — консьерж 4",
            "K5": "K5 — консьерж 5",
            "K6": "K6 — консьерж 6",
            "C": "🏦 C — центральная касса",
        },
        "uk": {
            "O": "🛡 O — каса охорони",
            "K1": "K1 — консьєрж 1",
            "K2": "K2 — консьєрж 2",
            "K3": "K3 — консьєрж 3",
            "K4": "K4 — консьєрж 4",
            "K5": "K5 — консьєрж 5",
            "K6": "K6 — консьєрж 6",
            "C": "🏦 C — центральна каса",
        },
        "en": {
            "O": "🛡 O — security cashbox",
            "K1": "K1 — concierge 1",
            "K2": "K2 — concierge 2",
            "K3": "K3 — concierge 3",
            "K4": "K4 — concierge 4",
            "K5": "K5 — concierge 5",
            "K6": "K6 — concierge 6",
            "C": "🏦 C — central cashbox",
        },
    }
    return labels.get(lang, labels["ru"]).get(code, code)


def _cashbox_buttons(lang: str) -> list[list[str]]:
    labels = [_cashbox_label(code, lang) for code in CASH_CODES]
    return [labels[i:i + 2] for i in range(0, len(labels), 2)] + [[t(lang, "back_parking")]]


def _cashbox_code(label: str) -> str | None:
    label = text(label)
    for lang in ("ru", "uk", "en"):
        for code in CASH_CODES:
            if label == _cashbox_label(code, lang):
                return code
    return None


def _service_buttons(state: dict, *, period_code: str, lang: str) -> list[list[str]]:
    mapping = {}
    labels = []
    for service in service_options(period_code):
        label = f"📌 {service_label(service)}"
        suffix = 2
        initial = label
        while label in mapping:
            label = f"{initial} [{suffix}]"
            suffix += 1
        mapping[label] = service
        labels.append(label)
    state["notice_service_buttons"] = mapping
    return [labels[i:i + 2] for i in range(0, len(labels), 2)] + [[t(lang, "back_parking"), t(lang, "home")]]


def _charge_buttons(state: dict, charges: list[dict], lang: str) -> list[list[str]]:
    mapping = {}
    buttons = []
    for charge in charges:
        label = (
            f"🧾 {charge.get('period_code') or '-'} | "
            f"{charge.get('service_code') or '-'} | "
            f"{money(charge['outstanding_amount'])}"
        )
        mapping[label] = charge
        buttons.append([label])
    state["notice_charge_buttons"] = mapping
    buttons.extend([[t(lang, "back_parking"), t(lang, "home")]])
    return buttons


def _notice_summary(state: dict, lang: str) -> str:
    unit = state.get("unit") or {}
    service = state.get("service") or {}
    kind = state.get("notice_type")
    type_label = t(lang, "cash_type") if kind == "CASH_HANDOVER" else t(lang, "bank_type")
    return "\n".join([
        t(lang, "review"),
        "",
        f"{type_label}",
        f"{base.tr(lang, 'home_label')}: {unit.get('apartment_number') or '-'}",
        f"{base.tr(lang, 'parking_charges')}: {service_label(service)}",
        f"{t(lang, 'edit_period')}: {state.get('period_code') or '-'}",
        (
            f"{t(lang, 'edit_cashbox')}: {_cashbox_label(state.get('cashbox_code') or 'O', lang)}"
            if kind == "CASH_HANDOVER" else "🏦 BANK"
        ),
        f"{t(lang, 'edit_amount')}: {money(state.get('amount'))} грн.",
        f"{t(lang, 'edit_comment')}: {state.get('comment') or '—'}",
        "",
        t(lang, "notice_start"),
    ])


async def show_parking_v2(update: Update, user_states: dict, user_id: int, lang: str) -> None:
    data = base._account_and_unit(user_id)
    if not data or not data.get("unit"):
        await base.show_client_portal(update, user_states, user_id, lang)
        return
    state = _state(user_states, user_id, create=True)
    state.clear()
    state.update({"_module": "client_notice_v2", "mode": "parking"})
    await update.message.reply_text(
        base._format_parking(data, base._billing_data(data["unit"]), lang),
        reply_markup=kb(_parking_keyboard(lang)),
    )


async def _start_notice(update: Update, user_states: dict, user_id: int, lang: str, notice_type: str) -> None:
    data = base._account_and_unit(user_id)
    if not data or not data.get("unit"):
        await base.show_client_portal(update, user_states, user_id, lang)
        return

    state = _state(user_states, user_id, create=True)
    state.clear()
    state.update({
        "_module": "client_notice_v2",
        "mode": "notice_period",
        "notice_type": notice_type,
        "account": data["account"],
        "unit": data["unit"],
        "period_code": next_month(),
        "cashbox_code": "O",
        "amount": None,
        "comment": "",
        "charge": None,
    })
    await update.message.reply_text(
        f"{t(lang, 'notice_title')}\n\n{t(lang, 'notice_start')}\n\n"
        f"{t(lang, 'default_period', period=next_month())}",
        reply_markup=kb([
            [t(lang, "period_keep", period=next_month())],
            [t(lang, "period_change")],
            [t(lang, "back_parking"), t(lang, "home")],
        ]),
    )


async def _select_charge_or_service(update: Update, state: dict, lang: str) -> None:
    unit = state["unit"]
    charges = open_charges(
        apartment_id=int(unit["id"]),
        period_code=state.get("period_code"),
    )
    if len(charges) == 1:
        charge = charges[0]
        state["charge"] = charge
        state["service"] = {
            "service_code": charge.get("service_code") or "UNSPECIFIED",
            "service_item_code": charge.get("service_item_code"),
            "service_type": "GENERAL",
            "service_name": charge.get("service_code") or "UNSPECIFIED",
        }
        state["amount"] = float(charge["outstanding_amount"])
        await _review_notice(update, state, lang)
        return
    if charges:
        state["mode"] = "notice_charge"
        await update.message.reply_text(
            t(lang, "choose_charge"),
            reply_markup=kb(_charge_buttons(state, charges, lang)),
        )
        return
    state["mode"] = "notice_service"
    await update.message.reply_text(
        t(lang, "no_charge") + "\n\n" + t(lang, "choose_service"),
        reply_markup=kb(_service_buttons(state, period_code=state["period_code"], lang=lang)),
    )


async def _review_notice(update: Update, state: dict, lang: str) -> None:
    state["mode"] = "notice_review"
    rows = [
        [t(lang, "send")],
        [t(lang, "edit_amount"), t(lang, "edit_period")],
    ]
    if state["notice_type"] == "CASH_HANDOVER":
        rows.append([t(lang, "edit_cashbox")])
    rows.extend([
        [t(lang, "edit_comment")],
        [t(lang, "cancel"), t(lang, "back_parking")],
        [t(lang, "home")],
    ])
    await update.message.reply_text(_notice_summary(state, lang), reply_markup=kb(rows))


async def _show_my_notices(update: Update, user_id: int, lang: str) -> None:
    data = base._account_and_unit(user_id)
    if not data:
        await base.show_client_portal(update, {}, user_id, lang)
        return
    rows = list_resident_notices(int(data["account"]["id"]))
    lines = [t(lang, "notices_title"), ""]
    for item in rows:
        status = t(lang, f"status_{item.get('notice_status') or 'NEW'}")
        kind = t(lang, "cash_type") if item.get("notice_type") == "CASH_HANDOVER" else t(lang, "bank_type")
        lines.append(
            f"{item.get('notice_number')}\n"
            f"{kind} | {money(item.get('declared_amount'))} грн. | {status}\n"
            f"{item.get('declared_period_code') or '-'} | {item.get('declared_cashbox_code') or 'BANK'}"
        )
    if not rows:
        lines.append(t(lang, "none"))
    await update.message.reply_text(
        "\n\n".join(lines),
        reply_markup=kb(_parking_keyboard(lang)),
    )


async def handle_client_portal_text(
    update: Update,
    user_states: dict,
    user_id: int,
    message_text: str,
    *,
    lang: str,
    user_mode: str | None,
    is_admin: bool = False,
) -> bool:
    message_text = text(message_text)
    lang = lang if lang in VT else "ru"

    # Admin uses base handler and cashier v2; resident notices are client-only.
    if user_mode != "client":
        return await base.handle_client_portal_text(
            update, user_states, user_id, message_text,
            lang=lang, user_mode=user_mode, is_admin=is_admin,
        )

    state = _state(user_states, user_id, create=False)
    mode = text(state.get("mode")) if state else ""

    # Enter the enhanced parking sub-menu before base sees its old parking command.
    if message_text == base.tr(lang, "parking") and not mode:
        await show_parking_v2(update, user_states, user_id, lang)
        return True

    if not mode:
        return await base.handle_client_portal_text(
            update, user_states, user_id, message_text,
            lang=lang, user_mode=user_mode, is_admin=is_admin,
        )

    if message_text == t(lang, "home"):
        user_states.pop(user_id, None)
        return False

    if message_text == t(lang, "back_parking"):
        await show_parking_v2(update, user_states, user_id, lang)
        return True

    if mode == "parking":
        data = base._account_and_unit(user_id)
        if not data or not data.get("unit"):
            await base.show_client_portal(update, user_states, user_id, lang)
            return True
        billing = base._billing_data(data["unit"])

        if message_text == base.tr(lang, "parking_balance"):
            await update.message.reply_text(base._format_parking(data, billing, lang), reply_markup=kb(_parking_keyboard(lang)))
            return True
        if message_text == base.tr(lang, "parking_charges"):
            await update.message.reply_text(base._format_charges(data, billing, lang), reply_markup=kb(_parking_keyboard(lang)))
            return True
        if message_text == base.tr(lang, "parking_payments"):
            await update.message.reply_text(base._format_payments(data, billing, lang), reply_markup=kb(_parking_keyboard(lang)))
            return True
        if message_text == base.tr(lang, "parking_how"):
            await update.message.reply_text(base.tr(lang, "payment_help"), reply_markup=kb(_parking_keyboard(lang)))
            return True
        if message_text == t(lang, "cash_notice"):
            await _start_notice(update, user_states, user_id, lang, "CASH_HANDOVER")
            return True
        if message_text == t(lang, "bank_notice"):
            await _start_notice(update, user_states, user_id, lang, "BANK_TRANSFER")
            return True
        if message_text == t(lang, "my_notices"):
            await _show_my_notices(update, user_id, lang)
            return True
        await update.message.reply_text(t(lang, "wrong"), reply_markup=kb(_parking_keyboard(lang)))
        return True

    if mode == "notice_period":
        if message_text == t(lang, "period_keep", period=next_month()):
            state["period_code"] = next_month()
            await _select_charge_or_service(update, state, lang)
            return True
        if message_text == t(lang, "period_change"):
            state["mode"] = "notice_period_manual"
            await update.message.reply_text(t(lang, "period_prompt"), reply_markup=kb([[t(lang, "back_parking"), t(lang, "home")]]))
            return True
        await update.message.reply_text(t(lang, "wrong"))
        return True

    if mode == "notice_period_manual":
        try:
            state["period_code"] = normalize_period(message_text, required=True)
        except Exception as exc:
            await update.message.reply_text(f"⚠️ {exc}")
            return True
        await _select_charge_or_service(update, state, lang)
        return True

    if mode == "notice_charge":
        charge = (state.get("notice_charge_buttons") or {}).get(message_text)
        if not charge:
            await update.message.reply_text(t(lang, "wrong"))
            return True
        state["charge"] = charge
        state["service"] = {
            "service_code": charge.get("service_code") or "UNSPECIFIED",
            "service_item_code": charge.get("service_item_code"),
            "service_type": "GENERAL",
            "service_name": charge.get("service_code") or "UNSPECIFIED",
        }
        state["amount"] = float(charge["outstanding_amount"])
        await _review_notice(update, state, lang)
        return True

    if mode == "notice_service":
        service = (state.get("notice_service_buttons") or {}).get(message_text)
        if not service:
            await update.message.reply_text(t(lang, "wrong"))
            return True
        state["service"] = service
        state["charge"] = None
        state["mode"] = "notice_amount"
        await update.message.reply_text(t(lang, "amount_manual"), reply_markup=kb([[t(lang, "back_parking"), t(lang, "home")]]))
        return True

    if mode == "notice_amount":
        try:
            state["amount"] = parse_amount(message_text)
        except Exception as exc:
            await update.message.reply_text(f"⚠️ {exc}")
            return True
        await _review_notice(update, state, lang)
        return True

    if mode == "notice_cashbox":
        code = _cashbox_code(message_text)
        if not code:
            await update.message.reply_text(t(lang, "cashbox_choose"))
            return True
        state["cashbox_code"] = code
        await _review_notice(update, state, lang)
        return True

    if mode == "notice_comment":
        state["comment"] = "" if message_text == "-" else message_text
        await _review_notice(update, state, lang)
        return True

    if mode == "notice_review":
        if message_text == t(lang, "send"):
            try:
                result = create_payment_notice(
                    account=state["account"],
                    apartment=state["unit"],
                    notice_type=state["notice_type"],
                    declared_cashbox_code=state.get("cashbox_code") if state["notice_type"] == "CASH_HANDOVER" else None,
                    period_code=state.get("period_code"),
                    service=state["service"],
                    amount=parse_amount(state["amount"]),
                    resident_comment=state.get("comment") or "",
                )
            except Exception as exc:
                await update.message.reply_text(f"⚠️ {exc}")
                return True
            await update.message.reply_text(t(lang, "notice_sent", number=result["notice_number"]))
            await show_parking_v2(update, user_states, user_id, lang)
            return True
        if message_text == t(lang, "edit_amount"):
            state["mode"] = "notice_amount"
            await update.message.reply_text(t(lang, "amount_manual"), reply_markup=kb([[t(lang, "back_parking"), t(lang, "home")]]))
            return True
        if message_text == t(lang, "edit_period"):
            state["mode"] = "notice_period_manual"
            await update.message.reply_text(t(lang, "period_prompt"), reply_markup=kb([[t(lang, "back_parking"), t(lang, "home")]]))
            return True
        if message_text == t(lang, "edit_cashbox") and state["notice_type"] == "CASH_HANDOVER":
            state["mode"] = "notice_cashbox"
            await update.message.reply_text(t(lang, "cashbox_choose"), reply_markup=kb(_cashbox_buttons(lang)))
            return True
        if message_text == t(lang, "edit_comment"):
            state["mode"] = "notice_comment"
            await update.message.reply_text(t(lang, "comment"), reply_markup=kb([[t(lang, "back_parking"), t(lang, "home")]]))
            return True
        if message_text == t(lang, "cancel"):
            await show_parking_v2(update, user_states, user_id, lang)
            return True
        await update.message.reply_text(t(lang, "wrong"))
        return True

    # A stale v2 state should not fall into old client portal text parser.
    await show_parking_v2(update, user_states, user_id, lang)
    return True
