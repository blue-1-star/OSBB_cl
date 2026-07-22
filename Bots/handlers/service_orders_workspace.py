# -*- coding: utf-8 -*-
"""
Telegram UI for simplified OSBB services.

Resident flow:
    interest with quantity -> payment notice -> confirmed cashier payment
    -> real paid service order is created automatically.

New remote flow:
    paid preorder -> aggregated supplier batch -> delivery received -> issue.

The UI never offers a fictitious per-unit stock reservation before a supplier
actually delivers a batch. Existing historic orders can remain in the database
unchanged; this module is for the new simplified workflow.
"""

from __future__ import annotations

from pathlib import Path
import re
import sqlite3
import sys
from typing import Any

from telegram import ReplyKeyboardMarkup, Update

HANDLERS_DIR = Path(__file__).resolve().parent
BOTS_DIR = HANDLERS_DIR.parent
ROOT = BOTS_DIR.parent
for folder in (ROOT, BOTS_DIR):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))

from access_control import has_permission, write_access_audit
from cashier_v2_core import create_payment_notice
from handlers import client_portal as resident_portal
from service_orders_core import (
    activate_access_credential,
    create_remote_asset,
    get_conn,
    money,
    record_remote_movement,
    service_order_summary,
    table_columns,
    text,
)
from service_preorders_core import (
    INTEREST,
    PAID_ORDER_CREATED,
    PAYMENT_NOTICE,
    NEW_REMOTE_PROFILE,
    attach_payment_notice_to_interest,
    create_service_interest,
    create_supplier_batch,
    ensure_simplified_service_schema,
    get_service_interest,
    get_supplier_batch,
    issue_new_remotes_from_batch,
    list_resident_service_interests,
    list_supplier_batches,
    order_supply_link,
    receive_supplier_batch,
    reconcile_paid_service_interests,
    supplier_demand,
    unpaid_interest_totals,
)
try:
    from service_access_policy import ServiceAccessDenied, result_to_short_text
except Exception:
    ServiceAccessDenied = None
    result_to_short_text = None
from phone_barrier_access_core import BARRIER_FAR_01, BARRIER_NEAR_02
from phone_barrier_access_service import (
    activate_phone_barrier_access_order,
    create_phone_barrier_access_interest,
    phone_access_summary_for_interest,
    phone_access_summary_for_order,
    phone_barrier_quote,
)
from profile_verification_core import phone_access_eligibility


MODULE = "service_orders_ui"
HOME = "🏠 Главное меню"
BACK = "⬅️ К услугам"
OPERATOR_BUTTON = "🔑 Оператор услуг"
CLIENT_ENTRY = "🔑 Пульты и доступ"


I18N: dict[str, dict[str, str]] = {
    "ru": {
        "title": "🔑 Пульты и доступ",
        "intro": (
            "Выберите услугу и количество. До подтверждённой оплаты это только "
            "намерение; после оплаты система сама создаёт настоящий заказ."
        ),
        "reprogram": "♻️ Перепрошить мой пульт",
        "new_remote": "🆕 Получить новые пульты",
        "refurbished": "🔄 Получить восстановленный пульт",
        "phone": "📱 Подключить телефон",
        "phone_title": "📞 Открытие шлагбаума телефоном",
        "phone_intro": "Укажите отдельный номер телефона, которому нужен доступ к шлагбауму. Он может отличаться от контактных номеров в кабинете. После подтверждённой оплаты заявка будет автоматически передана оператору для активации.",
        "phone_resident_prompt": "Введіть окремий номер телефону для доступу, наприклад +380501234567. Він не зобов’язаний збігатися з контактними номерами в кабінеті.",
        "phone_preview": "📄 Подтверждение намерения — телефонный доступ",
        "phone_number": "Номер телефона",
        "phone_operator_title": "📞 Телефонный доступ",
        "phone_operator_intro": "Здесь показаны только оплаченные заявки на телефонный доступ. После активации номера заявка завершается.",
        "phone_activated": "✅ Телефон {phone} активирован.",
        "my_requests": "📋 Мои услуги",
        "back_portal": "⬅️ В кабинет",
        "choose_quantity": "Сколько новых пультов нужно?",
        "other_quantity": "✏️ Другое количество",
        "preview": "📄 Подтверждение намерения",
        "create_interest": "✅ Зафиксировать намерение",
        "cancel": "❌ Отменить",
        "interest_saved": "✅ Намерение записано. Настоящий заказ появится после подтверждённой оплаты.",
        "cash_notice": "💵 Передать наличные в O",
        "bank_notice": "🏦 Сообщить об оплате через банк",
        "notice_saved": "✅ Уведомление {number} зарегистрировано. После подтверждения оплаты заказ создастся автоматически.",
        "need_unit": "Сначала привяжите квартиру в личном кабинете.",
        "no_offers": "Активных услуг пока нет.",
        "my_title": "📋 Мои услуги",
        "no_items": "Записей пока нет.",
        "operator_title": "🔑 Оператор услуг",
        "operator_intro": (
            "Неоплаченные намерения не засоряют работу. Здесь видны реальные оплаченные "
            "заказы, потребность для поставщика и полученные поставки."
        ),
        "open_orders": "📋 Оплаченные заявки",
        "supplier_demand": "🧾 Потребность поставщику",
        "supplier_batches": "📦 Поставки пультов",
        "refresh": "🔄 Обновить",
        "no_orders": "Нет оплаченных незавершённых заявок.",
        "no_demand": "Нет оплаченных передзаказов, не включённых в заказ поставщику.",
        "create_batch": "🧾 Сформировать заказ поставщику",
        "receive_delivery": "📥 Принять поставку",
        "issue_new": "📤 Выдать новые пульты жильцу",
        "receive_remote": "📥 Принять пульт жильца",
        "program_done": "🛠 Перепрошивка выполнена",
        "return_remote": "📤 Вернуть пульт жильцу",
        "activate_phone": "📱 Активировать телефонный доступ",
        "asset_prompt": "Введите маркировку принятого пульта, например OLD-174-01.",
        "phone_prompt": "Введите телефон для доступа, например +380501234567.",
        "wrong_phone": "Укажите номер: 8–20 цифр, можно начать с +.",
        "delivery_qty_prompt": "Введите количество фактически полученных пультов по этой поставке.",
        "denied": "⛔ Нет права на этот раздел или действие.",
        "wrong": "Выберите действие кнопкой.",
        "completed": "✅ Заявка завершена.",
        "interest_status": "Намерение",
        "paid_order_status": "Оплачен и передан в работу",
        "awaiting_payment": "Ожидает подтверждения оплаты",
        "awaiting_supplier": "Ожидает заказа поставщику",
        "awaiting_supply": "Ожидает поставку",
        "ready_for_issue": "Готов к выдаче",
    },
    "uk": {
        "title": "🔑 Пульти та доступ",
        "intro": (
            "Оберіть послугу й кількість. До підтвердженої оплати це лише намір; "
            "після оплати система сама створює справжнє замовлення."
        ),
        "reprogram": "♻️ Перепрограмувати мій пульт",
        "new_remote": "🆕 Отримати нові пульти",
        "refurbished": "🔄 Отримати відновлений пульт",
        "phone": "📱 Підключити телефон",
        "phone_title": "📞 Відкриття шлагбаума телефоном",
        "phone_intro": "Вкажіть окремий номер телефону, якому потрібен доступ до шлагбаума. Він може відрізнятися від контактних номерів у кабінеті. Після підтвердженої оплати заявку буде автоматично передано оператору для активації.",
        "phone_resident_prompt": "Введіть окремий номер телефону для доступу, наприклад +380501234567. Він не зобов’язаний збігатися з контактними номерами в кабінеті.",
        "phone_preview": "📄 Підтвердження наміру — телефонний доступ",
        "phone_number": "Номер телефону",
        "phone_operator_title": "📞 Телефонний доступ",
        "phone_operator_intro": "Тут показані лише оплачені заявки на телефонний доступ. Після активації номера заявку буде завершено.",
        "phone_activated": "✅ Телефон {phone} активовано.",
        "my_requests": "📋 Мої послуги",
        "back_portal": "⬅️ До кабінету",
        "choose_quantity": "Скільки нових пультів потрібно?",
        "other_quantity": "✏️ Інша кількість",
        "preview": "📄 Підтвердження наміру",
        "create_interest": "✅ Зафіксувати намір",
        "cancel": "❌ Скасувати",
        "interest_saved": "✅ Намір записано. Справжнє замовлення з’явиться після підтвердженої оплати.",
        "cash_notice": "💵 Передати готівку в O",
        "bank_notice": "🏦 Повідомити про оплату через банк",
        "notice_saved": "✅ Повідомлення {number} зареєстровано. Після підтвердження оплати замовлення створиться автоматично.",
        "need_unit": "Спочатку прив’яжіть квартиру в особистому кабінеті.",
        "no_offers": "Активних послуг поки немає.",
        "my_title": "📋 Мої послуги",
        "no_items": "Записів поки немає.",
        "operator_title": "🔑 Оператор послуг",
        "operator_intro": (
            "Неоплачені наміри не засмічують роботу. Тут видно реальні оплачені "
            "замовлення, потребу постачальнику та отримані поставки."
        ),
        "open_orders": "📋 Оплачені заявки",
        "supplier_demand": "🧾 Потреба постачальнику",
        "supplier_batches": "📦 Поставки пультів",
        "refresh": "🔄 Оновити",
        "no_orders": "Немає оплачених незавершених заявок.",
        "no_demand": "Немає оплачених передзамовлень, не включених у замовлення постачальнику.",
        "create_batch": "🧾 Сформувати замовлення постачальнику",
        "receive_delivery": "📥 Прийняти поставку",
        "issue_new": "📤 Видати нові пульти мешканцю",
        "receive_remote": "📥 Прийняти пульт мешканця",
        "program_done": "🛠 Перепрограмування виконано",
        "return_remote": "📤 Повернути пульт мешканцю",
        "activate_phone": "📱 Активувати телефонний доступ",
        "asset_prompt": "Введіть маркування прийнятого пульта, наприклад OLD-174-01.",
        "phone_prompt": "Введіть телефон для доступу, наприклад +380501234567.",
        "wrong_phone": "Вкажіть номер: 8–20 цифр, можна почати з +.",
        "delivery_qty_prompt": "Введіть кількість фактично отриманих пультів за цією поставкою.",
        "denied": "⛔ Немає права на цей розділ або дію.",
        "wrong": "Оберіть дію кнопкою.",
        "completed": "✅ Заявку завершено.",
        "interest_status": "Намір",
        "paid_order_status": "Оплачено і передано в роботу",
        "awaiting_payment": "Очікує підтвердження оплати",
        "awaiting_supplier": "Очікує замовлення постачальнику",
        "awaiting_supply": "Очікує поставку",
        "ready_for_issue": "Готово до видачі",
    },
    "en": {
        "title": "🔑 Remotes and access",
        "intro": "Select a service and quantity. It becomes a real order only after payment confirmation.",
        "reprogram": "♻️ Reprogram my remote",
        "new_remote": "🆕 Get new remotes",
        "refurbished": "🔄 Get refurbished remote",
        "phone": "📱 Connect phone access",
        "phone_title": "📞 Phone gate access",
        "phone_intro": "Enter the separate phone number that needs gate access. It may differ from profile contacts. After confirmed payment, the request is automatically sent to an operator for activation.",
        "phone_resident_prompt": "Enter the separate access phone number, for example +380501234567. It need not match a profile contact.",
        "phone_preview": "📄 Intent confirmation — phone access",
        "phone_number": "Phone number",
        "phone_operator_title": "📞 Phone access",
        "phone_operator_intro": "Only paid phone-access orders are shown here. The order is completed once the number is activated.",
        "phone_activated": "✅ Phone {phone} activated.",
        "my_requests": "📋 My services",
        "back_portal": "⬅️ Back to portal",
        "choose_quantity": "How many new remotes?",
        "other_quantity": "✏️ Other quantity",
        "preview": "📄 Intent confirmation",
        "create_interest": "✅ Record intent",
        "cancel": "❌ Cancel",
        "interest_saved": "✅ Intent recorded. A real order will be created after payment confirmation.",
        "cash_notice": "💵 Pay cash at O",
        "bank_notice": "🏦 Report bank payment",
        "notice_saved": "✅ Notice {number} recorded. The order will be created automatically after payment confirmation.",
        "need_unit": "Link your apartment in the resident portal first.",
        "no_offers": "No active services are available.",
        "my_title": "📋 My services",
        "no_items": "No records yet.",
        "operator_title": "🔑 Service operator",
        "operator_intro": "Only paid operational orders are shown here.",
        "open_orders": "📋 Paid orders",
        "supplier_demand": "🧾 Supplier demand",
        "supplier_batches": "📦 Remote deliveries",
        "refresh": "🔄 Refresh",
        "no_orders": "No open paid orders.",
        "no_demand": "No paid preorders awaiting a supplier batch.",
        "create_batch": "🧾 Create supplier order",
        "receive_delivery": "📥 Receive delivery",
        "issue_new": "📤 Issue new remotes",
        "receive_remote": "📥 Receive resident remote",
        "program_done": "🛠 Reprogramming done",
        "return_remote": "📤 Return remote",
        "activate_phone": "📱 Activate phone access",
        "asset_prompt": "Enter the received remote label, for example OLD-174-01.",
        "phone_prompt": "Enter the access phone number.",
        "wrong_phone": "Use 8–20 digits; a leading + is allowed.",
        "delivery_qty_prompt": "Enter the quantity actually received in this delivery.",
        "denied": "⛔ You do not have permission for this action.",
        "wrong": "Use a button.",
        "completed": "✅ Order completed.",
        "interest_status": "Interest",
        "paid_order_status": "Paid and operational",
        "awaiting_payment": "Awaiting payment confirmation",
        "awaiting_supplier": "Awaiting supplier order",
        "awaiting_supply": "Awaiting delivery",
        "ready_for_issue": "Ready for issue",
    },
}

PROFILE_TO_KEY = {
    "REMOTE_REPROGRAM_OWN": "reprogram",
    NEW_REMOTE_PROFILE: "new_remote",
    "REMOTE_REFURBISHED_FROM_STOCK": "refurbished",
    "PHONE_ACCESS_CONNECT": "phone",
}

STATUS_LABELS = {
    "AWAITING_RESIDENT_ASSET": "📥 Очікує пульт",
    "AWAITING_PAYMENT": "💳 Очікує оплату",
    "AWAITING_SUPPLIER_ORDER": "🧾 Очікує замовлення постачальнику",
    "AWAITING_SUPPLY": "🚚 Очікує поставку",
    "AWAITING_STOCK": "📦 Очікує резерв",
    "IN_PROGRESS": "🛠 У роботі",
    "READY_FOR_ISSUE": "📤 Готово до видачі",
    "COMPLETED": "✅ Завершено",
    "CANCELLED": "⚪ Скасовано",
}


PHONE_UI: dict[str, dict[str, str]] = {
    "ru": {
        "choose_points": "Выберите, к каким шлагбаумам нужен доступ.",
        "far": "1️⃣ Дальний шлагбаум №1",
        "near": "2️⃣ Ближний шлагбаум №2",
        "both": "1️⃣+2️⃣ Оба шлагбаума",
        "points": "Шлагбаумы",
        "connect_fee": "Подключение",
        "monthly_fee": "Абонплата",
        "first_month": "Первая абонплата",
        "monthly_suffix": "/ месяц",
        "debt_note": (
            "При подтверждённой задолженности действует предупреждение; "
            "после установленного срока доступ отключается на всех выбранных шлагбаумах."
        ),
        "debt_mode": "Проверка парковочной задолженности: требуется подтверждение оператора.",
        "sandbox_activate": "Учётная активация в sandbox; команд реальным контроллерам не отправляется.",
        "private_phone_note": (
            "Номер для відкриття є окремим непублічним номером доступу. "
            "Він може відрізнятися від контактних номерів у кабінеті та не звіряється з ними."
        ),
    },
    "uk": {
        "choose_points": "Оберіть, до яких шлагбаумів потрібен доступ.",
        "far": "1️⃣ Далекий шлагбаум №1",
        "near": "2️⃣ Ближній шлагбаум №2",
        "both": "1️⃣+2️⃣ Обидва шлагбауми",
        "points": "Шлагбауми",
        "connect_fee": "Підключення",
        "monthly_fee": "Абонплата",
        "first_month": "Перша абонплата",
        "monthly_suffix": "/ місяць",
        "debt_note": (
            "За підтвердженої заборгованості надсилається попередження; "
            "після встановленого строку доступ вимикається на всіх обраних шлагбаумах."
        ),
        "debt_mode": "Перевірка заборгованості за паркування: потрібне підтвердження оператора.",
        "sandbox_activate": "Облікова активація у sandbox; команд реальним контролерам не надсилається.",
        "private_phone_note": (
            "Номер для відкриття є окремим непублічним номером доступу. "
            "Він може відрізнятися від контактних номерів у кабінеті та не звіряється з ними."
        ),
    },
    "en": {
        "choose_points": "Choose the barriers that need access.",
        "far": "1️⃣ Far barrier No. 1",
        "near": "2️⃣ Near barrier No. 2",
        "both": "1️⃣+2️⃣ Both barriers",
        "points": "Barriers",
        "connect_fee": "Connection",
        "monthly_fee": "Monthly fee",
        "first_month": "First monthly charge",
        "monthly_suffix": "/ month",
        "debt_note": (
            "A confirmed arrears case triggers a warning; after the configured "
            "deadline access is disabled on all selected barriers."
        ),
        "debt_mode": "Parking-debt check: operator confirmation is required.",
        "sandbox_activate": "Sandbox accounting activation; no commands are sent to real controllers.",
        "private_phone_note": (
            "The access number is a separate private credential. It may differ "
            "from profile contacts and is not matched against them."
        ),
    },
}


# ---------------------------------------------------------------------------
# Shared UI helpers
# ---------------------------------------------------------------------------

def tr(lang: str, key: str, **kwargs: Any) -> str:
    return I18N.get(lang, I18N["ru"]).get(key, I18N["ru"][key]).format(**kwargs)


def phone_tr(lang: str, key: str) -> str:
    return PHONE_UI.get(lang, PHONE_UI["ru"]).get(key, PHONE_UI["ru"][key])


def _phone_point_text(rows: list[dict]) -> str:
    values = [
        text(row.get("access_point_name_snapshot"))
        or text(row.get("access_point_name_uk"))
        or text(row.get("access_point_code"))
        for row in rows
    ]
    return ", ".join(value for value in values if value) or "—"


def _phone_access_summary_for_interest(interest_id: int | None) -> dict | None:
    if not interest_id:
        return None
    try:
        return phone_access_summary_for_interest(int(interest_id))
    except Exception:
        return None


def _phone_access_summary_for_order(order_id: int | None) -> dict | None:
    if not order_id:
        return None
    try:
        return phone_access_summary_for_order(int(order_id))
    except Exception:
        return None


def kb(rows: list[list[str]]) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def _state(user_states: dict, user_id: int, *, create: bool = False) -> dict | None:
    value = user_states.get(user_id)
    if isinstance(value, dict) and value.get("_module") == MODULE:
        return value
    if create:
        value = {"_module": MODULE, "area": "", "mode": ""}
        user_states[user_id] = value
        return value
    return None


def _home(message_text: str) -> bool:
    return text(message_text) in {"🏠 Главное меню", "🏠 Головне меню", "🏠 Main menu", "⬅️ Назад"}


def _service_back(message_text: str) -> bool:
    return text(message_text) in {BACK, "⬅️ До послуг", "⬅️ Back to services"}


def _operator_back(message_text: str) -> bool:
    return text(message_text) in {"⬅️ К оператору", "⬅️ До оператора", "⬅️ Back to operator"}


def _resident_entry_texts() -> set[str]:
    return {
        CLIENT_ENTRY, "🔑 Пульти та доступ", "🔑 Remotes and access",
        "🔑 Пульты", "🔑 Пульти", "🔑 Remotes",
        "📞 Открытие по телефону", "📞 Відкриття телефоном", "📞 Phone gate access",
    }


def _operator_entry_texts() -> set[str]:
    return {OPERATOR_BUTTON, "🔑 Оператор послуг", "🔑 Service operator"}


def _phone_entry_texts() -> set[str]:
    return {
        "📞 Открытие по телефону",
        "📞 Відкриття телефоном",
        "📞 Phone gate access",
    }


def _is_phone_offer(offer: dict | None) -> bool:
    """
    A resident-facing phone-access service is identified primarily by its
    ACCESS category, not by a single historic workflow-profile code.

    That keeps the user interface stable if the catalog uses a differently
    named active phone profile, while still preserving the workflow profile
    itself in the order record.
    """
    offer = offer or {}
    if text(offer.get("service_category")).upper() == "ACCESS":
        return True
    profile = text(offer.get("workflow_profile_code")).upper()
    return "PHONE" in profile or "ACCESS" in profile


def _normalise_phone(value: str) -> str:
    return re.sub(r"[\s()\-]", "", text(value))


def _valid_phone(value: str) -> bool:
    return re.fullmatch(r"\+?\d{8,20}", _normalise_phone(value)) is not None


def _requested_phone(record: dict | None) -> str:
    """Read the resident-requested access number without parsing free text."""
    comment = text((record or {}).get("resident_comment"))
    match = re.search(r"(?:^|\n)ACCESS_PHONE=(\+?\d{8,20})(?:$|\n)", comment)
    return match.group(1) if match else ""


def _phone_offer_sort_key(offer: dict) -> tuple[int, int, str]:
    """Choose the one resident-facing phone offer from possibly noisy catalog data."""
    profile = text(offer.get("workflow_profile_code")).upper()
    title = (
        text(offer.get("service_item_code"))
        + " "
        + text(offer.get("service_item_name"))
        + " "
        + profile
    ).upper()
    exact_profile = 1 if profile == "PHONE_ACCESS_CONNECT" else 0
    # Prefer a normal published service over a technical/test duplicate.
    non_test = 0 if ("ТЕСТ" in title or "TEST" in title) else 1
    return (exact_profile, non_test, title)


def _resident_phone_offer() -> dict | None:
    candidates = [offer for offer in _current_offers() if _is_phone_offer(offer)]
    if not candidates:
        return None
    return sorted(candidates, key=_phone_offer_sort_key, reverse=True)[0]


def _account_and_unit(user_id: int) -> dict | None:
    data = resident_portal._account_and_unit(user_id)
    if not data or not data.get("account") or not data.get("unit"):
        return None
    return data


def _category_allowed(user_id: int | str, category: str, action: str = "VIEW") -> bool:
    return has_permission(
        user_id,
        "service_orders",
        action,
        scope_type="SERVICE_CATEGORY",
        scope_value=text(category).upper() or "GENERAL",
    )


def has_service_workspace_access(user_id: int | str) -> bool:
    return _category_allowed(user_id, "REMOTE") or _category_allowed(user_id, "ACCESS")


def _ensure_and_reconcile() -> None:
    """Safe on every entry: creates schema once, promotes already-confirmed notices."""
    conn = get_conn()
    try:
        ensure_simplified_service_schema(conn)
        reconcile_paid_service_interests(conn=conn)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _current_offers() -> list[dict]:
    """
    Read offers from both supported service-item schemas.

    Some OSBB databases call the broad catalog column ``base_service_code``;
    others call it ``service_code``. The UI keeps one stable output field
    named ``service_code`` and never assumes a column that is absent.
    """
    conn = get_conn()
    try:
        cur = conn.cursor()
        item_columns = table_columns(cur, "service_items")
        service_code_field = (
            "i.service_code AS service_code"
            if "service_code" in item_columns
            else (
                "i.base_service_code AS service_code"
                if "base_service_code" in item_columns
                else "NULL AS service_code"
            )
        )
        cur.execute(
            f"""
            SELECT i.service_item_code, {service_code_field}, i.service_item_name,
                   i.service_type, i.amount_default, i.currency,
                   w.workflow_profile_code, w.resident_request_enabled,
                   p.service_category, p.profile_name
            FROM service_items i
            JOIN service_item_workflows w
              ON w.service_item_code = i.service_item_code
             AND w.is_active = 1
            JOIN service_workflow_profiles p
              ON p.profile_code = w.workflow_profile_code
             AND p.is_active = 1
            WHERE COALESCE(i.is_active, 1) = 1
              AND COALESCE(i.status, 'active') = 'active'
              AND w.resident_request_enabled = 1
              AND p.service_category IN ('REMOTE', 'ACCESS')
            ORDER BY p.service_category, i.service_item_name, i.service_item_code
            """
        )
        rows = [dict(row) for row in cur.fetchall()]
        from service_orders_core import effective_price
        for row in rows:
            amount, currency = effective_price(cur, row["service_item_code"])
            row["unit_price"] = amount
            row["currency"] = currency
        return rows
    finally:
        conn.close()


def _offer_buttons(state: dict, lang: str, *, section: str = "all") -> list[list[str]]:
    """
    Build a short resident menu.

    For phone access, expose exactly one published ACCESS service even if
    technical catalog rows or a differently named phone workflow also exist.
    """
    mapping: dict[str, dict] = {}
    buttons: list[list[str]] = []

    if section == "phone":
        offer = _resident_phone_offer()
        if offer:
            label = tr(lang, "phone")
            mapping[label] = offer
            buttons.append([label])
        state["offer_buttons"] = mapping
        # Keep navigation visible even if the catalog currently has no phone
        # offer, so the screen never becomes a dead-end.
        buttons += [[tr(lang, "my_requests")], [tr(lang, "back_portal"), HOME]]
        return buttons

    known: dict[str, dict] = {}
    extra: list[dict] = []
    for offer in _current_offers():
        profile = text(offer.get("workflow_profile_code"))
        if _is_phone_offer(offer):
            continue
        if profile in PROFILE_TO_KEY:
            if profile not in known:
                known[profile] = offer
            continue
        extra.append(offer)

    for profile in (
        "REMOTE_REPROGRAM_OWN",
        NEW_REMOTE_PROFILE,
        "REMOTE_REFURBISHED_FROM_STOCK",
    ):
        offer = known.get(profile)
        if not offer:
            continue
        label = tr(lang, PROFILE_TO_KEY[profile])
        mapping[label] = offer
        buttons.append([label])

    for offer in extra:
        label = f"🧩 {offer.get('service_item_name') or offer['service_item_code']}"
        mapping[label] = offer
        buttons.append([label])

    state["offer_buttons"] = mapping
    buttons += [[tr(lang, "my_requests")], [tr(lang, "back_portal"), HOME]]
    return buttons

def _next_waiting_step(order: dict) -> dict | None:
    waiting = [
        row for row in order.get("steps") or []
        if int(row.get("is_required") or 0) == 1
        and text(row.get("step_status")) not in {"CONFIRMED", "WAIVED"}
    ]
    return min(waiting, key=lambda row: (int(row.get("sequence_no") or 0), int(row.get("id") or 0))) if waiting else None


def _step_lines(order: dict) -> list[str]:
    result = []
    for row in order.get("steps") or []:
        icon = "✅" if text(row.get("step_status")) in {"CONFIRMED", "WAIVED"} else "⌛"
        result.append(f"{icon} {row.get('step_name') or row.get('step_code')}")
    return result or ["—"]


def _order_card(order: dict, *, title: str) -> str:
    quantity = int(float(order.get("quantity") or 1))
    phone_summary = _phone_access_summary_for_order(order.get("id"))
    lines = [
        title, "",
        f"№: {order.get('order_number')}",
        f"Квартира: {order.get('apartment_number') or '-'}",
        f"Услуга: {order.get('service_name_snapshot') or order.get('service_item_code')}",
    ]
    if phone_summary:
        points = phone_summary.get("points") or phone_summary.get("subscription_points") or []
        lines += [
            f"{phone_tr('uk', 'points')}: {_phone_point_text(points)}",
            *(_phone_point_status_lines(points) or ["• Статуси шлагбаумів ще не сформовані"]),
            f"Телефон доступу: {phone_summary.get('phone_normalized') or _requested_phone(order) or '-'}",
            f"{phone_tr('uk', 'connect_fee')}: {money(phone_summary.get('connection_total'))} {phone_summary.get('currency') or 'UAH'}",
            f"{phone_tr('uk', 'monthly_fee')}: {money(phone_summary.get('monthly_total'))} {phone_summary.get('currency') or 'UAH'} {phone_tr('uk', 'monthly_suffix')}",
        ]
        if phone_summary.get("first_charge_period"):
            lines.append(
                f"{phone_tr('uk', 'first_month')}: {phone_summary.get('first_charge_period')}"
            )
        subscription = phone_summary.get("subscription") or {}
        if subscription:
            lines.append(f"Статус підписки: {subscription.get('subscription_status')}")
    else:
        lines.append(f"Кількість: {quantity}")
        if _requested_phone(order):
            lines.append(f"Телефон доступу: {_requested_phone(order)}")
    lines += [
        f"Сума: {money(order.get('amount_due_snapshot'))} {order.get('currency') or 'UAH'}",
        f"Статус: {STATUS_LABELS.get(text(order.get('order_status')), order.get('order_status') or '-')}",
        f"Оплата: {order.get('payment_status') or '-'}", "",
        "Кроки:", *_step_lines(order),
    ]
    return "\n".join(lines)

def _interest_card(interest: dict, lang: str) -> str:
    status = text(interest.get("interest_status"))
    status_label = {
        INTEREST: tr(lang, "interest_status"),
        PAYMENT_NOTICE: tr(lang, "awaiting_payment"),
        PAID_ORDER_CREATED: tr(lang, "paid_order_status"),
    }.get(status, status)
    phone_summary = _phone_access_summary_for_interest(interest.get("id"))
    lines = [
        "📄 Намір на послугу", "",
        f"№: {interest.get('interest_number')}",
        f"Квартира: {interest.get('apartment_number') or '-'}",
        f"Послуга: {interest.get('service_name_snapshot') or interest.get('service_item_code')}",
    ]
    if phone_summary:
        points = phone_summary.get("points") or []
        lines += [
            f"{phone_tr(lang, 'points')}: {_phone_point_text(points)}",
            f"{tr(lang, 'phone_number')}: {phone_summary.get('phone_normalized') or _requested_phone(interest) or '-'}",
            f"{phone_tr(lang, 'connect_fee')}: {money(phone_summary.get('connection_total'))} {phone_summary.get('currency') or 'UAH'}",
            f"{phone_tr(lang, 'monthly_fee')}: {money(phone_summary.get('monthly_total'))} {phone_summary.get('currency') or 'UAH'} {phone_tr(lang, 'monthly_suffix')}",
            f"{phone_tr(lang, 'first_month')}: {phone_summary.get('first_charge_period') or 'визначається після підтвердження оплати'}",
        ]
    else:
        lines.append(f"Кількість: {interest.get('quantity')}")
        if _requested_phone(interest):
            lines.append(f"Телефон доступу: {_requested_phone(interest)}")
    lines += [
        f"Сума: {money(interest.get('amount_due_snapshot'))} {interest.get('currency') or 'UAH'}",
        f"Статус: {status_label}",
    ]
    return "\n".join(lines)


_PROFILE_VERIFY_BUTTON = {
    "ru": "📋 Проверить мои данные",
    "uk": "📋 Перевірити мої дані",
    "en": "📋 Verify my data",
}


def _phone_profile_gate(data: dict) -> dict:
    """Gate phone access by profile readiness, never by phone matching."""
    conn = get_conn()
    try:
        gate = phone_access_eligibility(
            resident_account_id=int(data["account"]["id"]),
            apartment_id=data["unit"].get("id"),
            apartment_number=text(data["unit"].get("apartment_number")),
            conn=conn,
        )
        conn.commit()
        return gate
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _phone_profile_gate_text(gate: dict) -> str:
    structural = gate.get("structural_critical") or []
    if structural:
        lines = [
            "⛔ Підключення телефонного доступу неможливе.",
            "",
            "Спочатку завершіть обов’язкові дані у кабінеті:",
        ]
        lines += [f"• {item.get('text')}" for item in structural]
    else:
        lines = [
            "🟡 Обов’язкові дані заповнені, але ще не підтверджені.",
            "",
            "Відкрийте «Перевірити мої дані» та натисніть:",
            "«✅ Підтвердити обов’язкові дані».",
        ]
    lines += [
        "",
        "Номер для відкриття є окремим непублічним номером доступу; "
        "він може не збігатися з контактними телефонами.",
    ]
    return "\n".join(lines)


def _phone_point_status_lines(points: list[dict]) -> list[str]:
    """Show each selected barrier separately in resident/operator order cards."""
    result: list[str] = []
    icon_by_status = {
        "ACTIVE": "✅",
        "PENDING_ACTIVATION": "⌛",
        "SUSPENDED_FOR_DEBT": "⚠️",
        "DEACTIVATED_FOR_DEBT": "⛔",
        "DEACTIVATED_MANUAL": "⛔",
        "EXTERNAL_SYNC_ERROR": "❌",
    }
    for point in points:
        name = (
            text(point.get("access_point_name_snapshot"))
            or text(point.get("access_point_name_uk"))
            or text(point.get("access_point_code"))
        )
        status = text(point.get("point_status")) or "PENDING_ACTIVATION"
        result.append(f"{icon_by_status.get(status, '•')} {name} — {status}")
    return result

async def show_resident_services(
    update: Update,
    user_states: dict,
    user_id: int,
    *,
    lang: str,
    section: str = "all",
) -> None:
    data = _account_and_unit(user_id)
    if not data:
        await update.message.reply_text(
            tr(lang, "need_unit"),
            reply_markup=kb([[tr(lang, "back_portal")], [HOME]]),
        )
        return
    try:
        _ensure_and_reconcile()
    except Exception as exc:
        await update.message.reply_text(f"⚠️ {exc}")
        return

    if section == "phone":
        try:
            gate = _phone_profile_gate(data)
        except Exception as exc:
            await update.message.reply_text(f"⚠️ {exc}")
            return
        if not gate.get("allowed"):
            await update.message.reply_text(
                _phone_profile_gate_text(gate),
                reply_markup=kb(
                    [
                        [_PROFILE_VERIFY_BUTTON.get(lang, _PROFILE_VERIFY_BUTTON["ru"])],
                        [HOME],
                    ]
                ),
            )
            return

    state = _state(user_states, user_id, create=True)
    state.clear()
    state.update(
        {
            "_module": MODULE,
            "area": "resident",
            "mode": "resident_home",
            "account": data["account"],
            "unit": data["unit"],
            "resident_section": section,
        }
    )
    title = tr(lang, "phone_title") if section == "phone" else tr(lang, "title")
    intro = tr(lang, "phone_intro") if section == "phone" else tr(lang, "intro")
    body = f"{title}\n\n{intro}"
    if section == "phone" and not _resident_phone_offer():
        body += (
            "\n\n⚠️ У каталозі немає активної послуги телефонного доступу. "
            "Зверніться до оператора."
        )
    elif not _current_offers():
        body += "\n\n" + tr(lang, "no_offers")
    await update.message.reply_text(
        body,
        reply_markup=kb(_offer_buttons(state, lang, section=section)),
    )


async def _show_phone_access_points(update: Update, state: dict, lang: str) -> None:
    state["mode"] = "resident_phone_points"
    choices = {
        phone_tr(lang, "far"): [BARRIER_FAR_01],
        phone_tr(lang, "near"): [BARRIER_NEAR_02],
        phone_tr(lang, "both"): [BARRIER_FAR_01, BARRIER_NEAR_02],
    }
    state["phone_point_buttons"] = choices
    await update.message.reply_text(
        phone_tr(lang, "choose_points"),
        reply_markup=kb(
            [
                [phone_tr(lang, "far")],
                [phone_tr(lang, "near")],
                [phone_tr(lang, "both")],
                [BACK],
                [HOME],
            ]
        ),
    )


async def _show_quantity(update: Update, state: dict, lang: str) -> None:
    state["mode"] = "resident_quantity"
    await update.message.reply_text(
        tr(lang, "choose_quantity"),
        reply_markup=kb([["1", "2", "3"], ["4", "5"], [tr(lang, "other_quantity")], [BACK], [HOME]]),
    )


async def _show_preview(update: Update, state: dict, lang: str) -> None:
    offer = state["offer"]
    quantity = int(state.get("quantity") or 1)
    is_phone = _is_phone_offer(offer)
    if is_phone:
        points = state.get("phone_access_points") or []
        if not points:
            await _show_phone_access_points(update, state, lang)
            return
        try:
            quote = phone_barrier_quote(access_point_codes=points)
        except Exception as exc:
            await update.message.reply_text(f"⚠️ {exc}", reply_markup=kb([[BACK], [HOME]]))
            return
        state["phone_quote"] = quote
        total = float(quote["connection_total"])
        currency = quote.get("currency") or "UAH"
    else:
        price = offer.get("unit_price")
        if price is None:
            await update.message.reply_text(
                "⚠️ Для услуги не задана цена.",
                reply_markup=kb([[BACK], [HOME]]),
            )
            return
        total = float(price) * quantity
        currency = offer.get("currency") or "UAH"

    state["mode"] = "resident_preview"
    lines = [
        tr(lang, "phone_preview") if is_phone else tr(lang, "preview"),
        "",
        f"Квартира: {state['unit'].get('apartment_number') or '-'}",
        f"Услуга: {offer.get('service_item_name') or offer.get('service_item_code')}",
    ]
    if is_phone:
        quote = state["phone_quote"]
        lines += [
            f"{phone_tr(lang, 'points')}: {_phone_point_text(quote.get('access_points') or [])}",
            f"{tr(lang, 'phone_number')}: {state.get('requested_phone') or '-'}",
            f"{phone_tr(lang, 'connect_fee')}: {money(total)} {currency}",
            f"{phone_tr(lang, 'monthly_fee')}: {money(quote.get('monthly_total'))} {currency} {phone_tr(lang, 'monthly_suffix')}",
            f"{phone_tr(lang, 'first_month')}: {quote.get('first_charge_period')}",
            phone_tr(lang, 'debt_mode'),
            phone_tr(lang, 'debt_note'),
            phone_tr(lang, 'private_phone_note'),
        ]
    else:
        lines += [
            f"Кількість: {quantity}",
            f"Вартість: {money(total)} {currency}",
        ]
    lines += [
        "",
        (
            "До підтвердженої оплати це лише намір. Після оплати створюється "
            "справжнє замовлення."
        ),
    ]
    await update.message.reply_text(
        "\n".join(lines),
        reply_markup=kb(
            [[tr(lang, "create_interest")], [tr(lang, "cancel"), BACK], [HOME]]
        ),
    )

def _create_interest_from_state(state: dict, user_id: int) -> dict:
    if _is_phone_offer(state.get("offer")):
        phone = _normalise_phone(state.get("requested_phone"))
        if not _valid_phone(phone):
            raise ValueError(tr(state.get("lang") or "ru", "wrong_phone"))
        gate = _phone_profile_gate(
            {"account": state["account"], "unit": state["unit"]}
        )
        if not gate.get("allowed"):
            raise ValueError(_phone_profile_gate_text(gate))
        return create_phone_barrier_access_interest(
            resident_account_id=int(state["account"]["id"]),
            telegram_user_id=user_id,
            apartment_id=int(state["unit"]["id"]),
            apartment_number=text(state["unit"].get("apartment_number")),
            service_item_code=state["offer"]["service_item_code"],
            phone=phone,
            access_point_codes=state.get("phone_access_points") or [],
            parking_debt_check_mode=text(gate.get("parking_debt_check_mode")) or "MANUAL_REVIEW",
            parking_debt_check_note=text(gate.get("parking_debt_check_note")),
        )
    return create_service_interest(
        resident_account_id=int(state["account"]["id"]),
        telegram_user_id=user_id,
        apartment_id=int(state["unit"]["id"]),
        apartment_number=text(state["unit"].get("apartment_number")),
        service_item_code=state["offer"]["service_item_code"],
        quantity=int(state.get("quantity") or 1),
    )

def _notice_for_interest(interest: dict, state: dict, notice_type: str) -> dict:
    service = {
        "service_code": interest.get("service_code") or interest.get("service_item_code"),
        "service_item_code": interest.get("service_item_code"),
        "service_type": "ONE_TIME",
        "service_name": interest.get("service_name_snapshot"),
    }
    return create_payment_notice(
        account=state["account"],
        apartment=state["unit"],
        notice_type=notice_type,
        declared_cashbox_code="O" if notice_type == "CASH_HANDOVER" else None,
        period_code=None,
        service=service,
        amount=float(interest.get("amount_due_snapshot") or 0),
        resident_comment=(
            f"Намір {interest.get('interest_number')}; кількість {interest.get('quantity')}."
            + (
                f" Телефон доступу: {_requested_phone(interest)}."
                if _requested_phone(interest) else ""
            )
            + (
                (
                    " Шлагбауми: "
                    + _phone_point_text(
                        (_phone_access_summary_for_interest(interest.get("id")) or {}).get("points") or []
                    )
                    + "."
                )
                if _phone_access_summary_for_interest(interest.get("id")) else ""
            )
        ),
    )


async def _show_resident_records(update: Update, state: dict, lang: str) -> None:
    try:
        _ensure_and_reconcile()
    except Exception as exc:
        await update.message.reply_text(f"⚠️ {exc}")
        return
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, order_number, service_name_snapshot, quantity,
                   order_status, payment_status
            FROM service_orders
            WHERE resident_account_id = ?
            ORDER BY id DESC LIMIT 60
            """,
            (int(state["account"]["id"]),),
        )
        orders = [dict(row) for row in cur.fetchall()]
        interests = list_resident_service_interests(int(state["account"]["id"]), conn=conn)
    finally:
        conn.close()
    mapping: dict[str, tuple[str, int]] = {}
    buttons: list[list[str]] = []
    lines = [tr(lang, "my_title"), ""]
    for row in interests:
        label = f"📝 {row['interest_number']} | {row.get('service_name_snapshot')} | ×{row.get('quantity')}"
        mapping[label] = ("interest", int(row["id"]))
        buttons.append([label])
        lines.append(f"{row['interest_number']} | {row.get('interest_status')} | ×{row.get('quantity')}")
    for row in orders:
        label = f"📄 {row['order_number']} | {row.get('service_name_snapshot')} | ×{int(float(row.get('quantity') or 1))}"
        mapping[label] = ("order", int(row["id"]))
        buttons.append([label])
        lines.append(f"{row['order_number']} | {STATUS_LABELS.get(text(row.get('order_status')), row.get('order_status'))}")
    if not mapping:
        lines.append(tr(lang, "no_items"))
    state["resident_record_buttons"] = mapping
    state["mode"] = "resident_records"
    await update.message.reply_text("\n".join(lines), reply_markup=kb(buttons + [[BACK], [HOME]]))


async def _show_interest_card(update: Update, state: dict, interest_id: int, lang: str) -> None:
    interest = get_service_interest(interest_id)
    if int(interest.get("resident_account_id") or -1) != int(state["account"]["id"]):
        await update.message.reply_text(tr(lang, "denied"))
        return
    state["mode"] = "resident_interest_card"
    state["interest_id"] = int(interest_id)
    rows: list[list[str]] = []
    if text(interest.get("interest_status")) == INTEREST:
        rows.append([tr(lang, "cash_notice"), tr(lang, "bank_notice")])
    rows += [[tr(lang, "my_requests")], [BACK], [HOME]]
    await update.message.reply_text(_interest_card(interest, lang), reply_markup=kb(rows))


async def _show_resident_order(update: Update, state: dict, order_id: int, lang: str) -> None:
    order = service_order_summary(order_id)
    if int(order.get("resident_account_id") or -1) != int(state["account"]["id"]):
        await update.message.reply_text(tr(lang, "denied"))
        return
    state["mode"] = "resident_order_card"
    state["order_id"] = int(order_id)
    await update.message.reply_text(_order_card(order, title="📄 Моє замовлення"), reply_markup=kb([[tr(lang, "my_requests")], [BACK], [HOME]]))


async def _handle_resident(update: Update, user_states: dict, user_id: int, message_text: str, *, lang: str, state: dict) -> bool:
    if _home(message_text):
        user_states.pop(user_id, None)
        return False
    if _service_back(message_text):
        await show_resident_services(update, user_states, user_id, lang=lang)
        return True
    mode = text(state.get("mode"))
    if mode == "resident_home":
        if message_text == tr(lang, "my_requests"):
            await _show_resident_records(update, state, lang)
            return True
        offer = (state.get("offer_buttons") or {}).get(message_text)
        if offer:
            state["offer"] = offer
            state["lang"] = lang
            if _is_phone_offer(offer):
                state["quantity"] = 1
                state.pop("phone_access_points", None)
                state.pop("phone_quote", None)
                state.pop("requested_phone", None)
                await _show_phone_access_points(update, state, lang)
            elif text(offer.get("workflow_profile_code")) == NEW_REMOTE_PROFILE:
                await _show_quantity(update, state, lang)
            else:
                state["quantity"] = 1
                await _show_preview(update, state, lang)
            return True
        if message_text == tr(lang, "back_portal"):
            user_states.pop(user_id, None)
            return False

        # Keyboard-independent fallback for the phone page. A phone entered
        # before choosing a barrier is retained, then the resident chooses
        # Far No.1, Near No.2 or both.
        if state.get("resident_section") == "phone" and _valid_phone(message_text):
            offer = _resident_phone_offer()
            if not offer:
                await update.message.reply_text(
                    "⚠️ Активна послуга телефонного доступу не знайдена."
                )
                return True
            state["offer"] = offer
            state["lang"] = lang
            state["requested_phone"] = _normalise_phone(message_text)
            state["quantity"] = 1
            await _show_phone_access_points(update, state, lang)
            return True

        await update.message.reply_text(tr(lang, "wrong"))
        return True
    if mode == "resident_phone_points":
        points = (state.get("phone_point_buttons") or {}).get(message_text)
        if not points:
            await update.message.reply_text(tr(lang, "wrong"))
            return True
        state["phone_access_points"] = list(points)
        state["quantity"] = len(points)
        if _valid_phone(state.get("requested_phone") or ""):
            await _show_preview(update, state, lang)
            return True
        state["mode"] = "resident_phone_input"
        await update.message.reply_text(
            tr(lang, "phone_resident_prompt"),
            reply_markup=kb([[BACK], [HOME]]),
        )
        return True
    if mode == "resident_phone_input":
        phone = _normalise_phone(message_text)
        if not _valid_phone(phone):
            await update.message.reply_text(tr(lang, "wrong_phone"))
            return True
        state["requested_phone"] = phone
        state["quantity"] = len(state.get("phone_access_points") or [BARRIER_FAR_01])
        await _show_preview(update, state, lang)
        return True
    if mode == "resident_quantity":
        if message_text in {"1", "2", "3", "4", "5"}:
            state["quantity"] = int(message_text)
            await _show_preview(update, state, lang)
            return True
        if message_text == tr(lang, "other_quantity"):
            state["mode"] = "resident_quantity_input"
            await update.message.reply_text("Введіть ціле число від 1 до 20.", reply_markup=kb([[BACK], [HOME]]))
            return True
        await update.message.reply_text(tr(lang, "wrong"))
        return True
    if mode == "resident_quantity_input":
        try:
            quantity = int(text(message_text))
        except ValueError:
            quantity = 0
        if not 1 <= quantity <= 20:
            await update.message.reply_text("Вкажіть ціле число від 1 до 20.")
            return True
        state["quantity"] = quantity
        await _show_preview(update, state, lang)
        return True
    if mode == "resident_preview":
        if message_text == tr(lang, "create_interest"):
            try:
                interest = _create_interest_from_state(state, user_id)
            except Exception as exc:
                # OSBB_SERVICE_POLICY_UX_V1
                if ServiceAccessDenied is not None and isinstance(exc, ServiceAccessDenied):
                    await update.message.reply_text(
                        result_to_short_text(exc.result) if result_to_short_text else f"⚠️ {exc}"
                    )
                    return True
                await update.message.reply_text(f"⚠️ {exc}")
                return True
            await update.message.reply_text(tr(lang, "interest_saved"))
            await _show_interest_card(update, state, int(interest["id"]), lang)
            return True
        if message_text == tr(lang, "cancel"):
            await show_resident_services(update, user_states, user_id, lang=lang)
            return True
        await update.message.reply_text(tr(lang, "wrong"))
        return True
    if mode == "resident_records":
        selected = (state.get("resident_record_buttons") or {}).get(message_text)
        if selected:
            kind, record_id = selected
            if kind == "interest":
                await _show_interest_card(update, state, record_id, lang)
            else:
                await _show_resident_order(update, state, record_id, lang)
            return True
        await update.message.reply_text(tr(lang, "wrong"))
        return True
    if mode == "resident_interest_card":
        if message_text in {tr(lang, "cash_notice"), tr(lang, "bank_notice")}:
            interest = get_service_interest(int(state["interest_id"]))
            notice_type = "CASH_HANDOVER" if message_text == tr(lang, "cash_notice") else "BANK_TRANSFER"
            try:
                notice = _notice_for_interest(interest, state, notice_type)
                attach_payment_notice_to_interest(int(interest["id"]), notice)
            except Exception as exc:
                await update.message.reply_text(f"⚠️ {exc}")
                return True
            await update.message.reply_text(tr(lang, "notice_saved", number=notice.get("notice_number") or "—"))
            await _show_interest_card(update, state, int(interest["id"]), lang)
            return True
        if message_text == tr(lang, "my_requests"):
            await _show_resident_records(update, state, lang)
            return True
        await update.message.reply_text(tr(lang, "wrong"))
        return True
    if mode == "resident_order_card":
        if message_text == tr(lang, "my_requests"):
            await _show_resident_records(update, state, lang)
            return True
        await update.message.reply_text(tr(lang, "wrong"))
        return True
    await show_resident_services(update, user_states, user_id, lang=lang)
    return True


# ---------------------------------------------------------------------------
# Operator side
# ---------------------------------------------------------------------------

def _visible_categories(user_id: int) -> set[str]:
    return {category for category in ("REMOTE", "ACCESS") if _category_allowed(user_id, category)}


def _operator_orders(
    user_id: int,
    *,
    category_filter: str | None = None,
) -> list[dict]:
    try:
        _ensure_and_reconcile()
    except Exception:
        pass
    categories = _visible_categories(user_id)
    if category_filter:
        categories &= {text(category_filter).upper()}
    if not categories:
        return []
    conn = get_conn()
    try:
        cur = conn.cursor()
        places = ",".join("?" for _ in categories)
        cur.execute(
            f"""
            SELECT o.id, o.order_number, o.apartment_number, o.service_name_snapshot,
                   o.service_item_code, o.quantity, o.order_status, o.payment_status,
                   o.workflow_profile_code, p.service_category
            FROM service_orders o
            JOIN service_workflow_profiles p ON p.profile_code = o.workflow_profile_code
            WHERE o.order_status NOT IN ('COMPLETED', 'CANCELLED')
              AND p.service_category IN ({places})
            ORDER BY o.id ASC LIMIT 100
            """,
            tuple(sorted(categories)),
        )
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def _assert_order_access(user_id: int, order: dict, action: str = "VIEW") -> None:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT service_category FROM service_workflow_profiles WHERE profile_code = ?", (order["workflow_profile_code"],))
        row = cur.fetchone()
        category = text(row[0]) if row else "GENERAL"
    finally:
        conn.close()
    resource = "service_order_steps" if action == "CONFIRM" else "service_orders"
    if not has_permission(user_id, resource, action, scope_type="SERVICE_CATEGORY", scope_value=category):
        raise PermissionError("Нет права на это действие.")


async def show_service_operator_workspace(
    update: Update,
    user_states: dict,
    user_id: int,
    *,
    lang: str,
    category_filter: str | None = None,
) -> None:
    if not has_service_workspace_access(user_id):
        await update.message.reply_text(tr(lang, "denied"))
        return
    try:
        _ensure_and_reconcile()
    except Exception as exc:
        await update.message.reply_text(f"⚠️ {exc}")
        return

    normalized_filter = text(category_filter).upper() or None
    if normalized_filter and normalized_filter not in _visible_categories(user_id):
        await update.message.reply_text(tr(lang, "denied"))
        return

    state = _state(user_states, user_id, create=True)
    state.clear()
    state.update(
        {
            "_module": MODULE,
            "area": "operator",
            "mode": "operator_home",
            "category_filter": normalized_filter,
        }
    )
    if normalized_filter == "ACCESS":
        body = f"{tr(lang, 'phone_operator_title')}\n\n{tr(lang, 'phone_operator_intro')}"
        rows = [[tr(lang, "open_orders")], [tr(lang, "refresh")], [HOME]]
    else:
        body = f"{tr(lang, 'operator_title')}\n\n{tr(lang, 'operator_intro')}"
        rows = [
            [tr(lang, "open_orders")],
            [tr(lang, "supplier_demand"), tr(lang, "supplier_batches")],
            [tr(lang, "refresh")],
            [HOME],
        ]
    await update.message.reply_text(body, reply_markup=kb(rows))


async def _show_operator_orders(update: Update, state: dict, user_id: int, lang: str) -> None:
    rows = _operator_orders(user_id, category_filter=state.get("category_filter"))
    mapping: dict[str, int] = {}
    buttons: list[list[str]] = []
    lines = [tr(lang, "open_orders"), ""]
    for row in rows:
        label = f"📄 {row['order_number']} | кв.{row.get('apartment_number') or '-'} | ×{int(float(row.get('quantity') or 1))} | {row.get('service_name_snapshot')}"
        mapping[label] = int(row["id"])
        buttons.append([label])
        lines.append(f"{row['order_number']} | {STATUS_LABELS.get(text(row.get('order_status')), row.get('order_status'))}")
    if not rows:
        lines.append(tr(lang, "no_orders"))
    state["operator_order_buttons"] = mapping
    state["mode"] = "operator_orders"
    await update.message.reply_text("\n".join(lines), reply_markup=kb(buttons + [["⬅️ К оператору"], [HOME]]))


def _order_actions(order: dict, lang: str) -> list[list[str]]:
    step = _next_waiting_step(order)
    if not step:
        return [["⬅️ К оператору"], [HOME]]
    code = text(step.get("step_code"))
    mapping = {
        "RESIDENT_REMOTE_RECEIVED": "receive_remote",
        "REMOTE_PROGRAMMED": "program_done",
        "RESIDENT_REMOTE_RETURNED": "return_remote",
        "DIGITAL_ACCESS_ACTIVATED": "activate_phone",
        "REMOTE_BATCH_ISSUED": "issue_new",
    }
    key = mapping.get(code)
    labels = {
        "receive_remote": tr(lang, "receive_remote"),
        "program_done": tr(lang, "program_done"),
        "return_remote": tr(lang, "return_remote"),
        "activate_phone": tr(lang, "activate_phone"),
        "issue_new": tr(lang, "issue_new"),
    }
    rows: list[list[str]] = []
    if key:
        rows.append([labels[key]])
    elif code == "SUPPLIER_BATCH_ASSIGNED":
        rows.append([tr(lang, "supplier_demand")])
    elif code == "SUPPLIER_BATCH_RECEIVED":
        rows.append([tr(lang, "supplier_batches")])
    rows += [["⬅️ К оператору"], [HOME]]
    return rows


async def _show_operator_order(update: Update, state: dict, user_id: int, order_id: int, lang: str) -> None:
    order = service_order_summary(order_id)
    try:
        _assert_order_access(user_id, order)
    except Exception:
        await update.message.reply_text(tr(lang, "denied"))
        return
    state["mode"] = "operator_order_card"
    state["order_id"] = int(order_id)
    body = _order_card(order, title="🔑 Заявка оператора")
    if text(order.get("workflow_profile_code")) == NEW_REMOTE_PROFILE:
        link = order_supply_link(int(order_id))
        if link:
            body += f"\n\nПоставка: {link.get('batch_number')} | {link.get('link_status')}"
    await update.message.reply_text(body, reply_markup=kb(_order_actions(order, lang)))


def _receive_resident_remote(order: dict, asset_number: str, user_id: int) -> dict:
    _assert_order_access(user_id, order, "CONFIRM")
    conn = get_conn()
    try:
        asset = create_remote_asset(
            asset_number=text(asset_number), ownership_type="RESIDENT", inventory_status="IN_SERVICE",
            condition_status="UNKNOWN", apartment_id=int(order["apartment_id"]) if order.get("apartment_id") is not None else None,
            apartment_number=text(order.get("apartment_number")), actor_id=user_id,
            note=f"Принят по заявке {order['order_number']}.", conn=conn,
        )
        result = record_remote_movement(
            remote_asset_id=int(asset["id"]), service_order_id=int(order["id"]),
            movement_type="RECEIVED_FROM_RESIDENT", to_state="IN_SERVICE", actor_id=user_id,
            apartment_id=int(order["apartment_id"]) if order.get("apartment_id") is not None else None,
            apartment_number=text(order.get("apartment_number")), note="Пульт жильца принят.",
            confirm_step_code="RESIDENT_REMOTE_RECEIVED", conn=conn,
        )
        cur = conn.cursor()
        cur.execute("UPDATE remote_order_details SET resident_asset_id = ?, updated_at = ? WHERE service_order_id = ?", (int(asset["id"]), __import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S'), int(order["id"])))
        conn.commit()
        return result["order"]
    except Exception:
        conn.rollback(); raise
    finally:
        conn.close()


def _program(order: dict, user_id: int) -> dict:
    from service_orders_core import confirm_order_step
    _assert_order_access(user_id, order, "CONFIRM")
    return confirm_order_step(order_id=int(order["id"]), step_code="REMOTE_PROGRAMMED", actor_id=user_id, note="Перепрошивка выполнена.", source_context="service_orders_workspace")


def _return_own_remote(order: dict, user_id: int) -> dict:
    _assert_order_access(user_id, order, "CONFIRM")
    conn = get_conn()
    try:
        cur = conn.cursor()
        row = cur.execute("SELECT resident_asset_id FROM remote_order_details WHERE service_order_id = ?", (int(order["id"]),)).fetchone()
        if not row or not row[0]:
            raise ValueError("Нет принятого пульта жильца.")
        result = record_remote_movement(
            remote_asset_id=int(row[0]), service_order_id=int(order["id"]),
            movement_type="RETURNED_TO_RESIDENT", to_state="WITH_RESIDENT", actor_id=user_id,
            apartment_id=int(order["apartment_id"]) if order.get("apartment_id") is not None else None,
            apartment_number=text(order.get("apartment_number")), note="Собственный пульт возвращён жильцу.",
            confirm_step_code="RESIDENT_REMOTE_RETURNED", conn=conn,
        )
        conn.commit()
        return result["order"]
    except Exception:
        conn.rollback(); raise
    finally:
        conn.close()


def _activate_phone(order: dict, phone: str, user_id: int) -> dict:
    _assert_order_access(user_id, order, "CONFIRM")
    summary = _phone_access_summary_for_order(order.get("id"))
    if summary:
        return activate_phone_barrier_access_order(
            order_id=int(order["id"]),
            actor_id=user_id,
        )["order"]
    # Compatibility path for completed/legacy generic phone orders created
    # before the two-barrier subscription model was introduced.
    return activate_access_credential(
        order_id=int(order["id"]), credential_value=phone, actor_id=user_id,
        apartment_id=int(order["apartment_id"]) if order.get("apartment_id") is not None else None,
        apartment_number=text(order.get("apartment_number")), note="Телефонный доступ активирован оператором.",
    )["order"]

async def _show_supplier_demand(update: Update, state: dict, user_id: int, lang: str) -> None:
    rows = supplier_demand()
    lines = [tr(lang, "supplier_demand"), ""]
    buttons: list[list[str]] = []
    mapping: dict[str, str] = {}
    for row in rows:
        label = f"🧾 {row['service_name_snapshot']} | {int(row['quantity'] or 0)} шт."
        mapping[label] = row["service_item_code"]
        buttons.append([label])
        lines.append(f"{row['service_name_snapshot']}: {int(row['quantity'] or 0)} шт. ({row['order_count']} заявок)")
    if not rows:
        lines.append(tr(lang, "no_demand"))
    state["demand_buttons"] = mapping
    state["mode"] = "operator_demand"
    await update.message.reply_text("\n".join(lines), reply_markup=kb(buttons + [["⬅️ К оператору"], [HOME]]))


async def _show_batches(update: Update, state: dict, lang: str) -> None:
    rows = list_supplier_batches()
    lines = [tr(lang, "supplier_batches"), ""]
    buttons: list[list[str]] = []
    mapping: dict[str, int] = {}
    for row in rows:
        label = f"📦 {row['batch_number']} | {row['quantity_received']}/{row['quantity_requested']} | {row['batch_status']}"
        mapping[label] = int(row["id"])
        buttons.append([label])
        lines.append(f"{row['batch_number']} | {row['service_name_snapshot']} | отримано {row['quantity_received']}/{row['quantity_requested']}")
    if not rows:
        lines.append("Немає відкритих поставок.")
    state["batch_buttons"] = mapping
    state["mode"] = "operator_batches"
    await update.message.reply_text("\n".join(lines), reply_markup=kb(buttons + [["⬅️ К оператору"], [HOME]]))


async def _show_batch_card(update: Update, state: dict, batch_id: int, lang: str) -> None:
    batch = get_supplier_batch(batch_id)
    state["mode"] = "operator_batch_card"
    state["batch_id"] = int(batch_id)
    lines = [
        "📦 Поставка пультів", "",
        f"№: {batch['batch_number']}",
        f"Послуга: {batch['service_name_snapshot']}",
        f"Замовлено: {batch['quantity_requested']} шт.",
        f"Отримано: {batch['quantity_received']} шт.",
        f"Видано: {batch['quantity_issued']} шт.",
        f"Статус: {batch['batch_status']}", "", "Заявки:",
    ]
    for link in batch.get("links") or []:
        lines.append(f"• {link['order_number']} | кв.{link['apartment_number']} | {link['quantity']} шт. | {link['link_status']}")
    buttons = []
    if int(batch.get("quantity_received") or 0) < int(batch.get("quantity_requested") or 0):
        buttons.append([tr(lang, "receive_delivery")])
    buttons += [["⬅️ К оператору"], [HOME]]
    await update.message.reply_text("\n".join(lines), reply_markup=kb(buttons))


async def _handle_operator(update: Update, user_states: dict, user_id: int, message_text: str, *, lang: str, state: dict) -> bool:
    if _home(message_text):
        user_states.pop(user_id, None)
        return False
    if not has_service_workspace_access(user_id):
        await update.message.reply_text(tr(lang, "denied")); return True
    if _operator_back(message_text):
        await show_service_operator_workspace(
            update,
            user_states,
            user_id,
            lang=lang,
            category_filter=state.get("category_filter"),
        )
        return True
    mode = text(state.get("mode"))
    if mode == "operator_home":
        if message_text in {tr(lang, "open_orders"), tr(lang, "refresh")}:
            await _show_operator_orders(update, state, user_id, lang); return True
        if message_text == tr(lang, "supplier_demand"):
            await _show_supplier_demand(update, state, user_id, lang); return True
        if message_text == tr(lang, "supplier_batches"):
            await _show_batches(update, state, lang); return True
        await update.message.reply_text(tr(lang, "wrong")); return True
    if mode == "operator_orders":
        order_id = (state.get("operator_order_buttons") or {}).get(message_text)
        if order_id:
            await _show_operator_order(update, state, user_id, int(order_id), lang); return True
        await update.message.reply_text(tr(lang, "wrong")); return True
    if mode == "operator_order_card":
        order = service_order_summary(int(state["order_id"]))
        action_map = {
            tr(lang, "receive_remote"): "receive_remote",
            tr(lang, "program_done"): "program_done",
            tr(lang, "return_remote"): "return_remote",
            tr(lang, "activate_phone"): "activate_phone",
            tr(lang, "issue_new"): "issue_new",
            tr(lang, "supplier_demand"): "supplier_demand",
            tr(lang, "supplier_batches"): "supplier_batches",
        }
        action = action_map.get(message_text)
        if not action:
            await update.message.reply_text(tr(lang, "wrong")); return True
        if action == "supplier_demand":
            await _show_supplier_demand(update, state, user_id, lang); return True
        if action == "supplier_batches":
            await _show_batches(update, state, lang); return True
        try:
            _assert_order_access(user_id, order, "CONFIRM")
        except Exception:
            await update.message.reply_text(tr(lang, "denied")); return True
        if action == "receive_remote":
            state["mode"] = "operator_remote_label"
            await update.message.reply_text(tr(lang, "asset_prompt"), reply_markup=kb([["⬅️ К оператору"], [HOME]])); return True
        if action == "activate_phone":
            phone = _requested_phone(order)
            if phone:
                try:
                    _activate_phone(order, phone, user_id)
                except Exception as exc:
                    await update.message.reply_text(f"⚠️ {exc}")
                    return True
                await update.message.reply_text(tr(lang, "phone_activated", phone=phone))
                await _show_operator_order(
                    update,
                    state,
                    user_id,
                    int(order["id"]),
                    lang,
                )
                return True

            # Compatibility path for legacy manually-created orders that do
            # not contain a resident-requested number.
            state["mode"] = "operator_phone_input"
            await update.message.reply_text(
                tr(lang, "phone_prompt"),
                reply_markup=kb([["⬅️ К оператору"], [HOME]]),
            )
            return True
        try:
            if action == "program_done":
                _program(order, user_id)
            elif action == "return_remote":
                _return_own_remote(order, user_id)
            elif action == "issue_new":
                issue_new_remotes_from_batch(service_order_id=int(order["id"]), actor_id=user_id)
        except Exception as exc:
            # OSBB_SERVICE_POLICY_UX_V1
            if ServiceAccessDenied is not None and isinstance(exc, ServiceAccessDenied):
                await update.message.reply_text(
                    result_to_short_text(exc.result) if result_to_short_text else f"⚠️ {exc}"
                )
                return True
            await update.message.reply_text(f"⚠️ {exc}"); return True
        await _show_operator_order(update, state, user_id, int(order["id"]), lang); return True
    if mode == "operator_remote_label":
        label = text(message_text)
        if len(label) < 3:
            await update.message.reply_text("Маркировка должна содержать минимум 3 символа."); return True
        order = service_order_summary(int(state["order_id"]))
        try:
            _receive_resident_remote(order, label, user_id)
        except Exception as exc:
            await update.message.reply_text(f"⚠️ {exc}"); return True
        await _show_operator_order(update, state, user_id, int(order["id"]), lang); return True
    if mode == "operator_phone_input":
        phone = re.sub(r"[\s()\-]", "", text(message_text))
        if not re.fullmatch(r"\+?\d{8,20}", phone):
            await update.message.reply_text(tr(lang, "wrong_phone")); return True
        order = service_order_summary(int(state["order_id"]))
        try:
            _activate_phone(order, phone, user_id)
        except Exception as exc:
            # OSBB_PHONE_ACCESS_POLICY_UX_BRIDGE_V1
            if ServiceAccessDenied is not None and isinstance(exc, ServiceAccessDenied):
                await update.message.reply_text(
                    result_to_short_text(exc.result) if result_to_short_text else f"⚠️ {exc}"
                )
                return True
            await update.message.reply_text(f"⚠️ {exc}"); return True
        await _show_operator_order(update, state, user_id, int(order["id"]), lang); return True
    if mode == "operator_demand":
        service_item_code = (state.get("demand_buttons") or {}).get(message_text)
        if not service_item_code:
            await update.message.reply_text(tr(lang, "wrong")); return True
        try:
            batch = create_supplier_batch(service_item_code=service_item_code, actor_id=user_id)
        except Exception as exc:
            await update.message.reply_text(f"⚠️ {exc}"); return True
        await update.message.reply_text(f"✅ Створено {batch['batch_number']}: {batch['quantity_requested']} шт.")
        await _show_batch_card(update, state, int(batch["id"]), lang); return True
    if mode == "operator_batches":
        batch_id = (state.get("batch_buttons") or {}).get(message_text)
        if not batch_id:
            await update.message.reply_text(tr(lang, "wrong")); return True
        await _show_batch_card(update, state, int(batch_id), lang); return True
    if mode == "operator_batch_card":
        if message_text == tr(lang, "receive_delivery"):
            state["mode"] = "operator_delivery_qty"
            await update.message.reply_text(tr(lang, "delivery_qty_prompt"), reply_markup=kb([["⬅️ К оператору"], [HOME]])); return True
        await update.message.reply_text(tr(lang, "wrong")); return True
    if mode == "operator_delivery_qty":
        try:
            quantity = int(text(message_text))
        except ValueError:
            quantity = 0
        if quantity <= 0:
            await update.message.reply_text("Вкажіть додатне ціле число."); return True
        try:
            receive_supplier_batch(batch_id=int(state["batch_id"]), received_now=quantity, actor_id=user_id)
        except Exception as exc:
            await update.message.reply_text(f"⚠️ {exc}"); return True
        await _show_batch_card(update, state, int(state["batch_id"]), lang); return True
    await show_service_operator_workspace(update, user_states, user_id, lang=lang)
    return True


async def handle_service_orders_text(update: Update, user_states: dict, user_id: int, message_text: str, *, lang: str, user_mode: str | None) -> bool:
    message_text = text(message_text)
    lang = lang if lang in I18N else "ru"
    try:
        _ensure_and_reconcile()
    except Exception:
        # Do not show a technical schema error before user deliberately enters
        # the services screen; routing remains available for the usual menus.
        pass
    if message_text in _operator_entry_texts():
        await show_service_operator_workspace(update, user_states, user_id, lang=lang)
        return True
    state = _state(user_states, user_id, create=False)
    if state:
        if text(state.get("area")) == "resident":
            return await _handle_resident(update, user_states, user_id, message_text, lang=lang, state=state)
        if text(state.get("area")) == "operator":
            return await _handle_operator(update, user_states, user_id, message_text, lang=lang, state=state)
    if user_mode == "client" and message_text in _resident_entry_texts():
        section = "phone" if message_text in _phone_entry_texts() else "all"
        await show_resident_services(
            update,
            user_states,
            user_id,
            lang=lang,
            section=section,
        )
        return True
    if user_mode == "admin" and message_text == "🔑 Заявки на пульты" and has_service_workspace_access(user_id):
        await show_service_operator_workspace(
            update,
            user_states,
            user_id,
            lang=lang,
            category_filter="REMOTE",
        )
        return True
    if user_mode == "admin" and message_text == "📞 Телефонный доступ" and has_service_workspace_access(user_id):
        await show_service_operator_workspace(
            update,
            user_states,
            user_id,
            lang=lang,
            category_filter="ACCESS",
        )
        return True
    return False
