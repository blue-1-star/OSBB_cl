"""
Клиентский кабинет ОСББ — версия для запуска.

Что реально работает:
- строгие меню на выбранном языке: RU / UA / EN;
- личный кабинет и привязанная квартира;
- список автомобилей квартиры;
- парковочный счёт: начисления, оплаты, остаток — только чтение;
- обращения на пульты: первая выдача / дополнительный / замена;
- операторский список заявок на пульты: NEW → IN_REVIEW → ISSUED / REJECTED.

Что пока честно обозначено как подготовка:
- автоматическое добавление/удаление телефона в GEOS RC-4000;
- онлайн-оплата;
- автоматическое сообщение о выдаче пульта;
- остальные публичные разделы.

Этот файл не отправляет SMS и не меняет GSM-контроллер.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import re
import sqlite3
import sys
from typing import Any

from telegram import Update, ReplyKeyboardMarkup

HANDLERS_DIR = Path(__file__).resolve().parent
BOTS_DIR = HANDLERS_DIR.parent
OSBB_ROOT = BOTS_DIR.parent
PY_ROOT = OSBB_ROOT.parent

for folder in (OSBB_ROOT, PY_ROOT):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))

from config import paths, USE_TEST_DB

try:
    from audit_logger import audit_log
except Exception:
    audit_log = None


# ---------------------------------------------------------------------------
# Language pack. Each submenu uses keys from this pack, not hard-coded RU.
# ---------------------------------------------------------------------------

I18N = {
    "ru": {
        "welcome": "Добро пожаловать в личный кабинет ОСББ.",
        "choose_menu": "Пожалуйста, выберите действие кнопкой на русском языке.",
        "home": "🏠 Главное меню",
        "back_portal": "⬅️ К кабинету",
        "my_home": "🏠 Моя квартира",
        "change_home": "✏️ Изменить квартиру",
        "my_vehicles": "🚗 Мои автомобили",
        "parking": "🚗 Парковка",
        "remotes": "🔑 Пульты",
        "phone": "📞 Открытие по телефону",
        "improve": "🏗 Благоустройство",
        "news": "📢 Объявления",
        "contacts": "📞 Контакты",
        "admin": "🔐 Админ-режим",
        "parking_balance": "💳 Состояние счёта",
        "parking_charges": "📅 Начисления",
        "parking_payments": "💰 Оплаты",
        "parking_how": "ℹ️ Как оплатить",
        "remote_my": "📋 Мои обращения",
        "remote_new": "➕ Запросить пульт",
        "remote_how": "ℹ️ Порядок получения",
        "remote_first": "🆕 Первый пульт",
        "remote_additional": "➕ Дополнительный пульт",
        "remote_replace": "🔁 Замена пульта",
        "remote_in_work": "✅ В работу",
        "remote_issued": "🎁 Выдан",
        "remote_rejected": "❌ Отклонить",
        "remote_list": "🔑 Заявки на пульты",
        "back_requests": "⬅️ К заявкам",
        "confirm": "✅ Подтвердить",
        "cancel": "❌ Отмена",
        "yes": "✅ Да, это моя квартира",
        "other_home": "✏️ Ввести другую квартиру",
        "link_prompt": "Введите номер квартиры.",
        "link_not_found": "Квартира не найдена. Проверьте номер и введите ещё раз.",
        "link_group": "Этот номер входит в составную группу. Для привязки обратитесь к оператору.",
        "link_confirm": "Квартира {unit}\n\nОтправить оператору запрос на привязку к этой квартире?\n\nДо проверки данные квартиры и автомобилей не показываются.",
        "linked": "✅ Запрос #{id} на привязку к квартире {unit} принят. Оператор проверит его отдельно.",
        "no_unit": "Квартира пока не привязана. Выберите «Изменить квартиру».",
        "cabinet": "🏠 Личный кабинет",
        "home_label": "Квартира",
        "entrance": "Подъезд",
        "account_status": "Статус кабинета",
        "verified": "✅ проверен оператором",
        "pending": "⏳ ожидает проверки",
        "parking_title": "🚗 Парковка — кв. {unit}",
        "charges_title": "📅 Начисления парковки — кв. {unit}",
        "payments_title": "💰 Оплаты парковки — кв. {unit}",
        "charged": "Начислено",
        "allocated": "Учтено по начислениям",
        "due": "К оплате по начислениям",
        "received": "Оплат поступило",
        "unallocated": "Нераспределённые оплаты",
        "no_charges": "Начислений пока нет.",
        "no_payments": (
            "Оплаты с привязкой к квартире пока не найдены.\n\n"
            "Это не обязательно означает отсутствие оплаты: "
            "старые операции могут ещё ожидать распределения оператором."
        ),
        "billing_error": "Данные по парковке пока готовятся.",
        "periods": "Периоды в кабинете",
        "latest_period": "Последний период в системе",
        "link_request_missing": "Безопасная привязка квартиры ещё подключается. Обратитесь к оператору.",
        "link_admin": "🔗 Запросы квартир",
        "link_admin_new": "🟡 Новые",
        "link_admin_all": "📋 Все",
        "link_admin_title": "🔗 Запросы на привязку квартир",
        "link_admin_empty": "Новых запросов на привязку нет.",
        "link_admin_card": "🔗 Запрос #{id}",
        "link_approve": "✅ Подтвердить квартиру",
        "link_reject": "❌ Отклонить запрос",
        "link_operator_note_prompt": "Введите заметку оператора или «-».",
        "link_admin_updated": "✅ Запрос на привязку обработан.",
        "payment_help": (
            "ℹ️ Как оплатить парковку\n\n"
            "Реквизиты и каналы оплаты публикуются оператором. "
            "Если вы уже оплатили, сохраните подтверждение оплаты."
        ),
        "phones_stub": (
            "📞 Открытие шлагбаума по телефону\n\n"
            "Реестр телефонного доступа наполняется оператором. "
            "Автоматическое изменение GEOS RC-4000 пока не включено."
        ),
        "improve_stub": "🏗 Благоустройство\n\nРаздел готовится.",
        "news_stub": "📢 Объявления\n\nПубличная лента объявлений наполняется.",
        "contacts_stub": "📞 Контакты\n\nКонтакты ОСББ будут опубликованы здесь.",
        "remotes_title": "🔑 Пульты шлагбаума",
        "remotes_intro": (
            "Здесь можно оставить обращение на пульт. "
            "Выдача не происходит автоматически: заявку обрабатывает оператор."
        ),
        "remote_how_text": (
            "ℹ️ Порядок получения пульта\n\n"
            "1. Оставьте обращение в боте.\n"
            "2. Оператор проверит квартиру и обращение.\n"
            "3. Вам сообщат порядок выдачи.\n\n"
            "Пульт не выдаётся автоматически после нажатия кнопки."
        ),
        "remote_kind_prompt": "Выберите тип обращения.",
        "remote_quantity_prompt": "Введите количество пультов: от 1 до 10.",
        "remote_comment_prompt": (
            "Введите комментарий к обращению.\n\n"
            "Например: «для арендатора», «утерян старый».\n"
            "Введите «-», если комментария нет."
        ),
        "remote_saved": "✅ Обращение #{id} принято.\n\nСтатус: новое.\nОператор рассмотрит его отдельно.",
        "remote_no_requests": "Обращений по пультам пока нет.",
        "remote_my_title": "📋 Мои обращения по пультам",
        "remote_status_NEW": "🟡 Новое",
        "remote_status_IN_REVIEW": "🔵 В работе",
        "remote_status_ISSUED": "✅ Выдан",
        "remote_status_REJECTED": "❌ Отклонено",
        "remote_status_CANCELLED": "⚪ Отменено",
        "remote_admin_empty": "Новых обращений нет.",
        "remote_admin_title": "🔑 Заявки на пульты — оператор",
        "remote_admin_card": "🔑 Обращение #{id}",
        "remote_admin_note_prompt": "Введите заметку оператора или «-».",
        "remote_admin_updated": "✅ Статус обращения обновлён.",
        "remote_only_admin": "Нет доступа к заявкам операторов.",
        "remote_comment": "Комментарий",
        "remote_operator_note": "Заметка оператора",
        "remote_kind_FIRST": "Первый пульт",
        "remote_kind_ADDITIONAL": "Дополнительный пульт",
        "remote_kind_REPLACEMENT": "Замена пульта",
        "vehicle_none": "Автомобили пока не найдены.",
        "vehicle_title": "🚗 Автомобили квартиры {unit}",
        "parking_day": "Day",
        "parking_night": "Night",
        "parking_unknown": "не указан",
        "wrong_remote_qty": "Введите целое число от 1 до 10.",
        "remote_missing_table": "Раздел заявок на пульты ещё подключается. Обратитесь к оператору.",
        "remote_admin_new": "🟡 Новые",
        "remote_admin_all": "📋 Все",
    },
    "uk": {
        "welcome": "Ласкаво просимо до особистого кабінету ОСББ.",
        "choose_menu": "Будь ласка, оберіть дію кнопкою українською мовою.",
        "home": "🏠 Головне меню",
        "back_portal": "⬅️ До кабінету",
        "my_home": "🏠 Моя квартира",
        "change_home": "✏️ Змінити квартиру",
        "my_vehicles": "🚗 Мої автомобілі",
        "parking": "🚗 Паркування",
        "remotes": "🔑 Пульти",
        "phone": "📞 Відкриття телефоном",
        "improve": "🏗 Благоустрій",
        "news": "📢 Оголошення",
        "contacts": "📞 Контакти",
        "admin": "🔐 Адмін-режим",
        "parking_balance": "💳 Стан рахунку",
        "parking_charges": "📅 Нарахування",
        "parking_payments": "💰 Оплати",
        "parking_how": "ℹ️ Як сплатити",
        "remote_my": "📋 Мої звернення",
        "remote_new": "➕ Запитати пульт",
        "remote_how": "ℹ️ Порядок отримання",
        "remote_first": "🆕 Перший пульт",
        "remote_additional": "➕ Додатковий пульт",
        "remote_replace": "🔁 Заміна пульта",
        "remote_in_work": "✅ В роботу",
        "remote_issued": "🎁 Видано",
        "remote_rejected": "❌ Відхилити",
        "remote_list": "🔑 Заявки на пульти",
        "back_requests": "⬅️ До заявок",
        "confirm": "✅ Підтвердити",
        "cancel": "❌ Скасувати",
        "yes": "✅ Так, це моя квартира",
        "other_home": "✏️ Ввести іншу квартиру",
        "link_prompt": "Введіть номер квартири.",
        "link_not_found": "Квартиру не знайдено. Перевірте номер і введіть ще раз.",
        "link_group": "Цей номер входить до складеної групи. Для прив’язки зверніться до оператора.",
        "link_confirm": "Квартира {unit}\n\nНадіслати оператору запит на прив’язку до цієї квартири?\n\nДо перевірки дані квартири та автомобілів не показуються.",
        "linked": "✅ Запит #{id} на прив’язку до квартири {unit} прийнято. Оператор перевірить його окремо.",
        "no_unit": "Квартиру ще не прив’язано. Оберіть «Змінити квартиру».",
        "cabinet": "🏠 Особистий кабінет",
        "home_label": "Квартира",
        "entrance": "Під’їзд",
        "account_status": "Статус кабінету",
        "verified": "✅ перевірено оператором",
        "pending": "⏳ очікує перевірки",
        "parking_title": "🚗 Паркування — кв. {unit}",
        "charges_title": "📅 Нарахування паркування — кв. {unit}",
        "payments_title": "💰 Оплати паркування — кв. {unit}",
        "charged": "Нараховано",
        "allocated": "Зараховано до нарахувань",
        "due": "До сплати за нарахуваннями",
        "received": "Оплат надійшло",
        "unallocated": "Нерозподілені оплати",
        "no_charges": "Нарахувань поки немає.",
        "no_payments": (
            "Оплат із прив’язкою до квартири поки не знайдено.\n\n"
            "Це не обов’язково означає відсутність оплати: "
            "старі операції можуть ще чекати розподілу оператором."
        ),
        "billing_error": "Дані щодо паркування ще готуються.",
        "periods": "Періоди в кабінеті",
        "latest_period": "Останній період у системі",
        "link_request_missing": "Безпечна прив’язка квартири ще підключається. Зверніться до оператора.",
        "link_admin": "🔗 Запити квартир",
        "link_admin_new": "🟡 Нові",
        "link_admin_all": "📋 Усі",
        "link_admin_title": "🔗 Запити на прив’язку квартир",
        "link_admin_empty": "Нових запитів на прив’язку немає.",
        "link_admin_card": "🔗 Запит #{id}",
        "link_approve": "✅ Підтвердити квартиру",
        "link_reject": "❌ Відхилити запит",
        "link_operator_note_prompt": "Введіть нотатку оператора або «-».",
        "link_admin_updated": "✅ Запит на прив’язку опрацьовано.",
        "payment_help": (
            "ℹ️ Як сплатити за паркування\n\n"
            "Реквізити та канали оплати публікує оператор. "
            "Якщо ви вже сплатили, збережіть підтвердження оплати."
        ),
        "phones_stub": (
            "📞 Відкриття шлагбаума телефоном\n\n"
            "Реєстр телефонного доступу наповнює оператор. "
            "Автоматичну зміну GEOS RC-4000 поки не увімкнено."
        ),
        "improve_stub": "🏗 Благоустрій\n\nРозділ готується.",
        "news_stub": "📢 Оголошення\n\nПублічна стрічка оголошень наповнюється.",
        "contacts_stub": "📞 Контакти\n\nКонтакти ОСББ будуть опубліковані тут.",
        "remotes_title": "🔑 Пульти шлагбаума",
        "remotes_intro": (
            "Тут можна залишити звернення на пульт. "
            "Видача не відбувається автоматично: заявку опрацьовує оператор."
        ),
        "remote_how_text": (
            "ℹ️ Порядок отримання пульта\n\n"
            "1. Залиште звернення у боті.\n"
            "2. Оператор перевірить квартиру та звернення.\n"
            "3. Вам повідомлять порядок видачі.\n\n"
            "Пульт не видається автоматично після натискання кнопки."
        ),
        "remote_kind_prompt": "Оберіть тип звернення.",
        "remote_quantity_prompt": "Введіть кількість пультів: від 1 до 10.",
        "remote_comment_prompt": (
            "Введіть коментар до звернення.\n\n"
            "Наприклад: «для орендаря», «втрачено старий».\n"
            "Введіть «-», якщо коментаря немає."
        ),
        "remote_saved": "✅ Звернення #{id} прийнято.\n\nСтатус: нове.\nОператор розгляне його окремо.",
        "remote_no_requests": "Звернень щодо пультів поки немає.",
        "remote_my_title": "📋 Мої звернення щодо пультів",
        "remote_status_NEW": "🟡 Нове",
        "remote_status_IN_REVIEW": "🔵 В роботі",
        "remote_status_ISSUED": "✅ Видано",
        "remote_status_REJECTED": "❌ Відхилено",
        "remote_status_CANCELLED": "⚪ Скасовано",
        "remote_admin_empty": "Нових звернень немає.",
        "remote_admin_title": "🔑 Заявки на пульти — оператор",
        "remote_admin_card": "🔑 Звернення #{id}",
        "remote_admin_note_prompt": "Введіть нотатку оператора або «-».",
        "remote_admin_updated": "✅ Статус звернення оновлено.",
        "remote_only_admin": "Немає доступу до заявок операторів.",
        "remote_comment": "Коментар",
        "remote_operator_note": "Нотатка оператора",
        "remote_kind_FIRST": "Перший пульт",
        "remote_kind_ADDITIONAL": "Додатковий пульт",
        "remote_kind_REPLACEMENT": "Заміна пульта",
        "vehicle_none": "Автомобілі поки не знайдено.",
        "vehicle_title": "🚗 Автомобілі квартири {unit}",
        "parking_day": "Day",
        "parking_night": "Night",
        "parking_unknown": "не вказано",
        "wrong_remote_qty": "Введіть ціле число від 1 до 10.",
        "remote_missing_table": "Розділ заявок на пульти ще підключається. Зверніться до оператора.",
        "remote_admin_new": "🟡 Нові",
        "remote_admin_all": "📋 Усі",
    },
    "en": {
        "welcome": "Welcome to the OSBB resident portal.",
        "choose_menu": "Please use the buttons in the selected language.",
        "home": "🏠 Main menu",
        "back_portal": "⬅️ Back to portal",
        "my_home": "🏠 My apartment",
        "change_home": "✏️ Change apartment",
        "my_vehicles": "🚗 My vehicles",
        "parking": "🚗 Parking",
        "remotes": "🔑 Remotes",
        "phone": "📞 Phone gate access",
        "improve": "🏗 Improvements",
        "news": "📢 Announcements",
        "contacts": "📞 Contacts",
        "admin": "🔐 Admin mode",
        "parking_balance": "💳 Account status",
        "parking_charges": "📅 Charges",
        "parking_payments": "💰 Payments",
        "parking_how": "ℹ️ How to pay",
        "remote_my": "📋 My requests",
        "remote_new": "➕ Request a remote",
        "remote_how": "ℹ️ How collection works",
        "remote_first": "🆕 First remote",
        "remote_additional": "➕ Additional remote",
        "remote_replace": "🔁 Replacement remote",
        "remote_in_work": "✅ Start review",
        "remote_issued": "🎁 Issued",
        "remote_rejected": "❌ Reject",
        "remote_list": "🔑 Remote requests",
        "back_requests": "⬅️ Back to requests",
        "confirm": "✅ Confirm",
        "cancel": "❌ Cancel",
        "yes": "✅ Yes, this is my apartment",
        "other_home": "✏️ Enter another apartment",
        "link_prompt": "Enter your apartment number.",
        "link_not_found": "Apartment not found. Check the number and try again.",
        "link_group": "This number belongs to a combined group. Please contact the operator for linking.",
        "link_confirm": "Apartment {unit}\n\nSend the operator a request to link this apartment?\n\nApartment and vehicle details are not shown before verification.",
        "linked": "✅ Request #{id} to link apartment {unit} has been received. The operator will verify it separately.",
        "no_unit": "No apartment is linked yet. Choose “Change apartment”.",
        "cabinet": "🏠 Resident portal",
        "home_label": "Apartment",
        "entrance": "Entrance",
        "account_status": "Portal status",
        "verified": "✅ operator verified",
        "pending": "⏳ awaiting verification",
        "parking_title": "🚗 Parking — apartment {unit}",
        "charges_title": "📅 Parking charges — apartment {unit}",
        "payments_title": "💰 Parking payments — apartment {unit}",
        "charged": "Charged",
        "allocated": "Allocated to charges",
        "due": "Outstanding on charges",
        "received": "Payments received",
        "unallocated": "Unallocated payments",
        "no_charges": "There are no charges yet.",
        "no_payments": (
            "No payments linked to this apartment were found.\n\n"
            "This does not necessarily mean no payment was made: "
            "older operations may still await operator allocation."
        ),
        "billing_error": "Parking data is still being prepared.",
        "periods": "Periods shown",
        "latest_period": "Latest period in the system",
        "link_request_missing": "Secure apartment linking is still being connected. Please contact the operator.",
        "link_admin": "🔗 Apartment link requests",
        "link_admin_new": "🟡 New",
        "link_admin_all": "📋 All",
        "link_admin_title": "🔗 Apartment-link requests",
        "link_admin_empty": "There are no new apartment-link requests.",
        "link_admin_card": "🔗 Request #{id}",
        "link_approve": "✅ Approve apartment",
        "link_reject": "❌ Reject request",
        "link_operator_note_prompt": "Enter the operator note or “-”.",
        "link_admin_updated": "✅ Apartment-link request updated.",
        "payment_help": (
            "ℹ️ How to pay for parking\n\n"
            "Payment details and channels are published by the operator. "
            "Keep your proof of payment if you have already paid."
        ),
        "phones_stub": (
            "📞 Phone gate access\n\n"
            "The phone-access register is being completed by the operator. "
            "Automatic GEOS RC-4000 changes are not enabled yet."
        ),
        "improve_stub": "🏗 Improvements\n\nThis section is being prepared.",
        "news_stub": "📢 Announcements\n\nThe public announcement feed is being prepared.",
        "contacts_stub": "📞 Contacts\n\nOSBB contacts will be published here.",
        "remotes_title": "🔑 Gate remotes",
        "remotes_intro": (
            "You can submit a remote request here. "
            "A remote is not issued automatically: the operator reviews every request."
        ),
        "remote_how_text": (
            "ℹ️ How remote collection works\n\n"
            "1. Submit a request in the bot.\n"
            "2. The operator checks the apartment and request.\n"
            "3. You receive collection instructions.\n\n"
            "A remote is not issued automatically after pressing a button."
        ),
        "remote_kind_prompt": "Choose your request type.",
        "remote_quantity_prompt": "Enter the number of remotes: 1 to 10.",
        "remote_comment_prompt": (
            "Enter a comment.\n\n"
            "For example: “for tenant”, “old remote lost”.\n"
            "Enter “-” if there is no comment."
        ),
        "remote_saved": "✅ Request #{id} received.\n\nStatus: new.\nThe operator will review it separately.",
        "remote_no_requests": "There are no remote requests yet.",
        "remote_my_title": "📋 My remote requests",
        "remote_status_NEW": "🟡 New",
        "remote_status_IN_REVIEW": "🔵 Under review",
        "remote_status_ISSUED": "✅ Issued",
        "remote_status_REJECTED": "❌ Rejected",
        "remote_status_CANCELLED": "⚪ Cancelled",
        "remote_admin_empty": "There are no new requests.",
        "remote_admin_title": "🔑 Remote requests — operator",
        "remote_admin_card": "🔑 Request #{id}",
        "remote_admin_note_prompt": "Enter the operator note or “-”.",
        "remote_admin_updated": "✅ Request status updated.",
        "remote_only_admin": "You do not have access to operator requests.",
        "remote_comment": "Comment",
        "remote_operator_note": "Operator note",
        "remote_kind_FIRST": "First remote",
        "remote_kind_ADDITIONAL": "Additional remote",
        "remote_kind_REPLACEMENT": "Replacement remote",
        "vehicle_none": "No vehicles were found.",
        "vehicle_title": "🚗 Vehicles for apartment {unit}",
        "parking_day": "Day",
        "parking_night": "Night",
        "parking_unknown": "not specified",
        "wrong_remote_qty": "Enter an integer from 1 to 10.",
        "remote_missing_table": "The remote-request section is still being connected. Please contact the operator.",
        "remote_admin_new": "🟡 New",
        "remote_admin_all": "📋 All",
    },
}


def tr(lang: str, key: str, **kwargs: Any) -> str:
    lang = lang if lang in I18N else "ru"
    return I18N[lang][key].format(**kwargs)


def kb(rows: list[list[str]]) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def client_menu_keyboard(lang: str) -> list[list[str]]:
    return [
        [tr(lang, "my_home")],
        [tr(lang, "change_home")],
        [tr(lang, "my_vehicles")],
        [tr(lang, "parking"), tr(lang, "remotes")],
        [tr(lang, "phone")],
        [tr(lang, "improve")],
        [tr(lang, "news"), tr(lang, "contacts")],
        [tr(lang, "admin")],
    ]


# def client_welcome_text(lang: str) -> str:
#     return tr(lang, "welcome")

def client_welcome_text(lang: str, user_id: int = None) -> str:
    """
    Возвращает приветственное сообщение с именем и квартирой (если есть)
    
    Args:
        lang: язык ('ru', 'uk', 'en')
        user_id: Telegram ID пользователя (для получения данных)
    """
    
    # Базовое приветствие на нужном языке
    if lang == "ru":
        welcome = "👋 Добро пожаловать в систему управления ОСББ!"
        name_label = "Имя"
        apartment_label = "Квартира"
        no_apartment = "не привязана"
    elif lang == "uk":
        welcome = "👋 Ласкаво просимо до системи ОСББ!"
        name_label = "Ім'я"
        apartment_label = "Квартира"
        no_apartment = "не прив'язана"
    else:
        welcome = "👋 Welcome to the OSBB system!"
        name_label = "Name"
        apartment_label = "Apartment"
        no_apartment = "not linked"
    
    # Если user_id не передан — возвращаем только приветствие
    if user_id is None:
        return welcome
    
    # Пытаемся получить данные через новую модель
    try:
        from core_new.domain.residents import Resident
        resident = Resident.get_by_telegram_id(user_id)
        
        if resident:
            # Формируем персонализированное приветствие
            name = resident.display_name
            apartment = resident.apartment_number or no_apartment
            
            # Проверяем, есть ли у пользователя квартира
            if resident.has_apartment:
                return f"{welcome}\n\n👤 {name_label}: {name}\n🏠 {apartment_label}: {apartment}"
            else:
                return f"{welcome}\n\n👤 {name_label}: {name}\n🏠 {apartment_label}: {no_apartment}"
        else:
            # Если житель не найден — возвращаем стандартное приветствие
            return welcome
            
    except Exception as e:
        # В случае ошибки — возвращаем стандартное приветствие
        print(f"⚠️ Ошибка получения данных для приветствия: {e}")
        return welcome




def parking_menu_keyboard(lang: str) -> list[list[str]]:
    return [
        [tr(lang, "parking_balance")],
        [tr(lang, "parking_charges"), tr(lang, "parking_payments")],
        [tr(lang, "parking_how")],
        [tr(lang, "back_portal"), tr(lang, "home")],
    ]


def remotes_menu_keyboard(lang: str) -> list[list[str]]:
    return [
        [tr(lang, "remote_my")],
        [tr(lang, "remote_new")],
        [tr(lang, "remote_how")],
        [tr(lang, "back_portal"), tr(lang, "home")],
    ]


def admin_remote_menu_keyboard(lang: str) -> list[list[str]]:
    return [
        [tr(lang, "remote_admin_new"), tr(lang, "remote_admin_all")],
        [tr(lang, "home")],
    ]


def now_db() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_db_file() -> Path:
    return paths.OSBB_TEST_DB_FILE if USE_TEST_DB else paths.OSBB_DB_FILE


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(get_db_file())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def money(value: Any) -> str:
    value = float(value or 0)
    return (
        f"{int(value):,}".replace(",", " ")
        if value.is_integer()
        else f"{value:,.2f}".replace(",", " ")
    )


def table_exists(cur: sqlite3.Cursor, name: str) -> bool:
    cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name=?",
        (name,),
    )
    return cur.fetchone() is not None


def table_columns(cur: sqlite3.Cursor, name: str) -> set[str]:
    if not table_exists(cur, name):
        return set()
    cur.execute(f'PRAGMA table_info("{name}")')
    return {row["name"] for row in cur.fetchall()}


def _portal_state(
    user_states: dict,
    user_id: int,
    *,
    create: bool = False,
) -> dict | None:
    value = user_states.get(user_id)
    if isinstance(value, dict) and value.get("_module") == "client_portal":
        return value
    if create:
        value = {"_module": "client_portal", "mode": "client_home"}
        user_states[user_id] = value
        return value
    return None


def _legacy_state_active(user_states: dict, user_id: int) -> bool:
    value = user_states.get(user_id)
    return value is not None and not (
        isinstance(value, dict) and value.get("_module") == "client_portal"
    )


def _unit_select_fields(cur: sqlite3.Cursor) -> str:
    cols = table_columns(cur, "apartments")

    def field(name: str) -> str:
        return name if name in cols else f"NULL AS {name}"

    return ", ".join([
        "id",
        field("apartment_number"),
        field("unit_code"),
        field("unit_type"),
        field("record_status"),
        field("entrance_number"),
        field("entrance"),
        field("display_name"),
    ])


def _account_and_unit(telegram_user_id: int) -> dict | None:
    conn = get_conn()
    try:
        cur = conn.cursor()
        if not table_exists(cur, "resident_accounts"):
            return None

        cur.execute("""
            SELECT
                id,
                telegram_user_id,
                telegram_username,
                telegram_first_name,
                telegram_last_name,
                apartment_id,
                apartment_number,
                status,
                verified_at,
                language_code
            FROM resident_accounts
            WHERE telegram_user_id = ?
        """, (int(telegram_user_id),))
        account_row = cur.fetchone()
        if not account_row:
            return None

        account = dict(account_row)
        unit = None
        unit_select = _unit_select_fields(cur)

        if account.get("apartment_id"):
            cur.execute(
                f"SELECT {unit_select} FROM apartments WHERE id = ?",
                (int(account["apartment_id"]),),
            )
            row = cur.fetchone()
            unit = dict(row) if row else None

        if not unit and text(account.get("apartment_number")):
            cur.execute(
                f"SELECT {unit_select} FROM apartments WHERE apartment_number = ? LIMIT 1",
                (text(account["apartment_number"]),),
            )
            row = cur.fetchone()
            unit = dict(row) if row else None

        return {"account": account, "unit": unit}
    finally:
        conn.close()

def _find_exact_physical_unit(raw: str) -> dict | None:
    """
    Для пользовательской привязки сначала ищем точную физическую квартиру.
    Это важно: ввод 31 не должен автоматически прикрепить логическую группу 31_32.
    """
    raw = text(raw)
    conn = get_conn()
    try:
        cur = conn.cursor()
        cols = table_columns(cur, "apartments")
        predicates = []
        params: list[Any] = []

        if "apartment_number" in cols:
            predicates.append("apartment_number = ?")
            params.append(raw)
        if "unit_code" in cols:
            predicates.append("unit_code = ?")
            params.append(raw)

        if not predicates:
            return None

        cur.execute(
            f"SELECT {_unit_select_fields(cur)} FROM apartments "
            f"WHERE {' OR '.join(predicates)} ORDER BY id LIMIT 1",
            tuple(params),
        )
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()

def _vehicles_for_unit(unit_id: int) -> list[dict]:
    conn = get_conn()
    try:
        cur = conn.cursor()
        if not table_exists(cur, "vehicles"):
            return []
        cur.execute("""
            SELECT
                id,
                license_plate_normalized,
                license_plate,
                car_model_normalized,
                car_model,
                parking_time
            FROM vehicles
            WHERE apartment_id = ?
            ORDER BY id
        """, (int(unit_id),))
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def _format_vehicles(rows: list[dict], lang: str) -> str:
    if not rows:
        return tr(lang, "vehicle_none")

    lines = []
    for row in rows:
        plate = (
            text(row.get("license_plate_normalized"))
            or text(row.get("license_plate"))
            or "-"
        )
        model = (
            text(row.get("car_model_normalized"))
            or text(row.get("car_model"))
            or "-"
        )
        parking = text(row.get("parking_time")) or tr(lang, "parking_unknown")
        lines.append(f"• {plate} | {model} | {parking}")
    return "\n".join(lines)


def _link_requests_ready() -> bool:
    conn = get_conn()
    try:
        return table_exists(conn.cursor(), "apartment_link_requests")
    finally:
        conn.close()


def _create_apartment_link_request(account: dict, unit: dict) -> tuple[int, bool]:
    """
    Returns (request_id, was_created).
    Existing NEW request for the same account and apartment is reused.
    Current apartment remains unchanged until operator approval.
    """
    conn = get_conn()
    try:
        cur = conn.cursor()

        cur.execute("""
            SELECT id
            FROM apartment_link_requests
            WHERE resident_account_id = ?
              AND requested_apartment_id = ?
              AND status = 'NEW'
            ORDER BY id DESC
            LIMIT 1
        """, (int(account["id"]), int(unit["id"])))
        existing = cur.fetchone()
        if existing:
            return int(existing["id"]), False

        cur.execute("""
            INSERT INTO apartment_link_requests (
                resident_account_id,
                telegram_user_id,
                current_apartment_id,
                current_apartment_number,
                requested_apartment_id,
                requested_apartment_number,
                status,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, 'NEW', ?, ?)
        """, (
            int(account["id"]),
            str(account["telegram_user_id"]),
            int(account["apartment_id"]) if account.get("apartment_id") else None,
            text(account.get("apartment_number")) or None,
            int(unit["id"]),
            text(unit.get("apartment_number")),
            now_db(),
            now_db(),
        ))
        request_id = int(cur.lastrowid)

        if audit_log:
            audit_log(
                conn=conn,
                operator_id=str(account["telegram_user_id"]),
                user_id=str(account["telegram_user_id"]),
                actor_type="resident",
                action_type="apartment_link_request_created",
                table_name="apartment_link_requests",
                row_id=request_id,
                field_name="requested_apartment_id,status",
                old_value="",
                new_value=f"{unit['id']}, NEW",
                source_context="client_portal",
                comment="Пользователь создал запрос на привязку квартиры. Автоматическая привязка не выполнялась.",
                extra={
                    "current_apartment_id": account.get("apartment_id"),
                    "requested_apartment_number": unit.get("apartment_number"),
                },
                commit=False,
            )

        conn.commit()
        return request_id, True
    finally:
        conn.close()


def _list_admin_link_requests(status: str | None = None) -> list[dict]:
    conn = get_conn()
    try:
        cur = conn.cursor()
        sql = """
            SELECT
                r.id,
                r.status,
                r.telegram_user_id,
                r.current_apartment_number,
                r.requested_apartment_number,
                r.created_at,
                r.operator_note,
                a.telegram_username,
                a.telegram_first_name,
                a.telegram_last_name
            FROM apartment_link_requests r
            LEFT JOIN resident_accounts a ON a.id = r.resident_account_id
        """
        params: list[Any] = []
        if status:
            sql += " WHERE r.status = ?"
            params.append(status)
        sql += " ORDER BY CASE r.status WHEN 'NEW' THEN 1 ELSE 9 END, r.id DESC LIMIT 50"
        cur.execute(sql, tuple(params))
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def _get_admin_link_request(request_id: int) -> dict | None:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT
                r.*,
                a.telegram_username,
                a.telegram_first_name,
                a.telegram_last_name
            FROM apartment_link_requests r
            LEFT JOIN resident_accounts a ON a.id = r.resident_account_id
            WHERE r.id = ?
        """, (int(request_id),))
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _review_link_request(
    request_id: int,
    *,
    approve: bool,
    operator_id: int,
    operator_note: str | None,
) -> None:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT
                id,
                resident_account_id,
                telegram_user_id,
                current_apartment_id,
                current_apartment_number,
                requested_apartment_id,
                requested_apartment_number,
                status
            FROM apartment_link_requests
            WHERE id = ?
        """, (int(request_id),))
        request = cur.fetchone()
        if not request:
            raise ValueError("Запрос на привязку не найден.")
        if request["status"] != "NEW":
            raise ValueError("Этот запрос уже обработан.")

        new_status = "APPROVED" if approve else "REJECTED"
        timestamp = now_db()

        if approve:
            cur.execute("""
                UPDATE resident_accounts
                SET
                    apartment_id = ?,
                    apartment_number = ?,
                    status = 'apartment_confirmed',
                    verified_at = ?,
                    updated_at = ?
                WHERE id = ?
            """, (
                int(request["requested_apartment_id"]),
                text(request["requested_apartment_number"]),
                timestamp,
                timestamp,
                int(request["resident_account_id"]),
            ))

            if audit_log:
                audit_log(
                    conn=conn,
                    operator_id=str(operator_id),
                    user_id=str(operator_id),
                    actor_type="operator",
                    action_type="resident_account_apartment_link_approved",
                    table_name="resident_accounts",
                    row_id=request["resident_account_id"],
                    field_name="apartment_id,apartment_number",
                    old_value=f"{request['current_apartment_id'] or ''},{request['current_apartment_number'] or ''}",
                    new_value=f"{request['requested_apartment_id']},{request['requested_apartment_number']}",
                    source_context="client_portal",
                    comment="Оператор подтвердил запрос пользователя на привязку квартиры.",
                    extra={"link_request_id": request_id},
                    commit=False,
                )

        cur.execute("""
            UPDATE apartment_link_requests
            SET
                status = ?,
                operator_id = ?,
                operator_note = ?,
                reviewed_at = ?,
                updated_at = ?
            WHERE id = ?
        """, (
            new_status,
            str(operator_id),
            operator_note,
            timestamp,
            timestamp,
            int(request_id),
        ))

        if audit_log:
            audit_log(
                conn=conn,
                operator_id=str(operator_id),
                user_id=str(operator_id),
                actor_type="operator",
                action_type="apartment_link_request_reviewed",
                table_name="apartment_link_requests",
                row_id=request_id,
                field_name="status",
                old_value="NEW",
                new_value=new_status,
                source_context="client_portal",
                comment=operator_note or "Оператор обработал запрос на привязку квартиры.",
                extra={
                    "requested_apartment_number": request["requested_apartment_number"],
                    "approve": approve,
                },
                commit=False,
            )

        conn.commit()
    finally:
        conn.close()


def _format_admin_link_requests(rows: list[dict], lang: str) -> str:
    lines = [tr(lang, "link_admin_title"), ""]
    if not rows:
        lines.append(tr(lang, "link_admin_empty"))
        return "\n".join(lines)

    for row in rows:
        person = " ".join(
            value for value in [
                text(row.get("telegram_first_name")),
                text(row.get("telegram_last_name")),
            ] if value
        ) or text(row.get("telegram_username")) or str(row.get("telegram_user_id") or "-")

        current = text(row.get("current_apartment_number")) or "—"
        requested = text(row.get("requested_apartment_number")) or "—"
        lines.append(
            f"#{row['id']} | {current} → {requested}\n"
            f"{row['status']} | {person}"
        )
    return "\n\n".join(lines)


def _format_admin_link_request_card(row: dict, lang: str) -> str:
    person = " ".join(
        value for value in [
            text(row.get("telegram_first_name")),
            text(row.get("telegram_last_name")),
        ] if value
    ) or text(row.get("telegram_username")) or str(row.get("telegram_user_id") or "-")

    return "\n".join([
        tr(lang, "link_admin_card", id=row["id"]),
        "",
        f"Telegram: {person} | ID {row.get('telegram_user_id') or '-'}",
        f"Текущая квартира: {row.get('current_apartment_number') or 'не привязана'}",
        f"Запрошенная квартира: {row.get('requested_apartment_number') or '-'}",
        f"Статус: {row.get('status') or '-'}",
        f"Создано: {row.get('created_at') or '-'}",
        f"Заметка оператора: {row.get('operator_note') or '-'}",
    ])


def _get_unit_by_id(unit_id: int) -> dict | None:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            f"SELECT {_unit_select_fields(cur)} FROM apartments WHERE id = ?",
            (int(unit_id),),
        )
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Billing read model: no writes, no automatic allocation of payments.
# ---------------------------------------------------------------------------

def _allocation_amount_column(columns: set[str]) -> str | None:
    return "amount" if "amount" in columns else (
        "allocated_amount" if "allocated_amount" in columns else None
    )


def _billing_data(unit: dict) -> dict:
    result = {
        "error": None,
        "charges": [],
        "payments": [],
        "charged_total": 0.0,
        "allocated_total": 0.0,
        "outstanding_total": 0.0,
        "payments_total": 0.0,
        "unallocated_total": 0.0,
        "periods": [],
    }
    conn = get_conn()
    try:
        cur = conn.cursor()
        if not table_exists(cur, "charges"):
            result["error"] = "charges table missing"
            return result

        charge_columns = table_columns(cur, "charges")
        alloc_table = "payment_allocations" if table_exists(cur, "payment_allocations") else None
        alloc_columns = table_columns(cur, alloc_table) if alloc_table else set()

        filters = []
        params: list[Any] = []
        if "apartment_id" in charge_columns:
            filters.append("c.apartment_id = ?")
            params.append(int(unit["id"]))
        if "apartment_number" in charge_columns:
            filters.append("c.apartment_number = ?")
            params.append(text(unit.get("apartment_number")))
        if not filters:
            result["error"] = "charge link columns missing"
            return result

        amount_expr = "c.amount" if "amount" in charge_columns else "0"
        service_expr = "c.service_code" if "service_code" in charge_columns else "NULL"
        period_expr = "c.period_code" if "period_code" in charge_columns else "NULL"
        status_filter = (
            "AND COALESCE(c.charge_status, '') <> 'cancelled'"
            if "charge_status" in charge_columns
            else (
                "AND COALESCE(c.status, '') <> 'cancelled'"
                if "status" in charge_columns else ""
            )
        )

        allocation_join = ""
        allocation_select = "0 AS allocated_amount"
        if alloc_table and "charge_id" in alloc_columns:
            amount_col = _allocation_amount_column(alloc_columns)
            if amount_col:
                allocation_join = (
                    f'LEFT JOIN "{alloc_table}" pa ON pa.charge_id = c.id'
                )
                allocation_select = (
                    f'COALESCE(SUM(pa."{amount_col}"), 0) AS allocated_amount'
                )

        cur.execute(f"""
            SELECT
                c.id AS charge_id,
                {period_expr} AS period_code,
                {service_expr} AS service_code,
                {amount_expr} AS amount,
                {allocation_select}
            FROM charges c
            {allocation_join}
            WHERE ({' OR '.join(filters)})
            {status_filter}
            GROUP BY c.id
            ORDER BY COALESCE({period_expr}, '') DESC, c.id DESC
        """, tuple(params))

        for row in cur.fetchall():
            item = dict(row)
            item["amount"] = float(item["amount"] or 0)
            item["allocated_amount"] = float(item["allocated_amount"] or 0)
            item["outstanding_amount"] = max(0.0, item["amount"] - item["allocated_amount"])
            result["charges"].append(item)

        result["charged_total"] = sum(x["amount"] for x in result["charges"])
        result["allocated_total"] = sum(x["allocated_amount"] for x in result["charges"])
        result["outstanding_total"] = sum(x["outstanding_amount"] for x in result["charges"])

        for item in result["charges"]:
            period = text(item.get("period_code"))
            if period and period not in result["periods"]:
                result["periods"].append(period)

        if table_exists(cur, "payments"):
            payment_columns = table_columns(cur, "payments")
            p_filters = []
            p_params: list[Any] = []
            if "apartment_id" in payment_columns:
                p_filters.append("p.apartment_id = ?")
                p_params.append(int(unit["id"]))
            if "apartment_number" in payment_columns:
                p_filters.append("p.apartment_number = ?")
                p_params.append(text(unit.get("apartment_number")))

            if p_filters:
                p_amount = "p.amount" if "amount" in payment_columns else "0"
                p_date = "p.payment_date" if "payment_date" in payment_columns else "NULL"
                p_period = "p.period_code" if "period_code" in payment_columns else "NULL"
                p_method = (
                    "p.payment_method" if "payment_method" in payment_columns
                    else ("p.source" if "source" in payment_columns else "NULL")
                )

                pay_alloc_join = ""
                pay_alloc_select = "0 AS allocated_amount"
                if alloc_table and "payment_id" in alloc_columns:
                    amount_col = _allocation_amount_column(alloc_columns)
                    if amount_col:
                        pay_alloc_join = (
                            f'LEFT JOIN "{alloc_table}" pa2 ON pa2.payment_id = p.id'
                        )
                        pay_alloc_select = (
                            f'COALESCE(SUM(pa2."{amount_col}"), 0) AS allocated_amount'
                        )

                cur.execute(f"""
                    SELECT
                        p.id AS payment_id,
                        {p_date} AS payment_date,
                        {p_period} AS period_code,
                        {p_method} AS payment_method,
                        {p_amount} AS amount,
                        {pay_alloc_select}
                    FROM payments p
                    {pay_alloc_join}
                    WHERE ({' OR '.join(p_filters)})
                    GROUP BY p.id
                    ORDER BY COALESCE({p_date}, '') DESC, p.id DESC
                """, tuple(p_params))

                for row in cur.fetchall():
                    item = dict(row)
                    item["amount"] = float(item["amount"] or 0)
                    item["allocated_amount"] = float(item["allocated_amount"] or 0)
                    item["unallocated_amount"] = max(
                        0.0, item["amount"] - item["allocated_amount"]
                    )
                    result["payments"].append(item)

                result["payments_total"] = sum(x["amount"] for x in result["payments"])
                result["unallocated_total"] = sum(x["unallocated_amount"] for x in result["payments"])

        return result
    except sqlite3.Error as exc:
        result["error"] = str(exc)
        return result
    finally:
        conn.close()

# OSBB_REMOTE_DEBT_GATE_V1_CLIENT_HELPERS
def _remote_gate_service_is_blocking(service_code: object) -> bool:
    service = text(service_code).upper()
    if not service:
        return False
    return (
        service.startswith("PARKING")
        or service.startswith("BARRIER")
        or "PARK" in service
        or "ШЛАГ" in service
        or "SHLAG" in service
    )


def _remote_gate_block_message(apartment_number: object, amount: float, reason: str = "") -> str:
    apt = text(apartment_number) or "-"
    if reason:
        return (
            f"⚠️ За квартирой {apt} невозможно автоматически проверить задолженность.\n\n"
            "Заказ нового пульта через бот временно недоступен.\n"
            "Пожалуйста, обратитесь к оператору ОСББ для сверки."
        )
    return (
        f"⚠️ За квартирой {apt} числится задолженность за парковку / доступ к шлагбауму: "
        f"{amount:.2f} грн.\n\n"
        "Заказ нового пульта через бот временно недоступен.\n"
        "Пожалуйста, погасите задолженность у кассира/охраны или обратитесь к оператору ОСББ для сверки."
    )


def _remote_debt_gate(unit: dict) -> dict:
    """
    Read-only gate for resident remote requests.

    Uses _billing_data(), so it follows the current charges/payment_allocations
    compatibility logic and does not create any DB rows.
    """
    billing = _billing_data(unit)
    apt = text((unit or {}).get("apartment_number"))

    if billing.get("error"):
        return {
            "allowed": False,
            "outstanding_total": 0.0,
            "message": _remote_gate_block_message(apt, 0.0, str(billing.get("error"))),
        }

    total = 0.0
    rows = []
    for item in billing.get("charges") or []:
        service = item.get("service_code")
        if not _remote_gate_service_is_blocking(service):
            continue
        rest = float(item.get("outstanding_amount") or 0)
        if rest > 0.01:
            total += rest
            rows.append(item)

    if total > 0.01:
        return {
            "allowed": False,
            "outstanding_total": round(total, 2),
            "rows": rows,
            "message": _remote_gate_block_message(apt, total),
        }

    return {
        "allowed": True,
        "outstanding_total": 0.0,
        "rows": [],
        "message": "",
    }



def _service_name(code: str | None, lang: str) -> str:
    code = text(code)
    maps = {
        "PARKING_DAY": {"ru": "Парковка Day", "uk": "Паркування Day", "en": "Parking Day"},
        "PARKING_NIGHT": {"ru": "Парковка Night", "uk": "Паркування Night", "en": "Parking Night"},
    }
    return maps.get(code, {}).get(lang, code or "-")


def _format_dashboard(data: dict, billing: dict, lang: str) -> str:
    unit = data["unit"]
    account = data["account"]
    if not unit:
        return f"{tr(lang, 'cabinet')}\n\n{tr(lang, 'no_unit')}"

    unit_code = text(unit.get("apartment_number")) or text(unit.get("unit_code")) or "-"
    entrance = text(unit.get("entrance_number")) or text(unit.get("entrance")) or "-"
    status = tr(lang, "verified") if text(account.get("verified_at")) else tr(lang, "pending")

    lines = [
        tr(lang, "cabinet"),
        "",
        f"{tr(lang, 'home_label')}: {unit_code}",
        f"{tr(lang, 'entrance')}: {entrance}",
        f"{tr(lang, 'account_status')}: {status}",
        "",
        f"{tr(lang, 'parking')}:",
    ]

    if billing["error"]:
        lines.append(tr(lang, "billing_error"))
    elif not billing["charges"]:
        lines.append(tr(lang, "no_charges"))
    else:
        lines.extend([
            f"{tr(lang, 'charged')}: {money(billing['charged_total'])} грн",
            f"{tr(lang, 'due')}: {money(billing['outstanding_total'])} грн",
        ])
        if billing.get("periods"):
            lines.append(
                f"{tr(lang, 'latest_period')}: {billing['periods'][0]}"
            )
    return "\n".join(lines)


def _format_parking(data: dict, billing: dict, lang: str) -> str:
    unit_code = text(data["unit"].get("apartment_number")) or "-"
    lines = [tr(lang, "parking_title", unit=unit_code), ""]

    if billing["error"]:
        lines.append(tr(lang, "billing_error"))
        return "\n".join(lines)

    if not billing["charges"] and not billing["payments"]:
        lines.append(tr(lang, "no_charges"))
        return "\n".join(lines)

    lines.extend([
        f"{tr(lang, 'charged')}: {money(billing['charged_total'])} грн",
        f"{tr(lang, 'allocated')}: {money(billing['allocated_total'])} грн",
        f"{tr(lang, 'due')}: {money(billing['outstanding_total'])} грн",
        f"{tr(lang, 'received')}: {money(billing['payments_total'])} грн",
    ])
    if billing["unallocated_total"] > 0.009:
        lines.append(f"{tr(lang, 'unallocated')}: {money(billing['unallocated_total'])} грн")
    if billing["periods"]:
        lines.extend(["", f"{tr(lang, 'periods')}: {', '.join(billing['periods'][:6])}"])
    return "\n".join(lines)


def _format_charges(data: dict, billing: dict, lang: str) -> str:
    unit_code = text(data["unit"].get("apartment_number")) or "-"
    lines = [tr(lang, "charges_title", unit=unit_code), ""]
    if billing["error"]:
        lines.append(tr(lang, "billing_error"))
        return "\n".join(lines)
    if not billing["charges"]:
        lines.append(tr(lang, "no_charges"))
        return "\n".join(lines)

    for row in billing["charges"][:10]:
        lines.append(
            f"• {text(row.get('period_code')) or '-'} | "
            f"{_service_name(row.get('service_code'), lang)}\n"
            f"  {tr(lang, 'charged').lower()}: {money(row['amount'])} | "
            f"{tr(lang, 'due').lower()}: {money(row['outstanding_amount'])} грн"
        )
    return "\n".join(lines)


def _format_payments(data: dict, billing: dict, lang: str) -> str:
    unit_code = text(data["unit"].get("apartment_number")) or "-"
    lines = [tr(lang, "payments_title", unit=unit_code), ""]
    if billing["error"]:
        lines.append(tr(lang, "billing_error"))
        return "\n".join(lines)
    if not billing["payments"]:
        lines.append(tr(lang, "no_payments"))
        return "\n".join(lines)

    for row in billing["payments"][:10]:
        lines.append(
            f"• {text(row.get('payment_date')) or '-'} | "
            f"{text(row.get('period_code')) or '-'}\n"
            f"  {money(row['amount'])} грн | "
            f"{text(row.get('payment_method')) or '-'}"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Remote requests.
# ---------------------------------------------------------------------------

def _remote_table_ready() -> bool:
    conn = get_conn()
    try:
        return table_exists(conn.cursor(), "remote_requests")
    finally:
        conn.close()


def _remote_status_label(status: str, lang: str) -> str:
    return tr(lang, f"remote_status_{text(status) or 'NEW'}")


def _remote_kind_label(kind: str, lang: str) -> str:
    return tr(lang, f"remote_kind_{text(kind) or 'FIRST'}")


def _remote_requests_for_account(account_id: int) -> list[dict]:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT
                id, request_kind, quantity, resident_comment, status,
                operator_note, created_at, updated_at, reviewed_at, issued_at
            FROM remote_requests
            WHERE resident_account_id = ?
            ORDER BY id DESC
            LIMIT 30
        """, (int(account_id),))
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def _create_remote_request(
    *,
    account: dict,
    unit: dict,
    request_kind: str,
    quantity: int,
    resident_comment: str | None,
) -> int:
    # OSBB_REMOTE_DEBT_GATE_V1_CREATE_CHECK
    gate = _remote_debt_gate(unit)
    if not gate.get("allowed"):
        raise ValueError(gate.get("message") or "Заказ пульта временно недоступен из-за задолженности.")

    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO remote_requests (
                resident_account_id,
                telegram_user_id,
                apartment_id,
                apartment_number,
                request_kind,
                quantity,
                resident_comment,
                status,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 'NEW', ?, ?)
        """, (
            int(account["id"]),
            str(account["telegram_user_id"]),
            int(unit["id"]),
            text(unit.get("apartment_number")),
            request_kind,
            int(quantity),
            resident_comment,
            now_db(),
            now_db(),
        ))
        request_id = int(cur.lastrowid)

        if audit_log:
            audit_log(
                conn=conn,
                operator_id=str(account["telegram_user_id"]),
                user_id=str(account["telegram_user_id"]),
                actor_type="resident",
                action_type="remote_request_created",
                table_name="remote_requests",
                row_id=request_id,
                field_name="request_kind,quantity,status",
                old_value="",
                new_value=f"{request_kind}, {quantity}, NEW",
                source_context="client_portal",
                comment="Пользователь создал обращение на пульт.",
                extra={"apartment_id": unit["id"], "apartment_number": unit.get("apartment_number")},
                commit=False,
            )
        conn.commit()
        return request_id
    finally:
        conn.close()


def _admin_remote_rows(status: str | None = None) -> list[dict]:
    conn = get_conn()
    try:
        cur = conn.cursor()
        sql = """
            SELECT
                r.id, r.apartment_number, r.request_kind, r.quantity, r.status,
                r.resident_comment, r.operator_note, r.created_at,
                r.telegram_user_id,
                a.telegram_username, a.telegram_first_name, a.telegram_last_name
            FROM remote_requests r
            LEFT JOIN resident_accounts a ON a.id = r.resident_account_id
        """
        params: list[Any] = []
        if status:
            sql += " WHERE r.status = ?"
            params.append(status)
        sql += " ORDER BY CASE r.status WHEN 'NEW' THEN 1 WHEN 'IN_REVIEW' THEN 2 ELSE 9 END, r.id DESC LIMIT 30"
        cur.execute(sql, tuple(params))
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def _admin_remote_request(request_id: int) -> dict | None:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT
                r.*,
                a.telegram_username, a.telegram_first_name, a.telegram_last_name
            FROM remote_requests r
            LEFT JOIN resident_accounts a ON a.id = r.resident_account_id
            WHERE r.id = ?
        """, (int(request_id),))
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _update_remote_status(
    request_id: int,
    status: str,
    operator_id: int,
    operator_note: str | None = None,
) -> None:
    if status not in {"NEW", "IN_REVIEW", "ISSUED", "REJECTED", "CANCELLED"}:
        raise ValueError("Недопустимый статус заявки.")

    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT status, apartment_number, request_kind, quantity
            FROM remote_requests WHERE id = ?
        """, (int(request_id),))
        old = cur.fetchone()
        if not old:
            raise ValueError("Заявка не найдена.")

        timestamps = {
            "reviewed_at": now_db() if status == "IN_REVIEW" else None,
            "issued_at": now_db() if status == "ISSUED" else None,
            "closed_at": now_db() if status in {"ISSUED", "REJECTED", "CANCELLED"} else None,
        }
        cur.execute("""
            UPDATE remote_requests
            SET
                status = ?,
                operator_id = ?,
                operator_note = COALESCE(?, operator_note),
                updated_at = ?,
                reviewed_at = COALESCE(?, reviewed_at),
                issued_at = COALESCE(?, issued_at),
                closed_at = COALESCE(?, closed_at)
            WHERE id = ?
        """, (
            status,
            str(operator_id),
            operator_note,
            now_db(),
            timestamps["reviewed_at"],
            timestamps["issued_at"],
            timestamps["closed_at"],
            int(request_id),
        ))

        if audit_log:
            audit_log(
                conn=conn,
                operator_id=str(operator_id),
                user_id=str(operator_id),
                actor_type="operator",
                action_type="remote_request_status_update",
                table_name="remote_requests",
                row_id=request_id,
                field_name="status",
                old_value=old["status"],
                new_value=status,
                source_context="client_portal",
                comment=operator_note or "Оператор изменил статус заявки на пульт.",
                extra={
                    "apartment_number": old["apartment_number"],
                    "request_kind": old["request_kind"],
                    "quantity": old["quantity"],
                },
                commit=False,
            )
        conn.commit()
    finally:
        conn.close()


def _format_my_remote_requests(rows: list[dict], lang: str) -> str:
    lines = [tr(lang, "remote_my_title"), ""]
    if not rows:
        lines.append(tr(lang, "remote_no_requests"))
        return "\n".join(lines)

    for row in rows:
        lines.extend([
            f"#{row['id']} | {_remote_kind_label(row['request_kind'], lang)}",
            f"{_remote_status_label(row['status'], lang)} | {row['quantity']} шт.",
            f"{tr(lang, 'remote_comment')}: {row['resident_comment'] or '-'}",
        ])
        if row.get("operator_note"):
            lines.append(f"{tr(lang, 'remote_operator_note')}: {row['operator_note']}")
        lines.append("")
    return "\n".join(lines)


def _format_admin_remote_list(rows: list[dict], lang: str) -> str:
    lines = [tr(lang, "remote_admin_title"), ""]
    if not rows:
        lines.append(tr(lang, "remote_admin_empty"))
        return "\n".join(lines)

    for row in rows:
        person = " ".join(
            x for x in [text(row.get("telegram_first_name")), text(row.get("telegram_last_name"))] if x
        ) or text(row.get("telegram_username")) or str(row.get("telegram_user_id") or "-")
        lines.append(
            f"#{row['id']} | кв. {row['apartment_number']} | "
            f"{_remote_kind_label(row['request_kind'], lang)} × {row['quantity']}\n"
            f"{_remote_status_label(row['status'], lang)} | {person}"
        )
    return "\n\n".join(lines)


def _format_admin_remote_card(row: dict, lang: str) -> str:
    person = " ".join(
        x for x in [text(row.get("telegram_first_name")), text(row.get("telegram_last_name"))] if x
    ) or text(row.get("telegram_username")) or str(row.get("telegram_user_id") or "-")

    return "\n".join([
        tr(lang, "remote_admin_card", id=row["id"]),
        "",
        f"{tr(lang, 'home_label')}: {row['apartment_number']}",
        f"Telegram: {person} | ID {row.get('telegram_user_id') or '-'}",
        f"{_remote_kind_label(row['request_kind'], lang)}: {row['quantity']} шт.",
        f"Статус: {_remote_status_label(row['status'], lang)}",
        f"{tr(lang, 'remote_comment')}: {row.get('resident_comment') or '-'}",
        f"{tr(lang, 'remote_operator_note')}: {row.get('operator_note') or '-'}",
        f"Создано: {row.get('created_at') or '-'}",
    ])


# ---------------------------------------------------------------------------
# Screens.
# ---------------------------------------------------------------------------

# async def show_client_portal(update: Update, user_states: dict, user_id: int, lang: str) -> None:
#     data = _account_and_unit(user_id)
#     if not data or not data.get("unit"):
#         state = _portal_state(user_states, user_id, create=True)
#         state["mode"] = "portal_unlinked"
#         await update.message.reply_text(
#             f"{tr(lang, 'cabinet')}\n\n{tr(lang, 'no_unit')}",
#             reply_markup=kb([[tr(lang, "change_home")], [tr(lang, "home")]]),
#         )
#         return

#     billing = _billing_data(data["unit"])
#     state = _portal_state(user_states, user_id, create=True)
#     state["mode"] = "client_home"
#     await update.message.reply_text(
#         _format_dashboard(data, billing, lang),
#         reply_markup=kb(client_menu_keyboard(lang)),
#     )

async def show_client_portal(update: Update, user_states: dict, user_id: int, lang: str) -> None:
    data = _account_and_unit(user_id)
    
    # ВОТ ЗДЕСЬ МЫ ВСТАВЛЯЕМ НАШЕ НОВОЕ ПРИВЕТСТВИЕ
    welcome_message = client_welcome_text(lang, user_id)

    if not data or not data.get("unit"):
        state = _portal_state(user_states, user_id, create=True)
        state["mode"] = "portal_unlinked"
        # Отправляем приветствие + сообщение о том, что квартира не привязана
        await update.message.reply_text(
            f"{welcome_message}\n\n{tr(lang, 'no_unit')}",
            reply_markup=kb([[tr(lang, "change_home")], [tr(lang, "home")]]),
        )
        return

    billing = _billing_data(data["unit"])
    state = _portal_state(user_states, user_id, create=True)
    state["mode"] = "client_home"
    
    # Отправляем приветствие + основную информацию
    await update.message.reply_text(
        f"{welcome_message}\n\n{_format_dashboard(data, billing, lang)}",
        reply_markup=kb(client_menu_keyboard(lang)),
    )


async def show_parking(update: Update, user_states: dict, user_id: int, lang: str) -> None:
    data = _account_and_unit(user_id)
    if not data or not data.get("unit"):
        await show_client_portal(update, user_states, user_id, lang)
        return
    state = _portal_state(user_states, user_id, create=True)
    state["mode"] = "client_parking"
    await update.message.reply_text(
        _format_parking(data, _billing_data(data["unit"]), lang),
        reply_markup=kb(parking_menu_keyboard(lang)),
    )


async def show_remotes(update: Update, user_states: dict, user_id: int, lang: str) -> None:
    state = _portal_state(user_states, user_id, create=True)
    state["mode"] = "client_remotes"
    await update.message.reply_text(
        f"{tr(lang, 'remotes_title')}\n\n{tr(lang, 'remotes_intro')}",
        reply_markup=kb(remotes_menu_keyboard(lang)),
    )


async def show_admin_remotes(update: Update, user_states: dict, user_id: int, lang: str, only_new: bool = True) -> None:
    if not _remote_table_ready():
        await update.message.reply_text(tr(lang, "remote_missing_table"))
        return

    rows = _admin_remote_rows("NEW" if only_new else None)
    state = _portal_state(user_states, user_id, create=True)
    state["mode"] = "admin_remote_list"
    state["remote_admin_filter"] = "NEW" if only_new else "ALL"
    state["remote_admin_buttons"] = {
        f"🔑 #{row['id']} кв.{row['apartment_number']}": int(row["id"])
        for row in rows
    }
    buttons = []
    for label in state["remote_admin_buttons"]:
        buttons.append([label])
    buttons.extend(admin_remote_menu_keyboard(lang))

    await update.message.reply_text(
        _format_admin_remote_list(rows, lang),
        reply_markup=kb(buttons),
    )


async def _ask_portal_unit(update: Update, user_states: dict, user_id: int, lang: str) -> None:
    state = _portal_state(user_states, user_id, create=True)
    state["mode"] = "portal_wait_unit"
    await update.message.reply_text(
        tr(lang, "link_prompt"),
        reply_markup=kb([[tr(lang, "back_portal")], [tr(lang, "home")]]),
    )


async def _confirm_portal_unit(update: Update, user_states: dict, user_id: int, lang: str, unit: dict) -> None:
    # До решения оператора не показываем автомобили или иные данные выбранной
    # квартиры: пользователь может ошибиться или ввести чужой номер.
    state = _portal_state(user_states, user_id, create=True)
    state["mode"] = "portal_confirm_unit"
    state["pending_unit_id"] = int(unit["id"])
    unit_code = text(unit.get("apartment_number")) or text(unit.get("unit_code")) or "-"
    await update.message.reply_text(
        tr(lang, "link_confirm", unit=unit_code),
        reply_markup=kb([[tr(lang, "yes")], [tr(lang, "other_home")], [tr(lang, "home")]]),
    )


async def _show_vehicle_list(update: Update, user_states: dict, user_id: int, lang: str) -> None:
    data = _account_and_unit(user_id)
    if not data or not data.get("unit"):
        await show_client_portal(update, user_states, user_id, lang)
        return
    unit_code = text(data["unit"].get("apartment_number")) or "-"
    state = _portal_state(user_states, user_id, create=True)
    state["mode"] = "client_vehicles"
    await update.message.reply_text(
        f"{tr(lang, 'vehicle_title', unit=unit_code)}\n\n"
        f"{_format_vehicles(_vehicles_for_unit(int(data['unit']['id'])), lang)}",
        reply_markup=kb([[tr(lang, "back_portal")], [tr(lang, "home")]]),
    )


async def _show_my_remote_requests(update: Update, user_states: dict, user_id: int, lang: str) -> None:
    data = _account_and_unit(user_id)
    if not data or not data.get("unit"):
        await show_client_portal(update, user_states, user_id, lang)
        return
    if not _remote_table_ready():
        await update.message.reply_text(tr(lang, "remote_missing_table"), reply_markup=kb(remotes_menu_keyboard(lang)))
        return
    state = _portal_state(user_states, user_id, create=True)
    state["mode"] = "client_remotes"
    await update.message.reply_text(
        _format_my_remote_requests(_remote_requests_for_account(int(data["account"]["id"])), lang),
        reply_markup=kb(remotes_menu_keyboard(lang)),
    )


async def _start_remote_request(update: Update, user_states: dict, user_id: int, lang: str) -> None:
    data = _account_and_unit(user_id)
    if not data or not data.get("unit"):
        await show_client_portal(update, user_states, user_id, lang)
        return
    if not _remote_table_ready():
        await update.message.reply_text(tr(lang, "remote_missing_table"), reply_markup=kb(remotes_menu_keyboard(lang)))
        return

    state = _portal_state(user_states, user_id, create=True)
    state["mode"] = "remote_choose_kind"
    await update.message.reply_text(
        tr(lang, "remote_kind_prompt"),
        reply_markup=kb([
            [tr(lang, "remote_first"), tr(lang, "remote_additional")],
            [tr(lang, "remote_replace")],
            [tr(lang, "back_portal"), tr(lang, "home")],
        ]),
    )


async def _show_stub(update: Update, user_states: dict, user_id: int, lang: str, key: str, mode: str) -> None:
    state = _portal_state(user_states, user_id, create=True)
    state["mode"] = mode
    await update.message.reply_text(
        tr(lang, key),
        reply_markup=kb([[tr(lang, "back_portal")], [tr(lang, "home")]]),
    )



async def show_admin_link_requests(
    update: Update,
    user_states: dict,
    user_id: int,
    lang: str,
    *,
    only_new: bool = True,
) -> None:
    if not _link_requests_ready():
        await update.message.reply_text(tr(lang, "link_request_missing"))
        return

    rows = _list_admin_link_requests("NEW" if only_new else None)
    state = _portal_state(user_states, user_id, create=True)
    state["mode"] = "admin_link_list"
    state["admin_link_filter"] = "NEW" if only_new else "ALL"
    state["admin_link_buttons"] = {
        f"🔗 #{row['id']} {row.get('current_apartment_number') or '—'}→{row.get('requested_apartment_number') or '—'}": int(row["id"])
        for row in rows
    }

    buttons = [[label] for label in state["admin_link_buttons"]]
    buttons.extend([
        [tr(lang, "link_admin_new"), tr(lang, "link_admin_all")],
        [tr(lang, "home")],
    ])

    await update.message.reply_text(
        _format_admin_link_requests(rows, lang),
        reply_markup=kb(buttons),
    )

# ---------------------------------------------------------------------------
# Main handler.
# ---------------------------------------------------------------------------

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
    """
    Client mode is strict:
    - after language selection, only the current language menu is accepted;
    - an old-language/wrong-language button does not fall through to old RU logic;
    - legacy text/tuple states are untouched and handled by existing bot code.
    """
    lang = lang if lang in I18N else "ru"
    message_text = text(message_text)

    # Let the existing mode switch be processed by parking_bot.py.
    mode_switch_texts = {
        "👤 Клиентский режим", "👤 Режим мешканця", "👤 User mode",
        "🔐 Админ-режим", "🔐 Адмін-режим", "🔐 Admin mode",
    }
    if message_text in mode_switch_texts:
        return False

    # Never steal existing legacy data-entry states.
    if _legacy_state_active(user_states, user_id):
        return False

    state = _portal_state(user_states, user_id, create=False)
    current = text(state.get("mode")) if state else ""

    # Admin: handle only remote queue. Other admin sections remain old code.
    if user_mode == "admin":
        link_titles = {
            tr(lang, "link_admin"),
            "🔗 Запросы квартир",
            "🔗 Запити квартир",
            "🔗 Apartment link requests",
        }
        if message_text in link_titles:
            if not is_admin:
                await update.message.reply_text(tr(lang, "remote_only_admin"))
            else:
                await show_admin_link_requests(update, user_states, user_id, lang, only_new=True)
            return True

        if current.startswith("admin_link_"):
            if message_text == tr(lang, "home"):
                user_states.pop(user_id, None)
                return False

            if current == "admin_link_list":
                if message_text == tr(lang, "link_admin_new"):
                    await show_admin_link_requests(update, user_states, user_id, lang, only_new=True)
                    return True
                if message_text == tr(lang, "link_admin_all"):
                    await show_admin_link_requests(update, user_states, user_id, lang, only_new=False)
                    return True

                request_id = (state.get("admin_link_buttons") or {}).get(message_text)
                if request_id:
                    row = _get_admin_link_request(int(request_id))
                    if not row:
                        await update.message.reply_text("Запрос не найден.")
                        return True
                    state["mode"] = "admin_link_card"
                    state["admin_link_request_id"] = int(request_id)
                    buttons = [
                        [tr(lang, "link_approve"), tr(lang, "link_reject")],
                        [tr(lang, "link_admin")],
                        [tr(lang, "home")],
                    ]
                    await update.message.reply_text(
                        _format_admin_link_request_card(row, lang),
                        reply_markup=kb(buttons),
                    )
                    return True

                await update.message.reply_text("Выберите запрос кнопкой.")
                return True

            if current == "admin_link_card":
                if message_text == tr(lang, "link_admin"):
                    await show_admin_link_requests(
                        update,
                        user_states,
                        user_id,
                        lang,
                        only_new=(state.get("admin_link_filter") != "ALL"),
                    )
                    return True

                target = {
                    tr(lang, "link_approve"): True,
                    tr(lang, "link_reject"): False,
                }.get(message_text)

                if target is not None:
                    state["mode"] = "admin_link_wait_note"
                    state["admin_link_approve"] = target
                    await update.message.reply_text(
                        tr(lang, "link_operator_note_prompt"),
                        reply_markup=kb([[tr(lang, "link_admin")], [tr(lang, "home")]]),
                    )
                    return True

                await update.message.reply_text("Выберите действие кнопкой.")
                return True

            if current == "admin_link_wait_note":
                if message_text == tr(lang, "link_admin"):
                    await show_admin_link_requests(update, user_states, user_id, lang, only_new=True)
                    return True

                note = None if message_text == "-" else message_text
                _review_link_request(
                    int(state["admin_link_request_id"]),
                    approve=bool(state["admin_link_approve"]),
                    operator_id=user_id,
                    operator_note=note,
                )
                await update.message.reply_text(tr(lang, "link_admin_updated"))
                await show_admin_link_requests(update, user_states, user_id, lang, only_new=True)
                return True

        if message_text == tr(lang, "remote_list") or message_text == "🔑 Заявки на пульты":
            if not is_admin:
                await update.message.reply_text(tr(lang, "remote_only_admin"))
            else:
                await show_admin_remotes(update, user_states, user_id, lang, only_new=True)
            return True

        if current.startswith("admin_remote_"):
            if message_text == tr(lang, "home"):
                user_states.pop(user_id, None)
                return False

            if current == "admin_remote_list":
                if message_text == tr(lang, "remote_admin_new"):
                    await show_admin_remotes(update, user_states, user_id, lang, only_new=True)
                    return True
                if message_text == tr(lang, "remote_admin_all"):
                    await show_admin_remotes(update, user_states, user_id, lang, only_new=False)
                    return True

                request_id = (state.get("remote_admin_buttons") or {}).get(message_text)
                if request_id:
                    row = _admin_remote_request(int(request_id))
                    if not row:
                        await update.message.reply_text("Заявка не найдена.")
                        return True
                    state["mode"] = "admin_remote_card"
                    state["remote_admin_request_id"] = int(request_id)
                    buttons = [
                        [tr(lang, "remote_in_work"), tr(lang, "remote_issued")],
                        [tr(lang, "remote_rejected")],
                        [tr(lang, "back_requests"), tr(lang, "home")],
                    ]
                    await update.message.reply_text(_format_admin_remote_card(row, lang), reply_markup=kb(buttons))
                    return True

                await update.message.reply_text("Выберите заявку кнопкой.", reply_markup=kb(admin_remote_menu_keyboard(lang)))
                return True

            if current == "admin_remote_card":
                request_id = state.get("remote_admin_request_id")
                if message_text == tr(lang, "back_requests"):
                    await show_admin_remotes(
                        update,
                        user_states,
                        user_id,
                        lang,
                        only_new=(state.get("remote_admin_filter") != "ALL"),
                    )
                    return True

                mapping = {
                    tr(lang, "remote_in_work"): "IN_REVIEW",
                    tr(lang, "remote_issued"): "ISSUED",
                    tr(lang, "remote_rejected"): "REJECTED",
                }
                target = mapping.get(message_text)
                if target:
                    state["mode"] = "admin_remote_wait_note"
                    state["remote_admin_target_status"] = target
                    await update.message.reply_text(
                        tr(lang, "remote_admin_note_prompt"),
                        reply_markup=kb([[tr(lang, "back_requests")], [tr(lang, "home")]]),
                    )
                    return True

                await update.message.reply_text("Выберите действие кнопкой.")
                return True

            if current == "admin_remote_wait_note":
                if message_text == tr(lang, "back_requests"):
                    await show_admin_remotes(update, user_states, user_id, lang, only_new=False)
                    return True
                note = None if message_text == "-" else message_text
                _update_remote_status(
                    int(state["remote_admin_request_id"]),
                    text(state["remote_admin_target_status"]),
                    user_id,
                    note,
                )
                await update.message.reply_text(tr(lang, "remote_admin_updated"))
                await show_admin_remotes(update, user_states, user_id, lang, only_new=False)
                return True

        return False

    # Non-client modes: do not interfere.
    if user_mode != "client":
        return False

    # Strict current-language home button.
    if message_text == tr(lang, "home"):
        await show_client_portal(update, user_states, user_id, lang)
        return True

    # Root actions. We always process client messages here before old hard-coded RU.
    root_actions = {
        tr(lang, "my_home"): "home",
        tr(lang, "change_home"): "change",
        tr(lang, "my_vehicles"): "vehicles",
        tr(lang, "parking"): "parking",
        tr(lang, "remotes"): "remotes",
        tr(lang, "phone"): "phone",
        tr(lang, "improve"): "improve",
        tr(lang, "news"): "news",
        tr(lang, "contacts"): "contacts",
    }
    if message_text in root_actions:
        action = root_actions[message_text]
        if action == "home":
            await show_client_portal(update, user_states, user_id, lang)
        elif action == "change":
            await _ask_portal_unit(update, user_states, user_id, lang)
        elif action == "vehicles":
            await _show_vehicle_list(update, user_states, user_id, lang)
        elif action == "parking":
            await show_parking(update, user_states, user_id, lang)
        elif action == "remotes":
            await show_remotes(update, user_states, user_id, lang)
        elif action == "phone":
            await _show_stub(update, user_states, user_id, lang, "phones_stub", "client_phone")
        elif action == "improve":
            await _show_stub(update, user_states, user_id, lang, "improve_stub", "client_improve")
        elif action == "news":
            await _show_stub(update, user_states, user_id, lang, "news_stub", "client_news")
        elif action == "contacts":
            await _show_stub(update, user_states, user_id, lang, "contacts_stub", "client_contacts")
        return True

    # No local portal state yet: random/wrong-language input is not sent to old RU flow.
    if not current:
        await show_client_portal(update, user_states, user_id, lang)
        return True

    # Common back to portal.
    if message_text == tr(lang, "back_portal"):
        await show_client_portal(update, user_states, user_id, lang)
        return True

    # Link apartment.
    if current == "portal_unlinked":
        if message_text == tr(lang, "change_home"):
            await _ask_portal_unit(update, user_states, user_id, lang)
        else:
            await update.message.reply_text(tr(lang, "choose_menu"), reply_markup=kb([[tr(lang, "change_home")], [tr(lang, "home")]]))
        return True

    if current == "portal_wait_unit":
        unit = _find_exact_physical_unit(message_text)
        if not unit:
            await update.message.reply_text(tr(lang, "link_not_found"))
            return True
        if text(unit.get("unit_type")) and text(unit.get("unit_type")) != "RESIDENTIAL":
            await update.message.reply_text(tr(lang, "link_group"))
            return True
        await _confirm_portal_unit(update, user_states, user_id, lang, unit)
        return True

    if current == "portal_confirm_unit":
        if message_text == tr(lang, "other_home"):
            await _ask_portal_unit(update, user_states, user_id, lang)
            return True

        if message_text == tr(lang, "yes"):
            if not _link_requests_ready():
                await update.message.reply_text(tr(lang, "link_request_missing"))
                return True

            pending_id = state.get("pending_unit_id")
            if not pending_id:
                await _ask_portal_unit(update, user_states, user_id, lang)
                return True

            unit = _get_unit_by_id(int(pending_id))
            if not unit:
                await update.message.reply_text(tr(lang, "link_not_found"))
                return True

            data = _account_and_unit(user_id)
            if not data:
                await update.message.reply_text(tr(lang, "link_request_missing"))
                return True

            request_id, created = _create_apartment_link_request(data["account"], unit)
            unit_code = text(unit.get("apartment_number")) or "-"
            await update.message.reply_text(
                tr(lang, "linked", id=request_id, unit=unit_code)
            )

            # Текущую подтверждённую квартиру не меняем до решения оператора.
            await show_client_portal(update, user_states, user_id, lang)
            return True

        await update.message.reply_text(tr(lang, "choose_menu"))
        return True

    # Parking submenu.
    if current == "client_parking":
        data = _account_and_unit(user_id)
        if not data or not data.get("unit"):
            await show_client_portal(update, user_states, user_id, lang)
            return True
        billing = _billing_data(data["unit"])

        if message_text == tr(lang, "parking_balance"):
            await update.message.reply_text(_format_parking(data, billing, lang), reply_markup=kb(parking_menu_keyboard(lang)))
        elif message_text == tr(lang, "parking_charges"):
            await update.message.reply_text(_format_charges(data, billing, lang), reply_markup=kb(parking_menu_keyboard(lang)))
        elif message_text == tr(lang, "parking_payments"):
            await update.message.reply_text(_format_payments(data, billing, lang), reply_markup=kb(parking_menu_keyboard(lang)))
        elif message_text == tr(lang, "parking_how"):
            await update.message.reply_text(tr(lang, "payment_help"), reply_markup=kb(parking_menu_keyboard(lang)))
        else:
            await update.message.reply_text(tr(lang, "choose_menu"), reply_markup=kb(parking_menu_keyboard(lang)))
        return True

    # Remote submenu.
    if current == "client_remotes":
        if message_text == tr(lang, "remote_my"):
            await _show_my_remote_requests(update, user_states, user_id, lang)
        elif message_text == tr(lang, "remote_new"):
            await _start_remote_request(update, user_states, user_id, lang)
        elif message_text == tr(lang, "remote_how"):
            await update.message.reply_text(tr(lang, "remote_how_text"), reply_markup=kb(remotes_menu_keyboard(lang)))
        else:
            await update.message.reply_text(tr(lang, "choose_menu"), reply_markup=kb(remotes_menu_keyboard(lang)))
        return True

    if current == "remote_choose_kind":
        kind_map = {
            tr(lang, "remote_first"): "FIRST",
            tr(lang, "remote_additional"): "ADDITIONAL",
            tr(lang, "remote_replace"): "REPLACEMENT",
        }
        selected = kind_map.get(message_text)
        if not selected:
            await update.message.reply_text(tr(lang, "choose_menu"))
            return True
        state["remote_kind"] = selected
        state["mode"] = "remote_wait_quantity"
        await update.message.reply_text(
            tr(lang, "remote_quantity_prompt"),
            reply_markup=kb([[tr(lang, "back_portal")], [tr(lang, "home")]]),
        )
        return True

    if current == "remote_wait_quantity":
        try:
            quantity = int(message_text)
            if quantity < 1 or quantity > 10:
                raise ValueError
        except ValueError:
            await update.message.reply_text(tr(lang, "wrong_remote_qty"))
            return True
        state["remote_quantity"] = quantity
        state["mode"] = "remote_wait_comment"
        await update.message.reply_text(
            tr(lang, "remote_comment_prompt"),
            reply_markup=kb([[tr(lang, "back_portal")], [tr(lang, "home")]]),
        )
        return True

    if current == "remote_wait_comment":
        data = _account_and_unit(user_id)
        if not data or not data.get("unit"):
            await show_client_portal(update, user_states, user_id, lang)
            return True
        if not _remote_table_ready():
            await update.message.reply_text(tr(lang, "remote_missing_table"))
            await show_remotes(update, user_states, user_id, lang)
            return True
        comment = None if message_text == "-" else message_text
        # OSBB_REMOTE_DEBT_GATE_V1_CALL_WRAP
        try:
            request_id = _create_remote_request(
                account=data["account"],
                unit=data["unit"],
                request_kind=text(state.get("remote_kind")) or "FIRST",
                quantity=int(state.get("remote_quantity") or 1),
                resident_comment=comment,
            )
        except ValueError as exc:
            await update.message.reply_text(f"⚠️ {exc}", reply_markup=kb(remotes_menu_keyboard(lang)))
            state["mode"] = "client_remotes"
            return True
        state["mode"] = "client_remotes"
        state.pop("remote_kind", None)
        state.pop("remote_quantity", None)
        await update.message.reply_text(tr(lang, "remote_saved", id=request_id), reply_markup=kb(remotes_menu_keyboard(lang)))
        return True

    # Stub screens and vehicle list.
    if current in {"client_phone", "client_improve", "client_news", "client_contacts", "client_vehicles"}:
        await update.message.reply_text(
            tr(lang, "choose_menu"),
            reply_markup=kb([[tr(lang, "back_portal")], [tr(lang, "home")]]),
        )
        return True

    await show_client_portal(update, user_states, user_id, lang)
    return True
