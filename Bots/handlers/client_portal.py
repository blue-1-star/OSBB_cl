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
import json
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
from access_control import has_permission
from data_quality_report import build_quality_issues, quality_summary
from utils import normalize_plate as _registry_normalize_plate

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
        "choose_menu": "Выберите кнопку в меню.",
        "home": "🏠 Главное меню",
        "back_portal": "⬅️ К кабинету",
        "my_home": "🏠 Моя квартира",
        "claim_home": "🏠 Указать свою квартиру",
        "change_home": "✏️ Запросить смену квартиры",
        "my_home_settings": "🏠 Моя квартира\n\nКвартира: {apartment}\n\nЕсли номер квартиры изменился или был указан неверно, здесь можно отправить запрос оператору.",
        "my_vehicles": "🚗 Мои автомобили",
        "vehicle_resident_none": "Мы пока не нашли автомобилей, которые вы ранее сообщали. Если автомобиль есть, добавьте его — оператор проверит данные.",
        "suggest_changes": "✍️ Предложить изменение",
        "my_change_requests": "📨 Мои изменения",
        "change_requests_menu_title": "📨 Мои обращения\n\nВыберите, что показать.",
        "change_requests_active": "📨 Ожидающие",
        "change_requests_history": "🗂️ История",
        "reply_operator": "💬 Ответить оператору",
        "clarification_choose": "Выберите заявку, по которой хотите ответить оператору.",
        "clarification_reply_prompt": "Напишите ответ оператору. Он будет добавлен к этой заявке.",
        "clarification_none": "Нет заявок, ожидающих вашего ответа.",
        "clarification_reply_saved": "✅ Ответ отправлен оператору. Заявка снова ожидает рассмотрения.",
        "observer": "📖 Наблюдатель ОСББ",
        "observer_title": "📖 Наблюдатель ОСББ\n\nРежим только для просмотра. Изменение данных, заявок и платежей здесь невозможно.",
        "observer_summary": "📊 Сводка",
        "observer_apartments": "🏠 Квартиры и жители",
        "observer_vehicles": "🚗 Все автомобили",
        "observer_payments": "💰 Последние оплаты",
        "observer_requests": "📨 Заявки жителей",
        "observer_quality": "🧩 Пробелы в данных",
        "observer_denied": "У вас нет роли наблюдателя ОСББ.",
        "observer_prev": "⬅️ Раньше",
        "observer_next": "➡️ Далее",
        "my_change_requests_title": "📨 Мои предложения оператору",
        "my_change_requests_active_title": "📨 Мои ожидающие обращения",
        "my_change_requests_history_title": "🗂️ История обращений",
        "my_change_requests_none": "Вы ещё не отправляли предложений по своим данным.",
        "request_status_PENDING": "⏳ ожидает рассмотрения",
        "request_status_IN_PROGRESS": "🔵 в работе",
        "request_status_NEEDS_CLARIFICATION": "🟡 требуется уточнение",
        "request_status_RESOLVED": "✅ решено",
        "request_status_REJECTED": "❌ отклонено",
        "request_status_CLOSED": "✅ закрыто",
        "request_type_RESIDENT_VEHICLE_PARKING_END": "Автомобиль продан / не паркуется",
        "request_type_RESIDENT_VEHICLE_REMOVE": "Автомобиль внесён ошибочно",
        "request_type_RESIDENT_VEHICLE_UPDATE": "Исправление данных автомобиля",
        "request_type_RESIDENT_VEHICLE_CHANGE": "Изменение данных автомобиля",
        "request_type_RESIDENT_VEHICLE_ADD": "Добавление автомобиля",
        "request_type_RESIDENT_VEHICLES_CONFIRMED": "Подтверждение списка автомобилей",
        "request_type_RESIDENT_PROFILE_CHANGE": "Изменение личных данных",
        "suggest_vehicle": "🚗 Исправить автомобиль",
        "suggest_add_vehicle": "➕ Добавить автомобиль",
        "suggest_profile": "👤 Исправить мои данные",
        "suggest_vehicle_prompt": "Выберите автомобиль, по которому хотите предложить исправление.",
        "suggest_vehicle_text": "Опишите, что нужно исправить в этом автомобиле.",
        "vehicle_change_kind_prompt": "Что нужно изменить? Сначала показаны данные, которые сейчас находятся в реестре.",
        "vehicle_current": "Сейчас в реестре:\nНомер: {plate}\nМарка/модель: {model}\nЦвет: {color}\nРежим парковки: {parking}",
        "edit_plate": "🔢 Исправить госномер",
        "edit_model": "🚘 Исправить марку/модель",
        "edit_color": "🎨 Исправить цвет",
        "edit_parking_time": "🕒 Изменить режим парковки",
        "stop_parking": "🚫 Продан / не паркуется",
        "remove_vehicle": "🗑️ Внесён ошибочно",
        "vehicle_remove_prompt": "Подтвердите: этот автомобиль внесён ошибочно. Оператор проверит, можно ли удалить запись без нарушения истории оплат.",
        "vehicle_remove_confirm": "✅ Да, внесён ошибочно",
        "vehicle_removal_mode_required": "Для продажи или прекращения парковки сначала нужно указать режим парковки. Это необходимо для проверки начислений и оплат. Выберите «Изменить режим парковки» и отправьте отдельное предложение оператору.",
        "vehicle_removal_mode_suggested": "Режим парковки в карточке не указан, но в истории оплат квартиры найдено подтверждение режима {mode}. Заявку можно отправить; оператор сначала подтвердит эту рекомендацию.",
        "vehicle_removal_mode_unknown": "Режим парковки в карточке не указан и финансовой подсказки нет. Сообщение о продаже всё равно будет принято; оператор отдельно сверит расчёты.",
        "other_vehicle_change": "📝 Другое изменение",
        "vehicle_sold": "Автомобиль продан",
        "vehicle_no_longer_parks": "Больше не паркуется",
        "vehicle_plate_prompt": "Введите правильный госномер автомобиля.",
        "vehicle_model_prompt": "Введите правильные марку и модель автомобиля.",
        "vehicle_color_prompt": "Введите правильный цвет автомобиля.",
        "vehicle_parking_prompt": "Выберите правильный режим парковки.",
        "vehicle_stop_reason_prompt": "Укажите причину прекращения парковки.",
        "vehicle_stop_date_prompt": "Введите последний день парковки в формате ГГГГ-ММ-ДД. Эта дата будет проверена оператором перед перерасчётом.",
        "vehicle_stop_date_invalid": "Не удалось распознать дату. Пример: 2026-09-20.",
        "vehicle_stop_date_unknown": "Не помню точную дату",
        "vehicle_other_prompt": "Коротко опишите, что следует изменить. Оператор увидит текущие данные автомобиля.",
        "vehicle_change_saved": "✅ Предложение #{id} отправлено оператору. Данные и начисления пока не изменялись.",
        "request_registered": "✅ Заявка №{id} зарегистрирована в системе. Оператор рассмотрит её при первой возможности. Повторно отправлять это же обращение не нужно.",
        "suggest_new_vehicle_text": "Укажите госномер, марку и всё, что известно об автомобиле.",
        "add_vehicle_possible_correction": "Введённый номер {proposed} отличается всего на один символ от автомобиля {existing}. Возможно, это исправление номера, а не новый автомобиль.",
        "add_vehicle_use_correction": "✏️ Исправить {plate}",
        "add_vehicle_really_new": "➕ Это всё-таки новый автомобиль",
        "suggest_profile_text": "Опишите, какие ваши данные нужно исправить.",
        "suggest_saved": "✅ Предложение отправлено оператору. Данные в реестре пока не изменялись.",
        "suggest_denied": "У вас нет права отправлять предложения для этой квартиры.",
        "confirm_vehicles": "✅ Автомобили указаны верно",
        "confirm_vehicles_saved": "✅ Подтверждение автомобилей отправлено оператору. Реестр не изменялся.",
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
        "link_repeat_prompt": "Введите номер квартиры повторно.",
        "link_repeat_mismatch": "Номера не совпали. Введите номер квартиры ещё раз.",
        "link_not_found": "Квартира не найдена. Проверьте номер и введите ещё раз.",
        "link_group": "Этот номер входит в составную группу. Для привязки обратитесь к оператору.",
        "link_confirm": "Квартира {unit}\n\nОтправить оператору запрос на привязку к этой квартире?\n\nДо проверки данные квартиры и автомобилей не показываются.",
        "linked": "✅ Запрос #{id} на привязку к квартире {unit} принят. Оператор проверит его отдельно.",
        "no_unit": "Квартира пока не привязана. Укажите свою квартиру.",
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
        "choose_menu": "Оберіть кнопку в меню.",
        "home": "🏠 Головне меню",
        "back_portal": "⬅️ До кабінету",
        "my_home": "🏠 Моя квартира",
        "claim_home": "🏠 Вказати свою квартиру",
        "change_home": "✏️ Запросити зміну квартири",
        "my_home_settings": "🏠 Моя квартира\n\nКвартира: {apartment}\n\nЯкщо номер квартири змінився або був указаний помилково, тут можна надіслати запит оператору.",
        "my_vehicles": "🚗 Мої автомобілі",
        "vehicle_resident_none": "Ми поки не знайшли автомобілів, які ви повідомляли раніше. Якщо автомобіль є, додайте його — оператор перевірить дані.",
        "suggest_changes": "✍️ Запропонувати зміну",
        "my_change_requests": "📨 Мої зміни",
        "change_requests_menu_title": "📨 Мої звернення\n\nОберіть, що показати.",
        "change_requests_active": "📨 Очікують",
        "change_requests_history": "🗂️ Історія",
        "reply_operator": "💬 Відповісти оператору",
        "clarification_choose": "Оберіть заявку, щодо якої хочете відповісти оператору.",
        "clarification_reply_prompt": "Напишіть відповідь оператору. Її буде додано до цієї заявки.",
        "clarification_none": "Немає заявок, що очікують на вашу відповідь.",
        "clarification_reply_saved": "✅ Відповідь надіслано оператору. Заявка знову очікує розгляду.",
        "observer": "📖 Спостерігач ОСББ",
        "observer_title": "📖 Спостерігач ОСББ\n\nРежим лише для перегляду. Змінювати дані, заявки та платежі тут неможливо.",
        "observer_summary": "📊 Зведення",
        "observer_apartments": "🏠 Квартири та мешканці",
        "observer_vehicles": "🚗 Усі автомобілі",
        "observer_payments": "💰 Останні оплати",
        "observer_requests": "📨 Заявки мешканців",
        "observer_quality": "🧩 Прогалини в даних",
        "observer_denied": "У вас немає ролі спостерігача ОСББ.",
        "observer_prev": "⬅️ Раніше",
        "observer_next": "➡️ Далі",
        "my_change_requests_title": "📨 Мої пропозиції оператору",
        "my_change_requests_active_title": "📨 Мої звернення в роботі",
        "my_change_requests_history_title": "🗂️ Історія звернень",
        "my_change_requests_none": "Ви ще не надсилали пропозицій щодо своїх даних.",
        "request_status_PENDING": "⏳ очікує розгляду",
        "request_status_IN_PROGRESS": "🔵 у роботі",
        "request_status_NEEDS_CLARIFICATION": "🟡 потрібне уточнення",
        "request_status_RESOLVED": "✅ вирішено",
        "request_status_REJECTED": "❌ відхилено",
        "request_status_CLOSED": "✅ закрито",
        "request_type_RESIDENT_VEHICLE_PARKING_END": "Автомобіль продано / не паркується",
        "request_type_RESIDENT_VEHICLE_REMOVE": "Автомобіль внесено помилково",
        "request_type_RESIDENT_VEHICLE_UPDATE": "Виправлення даних автомобіля",
        "request_type_RESIDENT_VEHICLE_CHANGE": "Зміна даних автомобіля",
        "request_type_RESIDENT_VEHICLE_ADD": "Додавання автомобіля",
        "request_type_RESIDENT_VEHICLES_CONFIRMED": "Підтвердження списку автомобілів",
        "request_type_RESIDENT_PROFILE_CHANGE": "Зміна особистих даних",
        "suggest_vehicle": "🚗 Виправити автомобіль",
        "suggest_add_vehicle": "➕ Додати автомобіль",
        "suggest_profile": "👤 Виправити мої дані",
        "suggest_vehicle_prompt": "Оберіть автомобіль, щодо якого хочете запропонувати виправлення.",
        "suggest_vehicle_text": "Опишіть, що потрібно виправити в цьому автомобілі.",
        "vehicle_change_kind_prompt": "Що потрібно змінити? Спочатку показано дані, які зараз є в реєстрі.",
        "vehicle_current": "Зараз у реєстрі:\nНомер: {plate}\nМарка/модель: {model}\nКолір: {color}\nРежим паркування: {parking}",
        "edit_plate": "🔢 Виправити держномер",
        "edit_model": "🚘 Виправити марку/модель",
        "edit_color": "🎨 Виправити колір",
        "edit_parking_time": "🕒 Змінити режим паркування",
        "stop_parking": "🚫 Продано / не паркується",
        "remove_vehicle": "🗑️ Внесено помилково",
        "vehicle_remove_prompt": "Підтвердьте: цей автомобіль внесено помилково. Оператор перевірить, чи можна видалити запис без порушення історії оплат.",
        "vehicle_remove_confirm": "✅ Так, внесено помилково",
        "vehicle_removal_mode_required": "Для продажу або припинення паркування спочатку потрібно вказати режим паркування. Це необхідно для перевірки нарахувань і оплат. Оберіть «Змінити режим паркування» та надішліть окрему пропозицію оператору.",
        "vehicle_removal_mode_suggested": "У картці режим паркування не вказаний, але в історії оплат квартири знайдено підтвердження режиму {mode}. Заяву можна надіслати; оператор спочатку підтвердить цю рекомендацію.",
        "vehicle_removal_mode_unknown": "Режим паркування в картці не вказаний і фінансової підказки немає. Повідомлення про продаж усе одно буде прийнято; оператор окремо звірить розрахунки.",
        "other_vehicle_change": "📝 Інша зміна",
        "vehicle_sold": "Автомобіль продано",
        "vehicle_no_longer_parks": "Більше не паркується",
        "vehicle_plate_prompt": "Введіть правильний держномер автомобіля.",
        "vehicle_model_prompt": "Введіть правильні марку та модель автомобіля.",
        "vehicle_color_prompt": "Введіть правильний колір автомобіля.",
        "vehicle_parking_prompt": "Оберіть правильний режим паркування.",
        "vehicle_stop_reason_prompt": "Укажіть причину припинення паркування.",
        "vehicle_stop_date_prompt": "Введіть останній день паркування у форматі РРРР-ММ-ДД. Оператор перевірить цю дату перед перерахунком.",
        "vehicle_stop_date_invalid": "Не вдалося розпізнати дату. Приклад: 2026-09-20.",
        "vehicle_stop_date_unknown": "Не пам’ятаю точну дату",
        "vehicle_other_prompt": "Коротко опишіть, що потрібно змінити. Оператор побачить поточні дані автомобіля.",
        "vehicle_change_saved": "✅ Пропозицію #{id} надіслано оператору. Дані та нарахування поки не змінювалися.",
        "request_registered": "✅ Заяву №{id} зареєстровано в системі. Оператор розгляне її за першої можливості. Повторно надсилати це саме звернення не потрібно.",
        "suggest_new_vehicle_text": "Вкажіть держномер, марку та все, що відомо про автомобіль.",
        "add_vehicle_possible_correction": "Введений номер {proposed} відрізняється лише на один символ від автомобіля {existing}. Можливо, це виправлення номера, а не новий автомобіль.",
        "add_vehicle_use_correction": "✏️ Виправити {plate}",
        "add_vehicle_really_new": "➕ Це все ж новий автомобіль",
        "suggest_profile_text": "Опишіть, які ваші дані потрібно виправити.",
        "suggest_saved": "✅ Пропозицію надіслано оператору. Дані в реєстрі поки не змінювалися.",
        "suggest_denied": "У вас немає права надсилати пропозиції для цієї квартири.",
        "confirm_vehicles": "✅ Автомобілі вказано правильно",
        "confirm_vehicles_saved": "✅ Підтвердження автомобілів надіслано оператору. Реєстр не змінювався.",
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
        "link_repeat_prompt": "Введіть номер квартири повторно.",
        "link_repeat_mismatch": "Номери не збіглися. Введіть номер квартири ще раз.",
        "link_not_found": "Квартиру не знайдено. Перевірте номер і введіть ще раз.",
        "link_group": "Цей номер входить до складеної групи. Для прив’язки зверніться до оператора.",
        "link_confirm": "Квартира {unit}\n\nНадіслати оператору запит на прив’язку до цієї квартири?\n\nДо перевірки дані квартири та автомобілів не показуються.",
        "linked": "✅ Запит #{id} на прив’язку до квартири {unit} прийнято. Оператор перевірить його окремо.",
        "no_unit": "Квартиру ще не прив’язано. Вкажіть свою квартиру.",
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
        "choose_menu": "Choose a button from the menu.",
        "home": "🏠 Main menu",
        "back_portal": "⬅️ Back to portal",
        "my_home": "🏠 My apartment",
        "claim_home": "🏠 Specify my apartment",
        "change_home": "✏️ Request apartment change",
        "my_home_settings": "🏠 My apartment\n\nApartment: {apartment}\n\nIf the apartment number has changed or was entered incorrectly, you can send a request to the operator here.",
        "my_vehicles": "🚗 My vehicles",
        "vehicle_resident_none": "We have not found vehicles you reported earlier. If you have a vehicle, add it and an operator will verify the details.",
        "suggest_changes": "✍️ Suggest a change",
        "my_change_requests": "📨 My changes",
        "change_requests_menu_title": "📨 My requests\n\nChoose what to view.",
        "change_requests_active": "📨 Open requests",
        "change_requests_history": "🗂️ History",
        "reply_operator": "💬 Reply to operator",
        "clarification_choose": "Choose the request you want to reply about.",
        "clarification_reply_prompt": "Write your reply to the operator. It will be added to this request.",
        "clarification_none": "There are no requests awaiting your reply.",
        "clarification_reply_saved": "✅ Your reply was sent to the operator. The request is awaiting review again.",
        "observer": "📖 OSBB observer",
        "observer_title": "📖 OSBB observer\n\nRead-only mode. Data, requests, and payments cannot be changed here.",
        "observer_summary": "📊 Summary",
        "observer_apartments": "🏠 Apartments and residents",
        "observer_vehicles": "🚗 All vehicles",
        "observer_payments": "💰 Recent payments",
        "observer_requests": "📨 Resident requests",
        "observer_quality": "🧩 Data gaps",
        "observer_denied": "You do not have the OSBB observer role.",
        "observer_prev": "⬅️ Newer",
        "observer_next": "➡️ Older",
        "my_change_requests_title": "📨 My proposals to the operator",
        "my_change_requests_active_title": "📨 My open requests",
        "my_change_requests_history_title": "🗂️ Request history",
        "my_change_requests_none": "You have not sent any proposals about your details yet.",
        "request_status_PENDING": "⏳ awaiting review",
        "request_status_IN_PROGRESS": "🔵 in progress",
        "request_status_NEEDS_CLARIFICATION": "🟡 clarification required",
        "request_status_RESOLVED": "✅ resolved",
        "request_status_REJECTED": "❌ rejected",
        "request_status_CLOSED": "✅ closed",
        "request_type_RESIDENT_VEHICLE_PARKING_END": "Vehicle sold / no longer parks",
        "request_type_RESIDENT_VEHICLE_REMOVE": "Vehicle entered by mistake",
        "request_type_RESIDENT_VEHICLE_UPDATE": "Vehicle data correction",
        "request_type_RESIDENT_VEHICLE_CHANGE": "Vehicle data change",
        "request_type_RESIDENT_VEHICLE_ADD": "Add a vehicle",
        "request_type_RESIDENT_VEHICLES_CONFIRMED": "Vehicle list confirmation",
        "request_type_RESIDENT_PROFILE_CHANGE": "Personal data change",
        "suggest_vehicle": "🚗 Correct a vehicle",
        "suggest_add_vehicle": "➕ Add a vehicle",
        "suggest_profile": "👤 Correct my details",
        "suggest_vehicle_prompt": "Choose the vehicle you want to correct.",
        "suggest_vehicle_text": "Describe what should be corrected for this vehicle.",
        "vehicle_change_kind_prompt": "What needs to be changed? The current registry data is shown first.",
        "vehicle_current": "Currently in the registry:\nPlate: {plate}\nMake/model: {model}\nColor: {color}\nParking mode: {parking}",
        "edit_plate": "🔢 Correct plate",
        "edit_model": "🚘 Correct make/model",
        "edit_color": "🎨 Correct color",
        "edit_parking_time": "🕒 Change parking mode",
        "stop_parking": "🚫 Sold / no longer parks",
        "remove_vehicle": "🗑️ Entered by mistake",
        "vehicle_remove_prompt": "Confirm that this vehicle was entered by mistake. The operator will check whether it can be removed without breaking payment history.",
        "vehicle_remove_confirm": "✅ Yes, entered by mistake",
        "vehicle_removal_mode_required": "Before reporting a sale or parking end, specify the parking mode. It is required to check charges and payments. Choose “Change parking mode” and submit a separate proposal to the operator.",
        "vehicle_removal_mode_suggested": "The vehicle card has no parking mode, but apartment payment history supports {mode}. You may submit the request; an operator will confirm this recommendation first.",
        "vehicle_removal_mode_unknown": "The vehicle card has no parking mode and there is no financial hint. Your sale report will still be accepted; an operator will reconcile the billing separately.",
        "other_vehicle_change": "📝 Other change",
        "vehicle_sold": "Vehicle sold",
        "vehicle_no_longer_parks": "No longer parks",
        "vehicle_plate_prompt": "Enter the correct licence plate.",
        "vehicle_model_prompt": "Enter the correct make and model.",
        "vehicle_color_prompt": "Enter the correct vehicle color.",
        "vehicle_parking_prompt": "Select the correct parking mode.",
        "vehicle_stop_reason_prompt": "Select why parking is ending.",
        "vehicle_stop_date_prompt": "Enter the last parking day as YYYY-MM-DD. An operator will verify it before recalculation.",
        "vehicle_stop_date_invalid": "The date was not recognised. Example: 2026-09-20.",
        "vehicle_stop_date_unknown": "I do not remember the exact date",
        "vehicle_other_prompt": "Briefly describe what should change. The operator will see the current vehicle data.",
        "vehicle_change_saved": "✅ Proposal #{id} was sent to the operator. Registry data and charges have not changed.",
        "request_registered": "✅ Request #{id} was registered. An operator will review it as soon as possible. Please do not submit the same request again.",
        "suggest_new_vehicle_text": "Enter the plate, model, and everything known about the vehicle.",
        "add_vehicle_possible_correction": "The entered plate {proposed} differs by only one character from {existing}. This may be a correction rather than a new vehicle.",
        "add_vehicle_use_correction": "✏️ Correct {plate}",
        "add_vehicle_really_new": "➕ It is still a new vehicle",
        "suggest_profile_text": "Describe which of your details should be corrected.",
        "suggest_saved": "✅ Your suggestion was sent to the operator. Registry data was not changed.",
        "suggest_denied": "You do not have permission to submit suggestions for this apartment.",
        "confirm_vehicles": "✅ Vehicle details are correct",
        "confirm_vehicles_saved": "✅ Vehicle confirmation was sent to the operator. The registry was not changed.",
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
        "link_repeat_prompt": "Enter the apartment number again.",
        "link_repeat_mismatch": "The apartment numbers do not match. Enter the number again.",
        "link_not_found": "Apartment not found. Check the number and try again.",
        "link_group": "This number belongs to a combined group. Please contact the operator for linking.",
        "link_confirm": "Apartment {unit}\n\nSend the operator a request to link this apartment?\n\nApartment and vehicle details are not shown before verification.",
        "linked": "✅ Request #{id} to link apartment {unit} has been received. The operator will verify it separately.",
        "no_unit": "No apartment is linked yet. Specify your apartment.",
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


def client_menu_keyboard(
    lang: str, *, can_propose_changes: bool = False, can_observe: bool = False
) -> list[list[str]]:
    rows = [
        [tr(lang, "my_home")],
        [tr(lang, "my_vehicles")],
        [tr(lang, "parking"), tr(lang, "remotes")],
        [tr(lang, "phone")],
        [tr(lang, "improve")],
        [tr(lang, "news"), tr(lang, "contacts")],
        [tr(lang, "admin")],
    ]
    if can_propose_changes:
        rows.insert(3, [tr(lang, "suggest_changes"), tr(lang, "my_change_requests")])
    if can_observe:
        # A separate entry point makes the privilege visible without mixing it
        # with a resident's own data or any edit workflow.
        rows.insert(-1, [tr(lang, "observer")])
    return rows


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
                car_color_normalized,
                car_color,
                parking_time,
                source,
                created_source
            FROM vehicles
            WHERE apartment_id = ?
              -- An archived vehicle is financial/history evidence, not a
              -- current vehicle a resident may edit or report as sold again.
              AND COALESCE(lifecycle_status, 'ACTIVE') = 'ACTIVE'
            ORDER BY id
        """, (int(unit_id),))
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


# These are sources in which the resident (or their paper questionnaire) gave
# the data. Import queues, video evidence and operator-only discoveries are
# deliberately excluded from the resident-facing view.
RESIDENT_DECLARED_VEHICLE_SOURCES = {"paper_parking", "tbot_parking", "resident_portal"}


def _resident_known_vehicles_for_unit(unit_id: int) -> list[dict]:
    return [
        vehicle for vehicle in _vehicles_for_unit(unit_id)
        if text(vehicle.get("source")).lower() in RESIDENT_DECLARED_VEHICLE_SOURCES
        or text(vehicle.get("created_source")).lower().startswith("resident_portal")
    ]


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


def _vehicle_snapshot(vehicle: dict, lang: str) -> dict[str, str]:
    """Return the visible current values stored with a resident proposal."""
    return {
        "plate": text(vehicle.get("license_plate_normalized")) or text(vehicle.get("license_plate")) or "-",
        "model": text(vehicle.get("car_model_normalized")) or text(vehicle.get("car_model")) or "-",
        "color": text(vehicle.get("car_color_normalized")) or text(vehicle.get("car_color")) or "-",
        "parking": text(vehicle.get("parking_time")) or tr(lang, "parking_unknown"),
    }


def _plate_from_resident_text(value: str) -> str | None:
    """Extract one ordinary UA plate from a free-text add-vehicle proposal."""
    for candidate in re.findall(r"(?i)[A-ZА-ЯІЇЄҐ]{2}\s*\d{4}\s*[A-ZА-ЯІЇЄҐ]{2}", value or ""):
        normalized, status = _registry_normalize_plate(candidate)
        if normalized and status == "STANDARD":
            return normalized
    normalized, status = _registry_normalize_plate(value)
    return normalized if normalized and status == "STANDARD" else None


def _plate_distance(left: str, right: str) -> int:
    """Tiny Levenshtein implementation for a one-character typo warning."""
    if len(left) < len(right):
        left, right = right, left
    previous = list(range(len(right) + 1))
    for left_index, left_char in enumerate(left, start=1):
        current = [left_index]
        for right_index, right_char in enumerate(right, start=1):
            current.append(min(
                current[-1] + 1,
                previous[right_index] + 1,
                previous[right_index - 1] + (left_char != right_char),
            ))
        previous = current
    return previous[-1]


def _self_service_allowed(user_id: int, unit: dict, resource: str) -> bool:
    apartment = text(unit.get("apartment_number"))
    return bool(apartment) and has_permission(
        user_id,
        resource,
        "CREATE",
        scope_type="APARTMENT",
        scope_value=apartment,
    )


def _parking_mode_payment_suggestion(unit: dict) -> dict | None:
    """Return one unambiguous Day/Night signal from apartment payment metadata.

    Payments remain apartment-level facts and are never allocated to a car here.
    This is only a yellow operator recommendation for a missing parking mode.
    """
    apartment = text(unit.get("apartment_number"))
    if not apartment:
        return None
    conn = get_conn()
    try:
        cur = conn.cursor()
        if not table_exists(cur, "payments"):
            return None
        columns = table_columns(cur, "payments")
        if "apartment_number" not in columns:
            return None
        fields = [field for field in ("service_type", "base_service_code", "service_item_code", "period_code") if field in columns]
        if not fields:
            return None
        cur.execute(
            f"SELECT {', '.join(fields)} FROM payments WHERE apartment_number=?", (apartment,)
        )
        modes: list[str] = []
        periods: set[str] = set()
        for row in cur.fetchall():
            values = " ".join(text(row[field]) for field in fields if field != "period_code").upper()
            mode = "Night" if "NIGHT" in values else "Day" if "DAY" in values else ""
            if mode:
                modes.append(mode)
                if "period_code" in fields and text(row["period_code"]):
                    periods.add(text(row["period_code"]))
        if not modes or len(set(modes)) != 1:
            return None
        return {"mode": modes[0], "payment_count": len(modes), "periods": sorted(periods),
                "source": "apartment_payment_service_type"}
    finally:
        conn.close()


def _observer_allowed(user_id: int) -> bool:
    """Whether this account may open the OSBB-wide read-only desk."""
    return has_permission(user_id, "reports", "VIEW", scope_type="ALL", scope_value="*")


def _observer_labels(lang: str) -> dict[str, str]:
    """Small labels kept outside the data so the screen stays language-aware."""
    return {
        "ru": {
            "apartments": "квартир", "residents": "подтверждённых жителей",
            "vehicles": "активных автомобилей", "payments": "оплат", "total": "сумма оплат",
            "open": "открытых заявок жителей", "empty": "Записей пока нет.",
            "page": "Страница {page}", "unknown": "не указано",
        },
        "uk": {
            "apartments": "квартир", "residents": "підтверджених мешканців",
            "vehicles": "активних автомобілів", "payments": "оплат", "total": "сума оплат",
            "open": "відкритих заявок мешканців", "empty": "Записів поки немає.",
            "page": "Сторінка {page}", "unknown": "не вказано",
        },
        "en": {
            "apartments": "apartments", "residents": "confirmed residents",
            "vehicles": "active vehicles", "payments": "payments", "total": "payment total",
            "open": "open resident requests", "empty": "No records yet.",
            "page": "Page {page}", "unknown": "not specified",
        },
    }.get(lang, {})


def _observer_data(kind: str, page: int, lang: str = "ru", apartment_filter: str = "") -> tuple[str, bool]:
    """Read-only OSBB-wide report pages.  No write statement belongs here."""
    page = max(0, page)
    limit, offset = 15, page * 15
    conn = get_conn()
    try:
        cur = conn.cursor()
        if kind == "quality":
            issues = build_quality_issues(conn)
            if apartment_filter:
                issues = [item for item in issues if item["apartment"] == apartment_filter]
            summary = quality_summary(issues)
            rows = issues[offset:offset + limit + 1]
            more, rows = len(rows) > limit, rows[:limit]
            headings = {
                "ru": ("Пробелов", "затронуто записей", "кв.", "критично", "важно", "дополнить"),
                "uk": ("Прогалин", "записів потребують уваги", "кв.", "критично", "важливо", "доповнити"),
                "en": ("Gaps", "affected records", "apt.", "critical", "important", "complete"),
            }.get(lang, ("Пробелов", "затронуто записей", "кв.", "критично", "важно", "дополнить"))
            fields = {
                "uk": {"Квартира": "Квартира", "Госномер": "Держномер", "Госномер требует проверки": "Держномер потребує перевірки", "Марка/модель": "Марка/модель", "Режим парковки": "Режим паркування", "Цвет": "Колір", "Площадь": "Площа", "ФИО жителя/собственника": "ПІБ мешканця/власника", "Контакт": "Контакт"},
                "en": {"Квартира": "Apartment", "Госномер": "Plate", "Госномер требует проверки": "Plate needs review", "Марка/модель": "Make/model", "Режим парковки": "Parking mode", "Цвет": "Color", "Площадь": "Area", "ФИО жителя/собственника": "Resident/owner name", "Контакт": "Contact"},
            }.get(lang, {})
            levels = {"Критично": headings[3], "Важно": headings[4], "Дополнить": headings[5]}
            body = [f"{headings[0]}: {summary['issues']}; {headings[1]}: {summary['records']}."] if page == 0 else []
            body.extend(
                f"• {headings[2]}{row['apartment']} | {row['plate']} | {fields.get(row['field'], row['field'])} "
                f"({levels[row['severity']]}; #{row['object_id']})"
                for row in rows
            )
            return "\n".join(body), more
        if kind == "summary":
            cur.execute("""
                SELECT COUNT(*) FROM apartments
                WHERE COALESCE(unit_type, 'RESIDENTIAL') = 'RESIDENTIAL'
                  AND COALESCE(record_status, '') <> 'TEST'
            """)
            apartments = int(cur.fetchone()[0] or 0)
            cur.execute("SELECT COUNT(*) FROM resident_accounts WHERE status='apartment_confirmed'")
            residents = int(cur.fetchone()[0] or 0)
            cur.execute("""
                SELECT COUNT(*) FROM vehicles v JOIN apartments a ON a.id=v.apartment_id
                WHERE COALESCE(v.lifecycle_status, 'ACTIVE')='ACTIVE'
                  AND COALESCE(a.unit_type, 'RESIDENTIAL') = 'RESIDENTIAL'
                  AND COALESCE(a.record_status, '') <> 'TEST'
            """)
            vehicles = int(cur.fetchone()[0] or 0)
            cur.execute("SELECT COUNT(*), COALESCE(SUM(amount), 0) FROM payments")
            payments, payment_total = cur.fetchone()
            cur.execute("""
                SELECT COUNT(*) FROM operator_task_queue
                WHERE origin='RESIDENT_PORTAL' AND status IN ('PENDING', 'IN_PROGRESS')
            """)
            open_requests = int(cur.fetchone()[0] or 0)
            return (f"{apartments}|{residents}|{vehicles}|{int(payments or 0)}|{money(payment_total)}|{open_requests}", False)

        if kind == "apartments":
            cur.execute("""
                SELECT a.apartment_number,
                       GROUP_CONCAT(TRIM(COALESCE(r.telegram_first_name,'') || ' ' || COALESCE(r.telegram_last_name,'')), ', ') AS people,
                       COUNT(DISTINCT v.id) AS vehicles
                FROM apartments a
                LEFT JOIN resident_accounts r ON r.apartment_id=a.id AND r.status='apartment_confirmed'
                LEFT JOIN vehicles v ON v.apartment_id=a.id AND COALESCE(v.lifecycle_status,'ACTIVE')='ACTIVE'
                WHERE COALESCE(a.unit_type, 'RESIDENTIAL') = 'RESIDENTIAL'
                  AND COALESCE(a.record_status, '') <> 'TEST'
                GROUP BY a.id
                ORDER BY CASE WHEN a.apartment_number GLOB '[0-9]*' THEN 0 ELSE 1 END,
                         CAST(a.apartment_number AS INTEGER), a.apartment_number
                LIMIT ? OFFSET ?
            """, (limit + 1, offset))
            rows = cur.fetchall()
            more, rows = len(rows) > limit, rows[:limit]
            return ("\n".join(
                f"• кв.{text(row['apartment_number']) or '—'} — {text(row['people']) or '—'}; 🚗 {row['vehicles']}"
                for row in rows
            ), more)

        if kind == "vehicles":
            cur.execute("""
                SELECT a.apartment_number, v.license_plate_normalized, v.license_plate,
                       v.car_model_normalized, v.car_model, v.parking_time
                FROM vehicles v JOIN apartments a ON a.id=v.apartment_id
                WHERE COALESCE(v.lifecycle_status,'ACTIVE')='ACTIVE'
                  AND COALESCE(a.unit_type, 'RESIDENTIAL') = 'RESIDENTIAL'
                  AND COALESCE(a.record_status, '') <> 'TEST'
                ORDER BY a.apartment_number, COALESCE(v.license_plate_normalized, v.license_plate), v.id
                LIMIT ? OFFSET ?
            """, (limit + 1, offset))
            rows = cur.fetchall()
            more, rows = len(rows) > limit, rows[:limit]
            return ("\n".join(
                f"• кв.{text(row['apartment_number']) or '—'} | "
                f"{text(row['license_plate_normalized']) or text(row['license_plate']) or '—'} | "
                f"{text(row['car_model_normalized']) or text(row['car_model']) or '—'} | "
                f"{text(row['parking_time']) or '—'}"
                for row in rows
            ), more)

        if kind == "payments":
            cur.execute("""
                SELECT payment_date, created_at, apartment_number, amount, currency, payment_method, service_type
                FROM payments
                ORDER BY COALESCE(payment_date, created_at) DESC, id DESC
                LIMIT ? OFFSET ?
            """, (limit + 1, offset))
            rows = cur.fetchall()
            more, rows = len(rows) > limit, rows[:limit]
            return ("\n".join(
                f"• {text(row['payment_date']) or text(row['created_at'])[:10]} | кв.{text(row['apartment_number']) or '—'} | "
                f"{money(row['amount'])} {text(row['currency']) or 'UAH'} | "
                f"{text(row['service_type']) or text(row['payment_method']) or '—'}"
                for row in rows
            ), more)

        cur.execute("""
            SELECT id, apartment_number, task_type, status, plate, title, created_at
            FROM operator_task_queue
            WHERE origin='RESIDENT_PORTAL'
            ORDER BY created_at DESC, id DESC
            LIMIT ? OFFSET ?
        """, (limit + 1, offset))
        rows = cur.fetchall()
        more, rows = len(rows) > limit, rows[:limit]
        return ("\n".join(
            f"• #{row['id']} | кв.{text(row['apartment_number']) or '—'} | {text(row['status'])} | "
            f"{text(row['plate']) or text(row['task_type']) or '—'}"
            for row in rows
        ), more)
    finally:
        conn.close()


async def _show_observer_screen(
    update: Update, user_states: dict, user_id: int, lang: str, kind: str = "summary", page: int = 0,
    apartment_filter: str = "",
) -> None:
    if not _observer_allowed(user_id):
        await update.message.reply_text(tr(lang, "observer_denied"))
        await show_client_portal(update, user_states, user_id, lang)
        return
    state = _portal_state(user_states, user_id, create=True)
    state["mode"] = "observer"
    state["observer_kind"] = kind
    state["observer_page"] = max(0, page)
    state["observer_apartment_filter"] = apartment_filter if kind == "quality" else ""
    labels = _observer_labels(lang)
    raw, more = _observer_data(kind, page, lang, apartment_filter)
    if kind == "summary":
        apartments, residents, vehicles, payments, total, open_requests = raw.split("|")
        body = "\n".join([
            f"🏠 {apartments} {labels['apartments']}",
            f"👤 {residents} {labels['residents']}",
            f"🚗 {vehicles} {labels['vehicles']}",
            f"💰 {payments} {labels['payments']}; {labels['total']}: {total} UAH",
            f"📨 {open_requests} {labels['open']}",
        ])
    else:
        title = {
            "apartments": tr(lang, "observer_apartments"),
            "vehicles": tr(lang, "observer_vehicles"),
            "payments": tr(lang, "observer_payments"),
            "requests": tr(lang, "observer_requests"),
            "quality": tr(lang, "observer_quality"),
        }[kind]
        search_hint = (
            "\n\nЧтобы найти квартиру, отправьте её номер, например 160."
            if kind == "quality" and lang == "ru" else
            "\n\nЩоб знайти квартиру, надішліть її номер, наприклад 160."
            if kind == "quality" and lang == "uk" else
            "\n\nTo find an apartment, send its number, e.g. 160."
            if kind == "quality" else ""
        )
        scope = f" · {apartment_filter}" if apartment_filter else ""
        body = f"{title}{scope}\n\n{raw or labels['empty']}\n\n{labels['page'].format(page=page + 1)}{search_hint}"
    nav: list[str] = []
    if page > 0:
        nav.append(tr(lang, "observer_prev"))
    if more:
        nav.append(tr(lang, "observer_next"))
    buttons = [
        [tr(lang, "observer_summary")],
        [tr(lang, "observer_apartments"), tr(lang, "observer_vehicles")],
        [tr(lang, "observer_quality")],
        [tr(lang, "observer_payments"), tr(lang, "observer_requests")],
    ]
    if nav:
        buttons.append(nav)
    buttons.append([tr(lang, "back_portal")])
    await update.message.reply_text(
        tr(lang, "observer_title") + "\n\n" + body,
        reply_markup=kb(buttons),
    )


def _create_resident_change_task(
    *,
    user_id: int,
    unit: dict,
    task_type: str,
    title: str,
    description: str,
    vehicle: dict | None = None,
    payload: dict | None = None,
) -> int:
    """Queue a resident proposal; no registry data is changed here."""
    conn = get_conn()
    try:
        cur = conn.cursor()
        if not table_exists(cur, "operator_task_queue"):
            raise RuntimeError("Очередь предложений оператора ещё не подключена.")
        columns = [
            "priority", "task_type", "status", "apartment_number", "vehicle_id", "plate",
            "telegram_user_id", "title", "description", "origin", "created_by", "created_at", "updated_at",
        ]
        values: list[Any] = [
            "NORMAL", task_type, "PENDING", text(unit.get("apartment_number")),
            int(vehicle["id"]) if vehicle else None,
            (text(vehicle.get("license_plate_normalized")) or text(vehicle.get("license_plate"))) if vehicle else None,
            str(user_id), title, description.strip(), "RESIDENT_PORTAL", str(user_id), now_db(), now_db(),
        ]
        # The portal remains compatible until migration 012 is applied, while
        # storing a machine-readable proposal as soon as the column exists.
        if "payload_json" in table_columns(cur, "operator_task_queue"):
            columns.append("payload_json")
            values.append(json.dumps(payload or {}, ensure_ascii=False, sort_keys=True))
        placeholders = ", ".join("?" for _ in columns)
        cur.execute(
            f"INSERT INTO operator_task_queue({', '.join(columns)}) VALUES ({placeholders})",
            tuple(values),
        )
        task_id = int(cur.lastrowid)
        if audit_log:
            audit_log(
                conn=conn,
                operator_id=str(user_id),
                user_id=str(user_id),
                actor_type="resident",
                action_type="resident_change_proposal_created",
                table_name="operator_task_queue",
                row_id=task_id,
                field_name="task_type,status",
                old_value="",
                new_value=f"{task_type},PENDING",
                source_context="client_portal",
                comment="Житель предложил изменение; прямого изменения реестра не было.",
                commit=False,
            )
        conn.commit()
        return task_id
    finally:
        conn.close()


def _resident_change_tasks(user_id: int, *, history: bool | None = None) -> list[dict]:
    """Return only the current resident's proposals, split into open/history."""
    conn = get_conn()
    try:
        cur = conn.cursor()
        if not table_exists(cur, "operator_task_queue"):
            return []
        status_clause = ""
        params: list[str] = [str(user_id)]
        if history is True:
            status_clause = " AND status IN ('RESOLVED', 'REJECTED', 'CLOSED')"
        elif history is False:
            status_clause = " AND status IN ('PENDING', 'IN_PROGRESS', 'NEEDS_CLARIFICATION')"
        cur.execute(
            f"""
            SELECT id, task_type, status, vehicle_id, title, description, created_at, updated_at, close_note
            FROM operator_task_queue
            WHERE telegram_user_id=? AND origin='RESIDENT_PORTAL'
            {status_clause}
            ORDER BY id DESC
            """,
            tuple(params),
        )
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def _clarification_tasks(user_id: int) -> list[dict]:
    """Open resident tasks for which an operator has asked a question."""
    conn = get_conn()
    try:
        cur = conn.cursor()
        if not table_exists(cur, "resident_request_messages"):
            return []
        cur.execute(
            """
            SELECT q.id, q.title, q.vehicle_id, q.plate, q.close_note, q.updated_at
            FROM operator_task_queue q
            WHERE q.telegram_user_id=? AND q.origin='RESIDENT_PORTAL'
              AND q.status='NEEDS_CLARIFICATION'
            ORDER BY q.updated_at DESC, q.id DESC
            """, (str(user_id),),
        )
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def _save_clarification_reply(user_id: int, request_id: int, message: str) -> None:
    reply = text(message)
    if not reply:
        raise ValueError("Ответ не может быть пустым.")
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        task = cur.execute(
            """
            SELECT * FROM operator_task_queue
            WHERE id=? AND telegram_user_id=? AND origin='RESIDENT_PORTAL'
            """, (int(request_id), str(user_id)),
        ).fetchone()
        if not task or task["status"] != "NEEDS_CLARIFICATION":
            raise ValueError("Заявка уже не ожидает уточнения.")
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cur.execute(
            """
            INSERT INTO resident_request_messages(
                request_id, telegram_user_id, direction, message_text, delivery_status, created_by, created_at, read_at
            ) VALUES (?, ?, 'RESIDENT_TO_OPERATOR', ?, 'READ', ?, ?, ?)
            """, (int(request_id), str(user_id), reply, str(user_id), timestamp, timestamp),
        )
        cur.execute(
            """
            UPDATE resident_request_messages SET delivery_status='READ', read_at=?
            WHERE request_id=? AND direction='OPERATOR_TO_RESIDENT' AND delivery_status='SENT'
            """, (timestamp, int(request_id)),
        )
        cur.execute(
            """
            UPDATE operator_task_queue
            SET status='PENDING', assigned_to=NULL, updated_at=?, close_note=? WHERE id=?
            """, (timestamp, "Житель ответил на вопрос оператора; заявка возвращена в очередь.", int(request_id)),
        )
        if audit_log:
            audit_log(
                conn=conn, operator_id=str(user_id), user_id=str(user_id), actor_type="resident",
                action_type="resident_clarification_reply", table_name="operator_task_queue", row_id=int(request_id),
                field_name="status", old_value="NEEDS_CLARIFICATION", new_value="PENDING",
                source_context="client_portal", comment=reply, commit=False,
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _format_resident_change_tasks(rows: list[dict], lang: str, *, history: bool | None = None) -> str:
    if not rows:
        return tr(lang, "my_change_requests_none")
    title_key = (
        "my_change_requests_history_title" if history is True
        else "my_change_requests_active_title" if history is False
        else "my_change_requests_title"
    )
    lines = [tr(lang, title_key), ""]
    for row in rows:
        status_key = f"request_status_{text(row.get('status')).upper()}"
        status = I18N[lang].get(status_key, text(row.get("status")) or "—")
        task_type = text(row.get("task_type")).upper()
        if task_type == "RESIDENT_VEHICLE_CHANGE" and not row.get("vehicle_id"):
            task_type = "RESIDENT_VEHICLE_ADD"
        type_key = f"request_type_{task_type}"
        title = I18N[lang].get(type_key, text(row.get("title")) or "—")
        lines.extend([
            f"#{row['id']} · {status}",
            title,
            f"Відправлено: {text(row.get('created_at')) or '—'}",
        ])
        if text(row.get("close_note")):
            lines.append(f"Відповідь оператора: {text(row['close_note'])}")
        lines.append("")
    return "\n".join(lines).rstrip()


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
            reply_markup=kb([[tr(lang, "claim_home")], [tr(lang, "home")]]),
        )
        return

    billing = _billing_data(data["unit"])
    state = _portal_state(user_states, user_id, create=True)
    state["mode"] = "client_home"
    can_propose_changes = (
        _self_service_allowed(user_id, data["unit"], "vehicle_change_requests")
        or _self_service_allowed(user_id, data["unit"], "resident_profile_change_requests")
    )
    can_observe = _observer_allowed(user_id)
    
    # Отправляем приветствие + основную информацию
    await update.message.reply_text(
        f"{welcome_message}\n\n{_format_dashboard(data, billing, lang)}",
        reply_markup=kb(client_menu_keyboard(
            lang, can_propose_changes=can_propose_changes, can_observe=can_observe
        )),
    )


async def show_my_home_settings(update: Update, user_states: dict, user_id: int, lang: str) -> None:
    """Keep the exceptional apartment-change action out of the main menu."""
    data = _account_and_unit(user_id)
    if not data or not data.get("unit"):
        await show_client_portal(update, user_states, user_id, lang)
        return
    apartment = text(data["unit"].get("apartment_number")) or "—"
    state = _portal_state(user_states, user_id, create=True)
    state["mode"] = "client_my_home"
    await update.message.reply_text(
        tr(lang, "my_home_settings", apartment=apartment),
        reply_markup=kb([[tr(lang, "change_home")], [tr(lang, "back_portal")]]),
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
    vehicles = _resident_known_vehicles_for_unit(int(data["unit"]["id"]))
    state = _portal_state(user_states, user_id, create=True)
    state["mode"] = "client_vehicles"
    rows = []
    if _self_service_allowed(user_id, data["unit"], "vehicle_change_requests"):
        rows.append([tr(lang, "confirm_vehicles")])
        rows.append([tr(lang, "suggest_changes")])
    rows.extend([[tr(lang, "back_portal")], [tr(lang, "home")]])
    await update.message.reply_text(
        f"{tr(lang, 'vehicle_title', unit=unit_code)}\n\n"
        f"{_format_vehicles(vehicles, lang) if vehicles else tr(lang, 'vehicle_resident_none')}",
        reply_markup=kb(rows),
    )


async def _show_my_change_request_menu(update: Update, user_states: dict, user_id: int, lang: str) -> None:
    state = _portal_state(user_states, user_id, create=True)
    state["mode"] = "client_change_requests"
    await update.message.reply_text(
        tr(lang, "change_requests_menu_title"),
        reply_markup=kb([
            [tr(lang, "change_requests_active"), tr(lang, "change_requests_history")],
            [tr(lang, "reply_operator")],
            [tr(lang, "back_portal")], [tr(lang, "home")],
        ]),
    )


async def _show_my_change_requests(
    update: Update, user_states: dict, user_id: int, lang: str, *, history: bool
) -> None:
    state = _portal_state(user_states, user_id, create=True)
    state["mode"] = "client_change_requests"
    await update.message.reply_text(
        _format_resident_change_tasks(_resident_change_tasks(user_id, history=history), lang, history=history),
        reply_markup=kb([
            [tr(lang, "change_requests_active"), tr(lang, "change_requests_history")],
            [tr(lang, "reply_operator")],
            [tr(lang, "back_portal")], [tr(lang, "home")],
        ]),
    )


async def _show_resident_vehicle_change_menu(
    update: Update, user_states: dict, vehicle: dict, lang: str
) -> None:
    """Show a small structured editor, shared later with the operator UI."""
    snapshot = _vehicle_snapshot(vehicle, lang)
    state = _portal_state(user_states, update.effective_user.id, create=True)
    state["mode"] = "resident_vehicle_change_kind"
    state["resident_change_vehicle_id"] = int(vehicle["id"])
    await update.message.reply_text(
        tr(lang, "vehicle_current", **snapshot) + "\n\n" + tr(lang, "vehicle_change_kind_prompt"),
        reply_markup=kb([
            [tr(lang, "edit_plate"), tr(lang, "edit_model")],
            [tr(lang, "edit_color"), tr(lang, "edit_parking_time")],
            [tr(lang, "stop_parking")],
            [tr(lang, "remove_vehicle")],
            [tr(lang, "back_portal")],
        ]),
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
        tr(lang, "claim_home"): "change",
        tr(lang, "change_home"): "change",
        tr(lang, "my_vehicles"): "vehicles",
        tr(lang, "suggest_changes"): "suggest_changes",
        tr(lang, "my_change_requests"): "my_change_requests",
        tr(lang, "observer"): "observer",
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
            await show_my_home_settings(update, user_states, user_id, lang)
        elif action == "change":
            await _ask_portal_unit(update, user_states, user_id, lang)
        elif action == "vehicles":
            await _show_vehicle_list(update, user_states, user_id, lang)
        elif action == "suggest_changes":
            data = _account_and_unit(user_id)
            if not data or not data.get("unit"):
                await show_client_portal(update, user_states, user_id, lang)
            elif not (
                _self_service_allowed(user_id, data["unit"], "vehicle_change_requests")
                or _self_service_allowed(user_id, data["unit"], "resident_profile_change_requests")
            ):
                await update.message.reply_text(tr(lang, "suggest_denied"))
            else:
                state["mode"] = "resident_change_menu"
                await update.message.reply_text(
                    tr(lang, "choose_menu"),
                    reply_markup=kb([
                        [tr(lang, "suggest_vehicle")],
                        [tr(lang, "suggest_add_vehicle")],
                        [tr(lang, "suggest_profile")],
                        [tr(lang, "back_portal")],
                    ]),
                )
        elif action == "my_change_requests":
            data = _account_and_unit(user_id)
            if not data or not data.get("unit"):
                await show_client_portal(update, user_states, user_id, lang)
            elif not (
                _self_service_allowed(user_id, data["unit"], "vehicle_change_requests")
                or _self_service_allowed(user_id, data["unit"], "resident_profile_change_requests")
            ):
                await update.message.reply_text(tr(lang, "suggest_denied"))
            else:
                await _show_my_change_request_menu(update, user_states, user_id, lang)
        elif action == "observer":
            await _show_observer_screen(update, user_states, user_id, lang)
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

    # OSBB-wide desk: it deliberately has no mutation branches.  Permission is
    # rechecked on every click in case the observer role was revoked mid-session.
    if current == "observer":
        if not _observer_allowed(user_id):
            await update.message.reply_text(tr(lang, "observer_denied"))
            await show_client_portal(update, user_states, user_id, lang)
            return True
        kind_by_button = {
            tr(lang, "observer_summary"): "summary",
            tr(lang, "observer_apartments"): "apartments",
            tr(lang, "observer_vehicles"): "vehicles",
            tr(lang, "observer_payments"): "payments",
            tr(lang, "observer_requests"): "requests",
            tr(lang, "observer_quality"): "quality",
        }
        if message_text in kind_by_button:
            await _show_observer_screen(update, user_states, user_id, lang, kind_by_button[message_text])
            return True
        page = int(state.get("observer_page") or 0)
        kind = text(state.get("observer_kind")) or "summary"
        apartment_filter = text(state.get("observer_apartment_filter")) if kind == "quality" else ""
        if kind == "quality":
            apartment_match = re.fullmatch(r"(?:кв\.?\s*)?(\d+[A-Za-zА-Яа-яІЇЄҐіїєґ]?)", message_text.strip(), re.IGNORECASE)
            if apartment_match:
                await _show_observer_screen(update, user_states, user_id, lang, kind, 0, apartment_match.group(1))
                return True
        if message_text == tr(lang, "observer_prev"):
            await _show_observer_screen(update, user_states, user_id, lang, kind, max(0, page - 1), apartment_filter)
            return True
        if message_text == tr(lang, "observer_next"):
            await _show_observer_screen(update, user_states, user_id, lang, kind, page + 1, apartment_filter)
            return True
        await _show_observer_screen(update, user_states, user_id, lang, kind, page, apartment_filter)
        return True

    # Link apartment.
    if current == "portal_unlinked":
        if message_text == tr(lang, "claim_home"):
            await _ask_portal_unit(update, user_states, user_id, lang)
        else:
            await update.message.reply_text(tr(lang, "choose_menu"), reply_markup=kb([[tr(lang, "claim_home")], [tr(lang, "home")]]))
        return True

    if current == "portal_wait_unit":
        unit = _find_exact_physical_unit(message_text)
        if not unit:
            await update.message.reply_text(tr(lang, "link_not_found"))
            return True
        if text(unit.get("unit_type")) and text(unit.get("unit_type")) != "RESIDENTIAL":
            await update.message.reply_text(tr(lang, "link_group"))
            return True
        state["mode"] = "portal_repeat_unit"
        state["first_unit_id"] = int(unit["id"])
        await update.message.reply_text(
            tr(lang, "link_repeat_prompt"),
            reply_markup=kb([[tr(lang, "back_portal")], [tr(lang, "home")]]),
        )
        return True

    if current == "portal_repeat_unit":
        unit = _find_exact_physical_unit(message_text)
        if not unit or int(unit["id"]) != int(state.get("first_unit_id") or 0):
            await update.message.reply_text(tr(lang, "link_repeat_mismatch"))
            await _ask_portal_unit(update, user_states, user_id, lang)
            return True
        state.pop("first_unit_id", None)
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

    if current == "client_change_requests":
        if message_text == tr(lang, "change_requests_active"):
            await _show_my_change_requests(update, user_states, user_id, lang, history=False)
            return True
        if message_text == tr(lang, "change_requests_history"):
            await _show_my_change_requests(update, user_states, user_id, lang, history=True)
            return True
        if message_text == tr(lang, "reply_operator"):
            tasks = _clarification_tasks(user_id)
            if not tasks:
                await update.message.reply_text(tr(lang, "clarification_none"))
                return True
            state["mode"] = "resident_clarification_choose"
            state["clarification_tasks"] = {
                f"📨 #{row['id']} · {text(row.get('plate')) or text(row.get('title')) or 'заявка'}": int(row["id"])
                for row in tasks
            }
            await update.message.reply_text(
                tr(lang, "clarification_choose"),
                reply_markup=kb([[label] for label in state["clarification_tasks"]] + [[tr(lang, "back_portal")]]),
            )
            return True
        await _show_my_change_request_menu(update, user_states, user_id, lang)
        return True

    if current == "resident_clarification_choose":
        request_id = (state.get("clarification_tasks") or {}).get(message_text)
        if not request_id:
            await update.message.reply_text(tr(lang, "clarification_choose"))
            return True
        state["clarification_request_id"] = int(request_id)
        state["mode"] = "resident_clarification_reply"
        await update.message.reply_text(tr(lang, "clarification_reply_prompt"), reply_markup=kb([[tr(lang, "back_portal")]]))
        return True

    if current == "resident_clarification_reply":
        try:
            _save_clarification_reply(user_id, int(state.get("clarification_request_id") or 0), message_text)
        except ValueError as exc:
            await update.message.reply_text(str(exc))
            return True
        state.pop("clarification_request_id", None)
        state.pop("clarification_tasks", None)
        await update.message.reply_text(tr(lang, "clarification_reply_saved"))
        await _show_my_change_request_menu(update, user_states, user_id, lang)
        return True

    if current == "resident_change_menu":
        data = _account_and_unit(user_id)
        if not data or not data.get("unit"):
            await show_client_portal(update, user_states, user_id, lang)
            return True
        if message_text == tr(lang, "suggest_vehicle"):
            if not _self_service_allowed(user_id, data["unit"], "vehicle_change_requests"):
                await update.message.reply_text(tr(lang, "suggest_denied"))
                return True
            vehicles = _resident_known_vehicles_for_unit(int(data["unit"]["id"]))
            if not vehicles:
                await update.message.reply_text(tr(lang, "vehicle_none"))
                return True
            state["mode"] = "resident_change_choose_vehicle"
            state["resident_change_vehicles"] = {
                f"🚗 #{row['id']} {text(row.get('license_plate_normalized')) or text(row.get('license_plate')) or '-'}": int(row["id"])
                for row in vehicles
            }
            await update.message.reply_text(
                tr(lang, "suggest_vehicle_prompt"),
                reply_markup=kb([[label] for label in state["resident_change_vehicles"]] + [[tr(lang, "back_portal")]]),
            )
            return True
        if message_text == tr(lang, "suggest_add_vehicle"):
            if not _self_service_allowed(user_id, data["unit"], "vehicle_change_requests"):
                await update.message.reply_text(tr(lang, "suggest_denied"))
                return True
            state["mode"] = "resident_change_vehicle_text"
            state["resident_change_vehicle_id"] = None
            await update.message.reply_text(tr(lang, "suggest_new_vehicle_text"), reply_markup=kb([[tr(lang, "back_portal")]]))
            return True
        if message_text == tr(lang, "suggest_profile"):
            if not _self_service_allowed(user_id, data["unit"], "resident_profile_change_requests"):
                await update.message.reply_text(tr(lang, "suggest_denied"))
                return True
            state["mode"] = "resident_change_profile_text"
            await update.message.reply_text(tr(lang, "suggest_profile_text"), reply_markup=kb([[tr(lang, "back_portal")]]))
            return True
        await update.message.reply_text(tr(lang, "choose_menu"))
        return True

    if current == "resident_change_choose_vehicle":
        data = _account_and_unit(user_id)
        vehicle_id = (state.get("resident_change_vehicles") or {}).get(message_text)
        if not data or not data.get("unit") or not vehicle_id:
            await update.message.reply_text(tr(lang, "choose_menu"))
            return True
        vehicle = next((row for row in _resident_known_vehicles_for_unit(int(data["unit"]["id"])) if int(row["id"]) == int(vehicle_id)), None)
        if not vehicle or not _self_service_allowed(user_id, data["unit"], "vehicle_change_requests"):
            await update.message.reply_text(tr(lang, "suggest_denied"))
            return True
        await _show_resident_vehicle_change_menu(update, user_states, vehicle, lang)
        return True

    if current == "resident_vehicle_change_kind":
        data = _account_and_unit(user_id)
        vehicle_id = state.get("resident_change_vehicle_id")
        vehicle = next(
            (row for row in _resident_known_vehicles_for_unit(int(data["unit"]["id"])) if int(row["id"]) == int(vehicle_id or 0)),
            None,
        ) if data and data.get("unit") else None
        if not data or not data.get("unit") or not vehicle or not _self_service_allowed(user_id, data["unit"], "vehicle_change_requests"):
            await update.message.reply_text(tr(lang, "suggest_denied"))
            return True
        text_actions = {
            tr(lang, "edit_plate"): ("license_plate", "vehicle_plate_prompt"),
            tr(lang, "edit_model"): ("car_model", "vehicle_model_prompt"),
            tr(lang, "edit_color"): ("car_color", "vehicle_color_prompt"),
            tr(lang, "other_vehicle_change"): ("other", "vehicle_other_prompt"),
        }
        if message_text in text_actions:
            field, prompt = text_actions[message_text]
            state["resident_change_field"] = field
            state["mode"] = "resident_vehicle_change_value"
            await update.message.reply_text(tr(lang, prompt), reply_markup=kb([[tr(lang, "back_portal")]]))
            return True
        if message_text == tr(lang, "edit_parking_time"):
            state["resident_change_field"] = "parking_time"
            state["mode"] = "resident_vehicle_change_parking_time"
            await update.message.reply_text(
                tr(lang, "vehicle_parking_prompt"),
                reply_markup=kb([["☀️ Day", "🌙 Night"], ["🚫 Inactive"], [tr(lang, "back_portal")]]),
            )
            return True
        if message_text == tr(lang, "stop_parking"):
            if not text(vehicle.get("parking_time")):
                suggestion = _parking_mode_payment_suggestion(data["unit"])
                if not suggestion:
                    await update.message.reply_text(
                        tr(lang, "vehicle_removal_mode_unknown"),
                    )
                else:
                    state["resident_parking_mode_suggestion"] = suggestion
                    await update.message.reply_text(tr(lang, "vehicle_removal_mode_suggested", mode=suggestion["mode"]))
            state["mode"] = "resident_vehicle_stop_reason"
            await update.message.reply_text(
                tr(lang, "vehicle_stop_reason_prompt"),
                reply_markup=kb([[tr(lang, "vehicle_sold")], [tr(lang, "vehicle_no_longer_parks")], [tr(lang, "back_portal")]]),
            )
            return True
        if message_text == tr(lang, "remove_vehicle"):
            state["mode"] = "resident_vehicle_remove_confirm"
            await update.message.reply_text(
                tr(lang, "vehicle_remove_prompt"),
                reply_markup=kb([[tr(lang, "vehicle_remove_confirm")], [tr(lang, "back_portal")]]),
            )
            return True
        await update.message.reply_text(tr(lang, "choose_menu"))
        return True

    if current == "resident_vehicle_remove_confirm":
        data = _account_and_unit(user_id)
        vehicle_id = state.get("resident_change_vehicle_id")
        vehicle = next(
            (row for row in _resident_known_vehicles_for_unit(int(data["unit"]["id"])) if int(row["id"]) == int(vehicle_id or 0)),
            None,
        ) if data and data.get("unit") else None
        if not data or not data.get("unit") or not vehicle or not _self_service_allowed(user_id, data["unit"], "vehicle_change_requests"):
            await update.message.reply_text(tr(lang, "suggest_denied"))
            return True
        if message_text != tr(lang, "vehicle_remove_confirm"):
            await update.message.reply_text(tr(lang, "vehicle_remove_prompt"))
            return True
        snapshot = _vehicle_snapshot(vehicle, lang)
        task_id = _create_resident_change_task(
            user_id=user_id,
            unit=data["unit"],
            task_type="RESIDENT_VEHICLE_REMOVE",
            title="Предложение жителя: автомобиль внесён ошибочно",
            description=(
                "Житель сообщает, что автомобиль внесён ошибочно.\n"
                f"Номер: {snapshot['plate']}\nМарка/модель: {snapshot['model']}\n"
                "Просьба: проверить и удалить запись, только если у неё нет финансовой истории."
            ),
            vehicle=vehicle,
            payload={
                "schema_version": 1, "entity_type": "vehicle", "entity_id": int(vehicle["id"]),
                "current": snapshot, "proposed": {"action": "REMOVE"},
                "removal_reason": "MISTAKEN_ENTRY",
                "parking_mode_suggestion": state.get("resident_parking_mode_suggestion"),
            },
        )
        state.clear()
        await update.message.reply_text(tr(lang, "request_registered", id=task_id))
        await _show_vehicle_list(update, user_states, user_id, lang)
        return True

    if current == "resident_vehicle_change_parking_time":
        value_map = {"☀️ Day": "Day", "🌙 Night": "Night", "🚫 Inactive": "Inactive"}
        if message_text not in value_map:
            await update.message.reply_text(tr(lang, "vehicle_parking_prompt"))
            return True
        state["resident_change_value"] = value_map[message_text]
        state["mode"] = "resident_vehicle_change_save"
        message_text = value_map[message_text]

    if current == "resident_vehicle_stop_reason":
        reason_map = {
            tr(lang, "vehicle_sold"): "SOLD",
            tr(lang, "vehicle_no_longer_parks"): "NO_LONGER_PARKS",
        }
        if message_text not in reason_map:
            await update.message.reply_text(tr(lang, "vehicle_stop_reason_prompt"))
            return True
        state["resident_stop_reason"] = reason_map[message_text]
        state["mode"] = "resident_vehicle_stop_date"
        await update.message.reply_text(
            tr(lang, "vehicle_stop_date_prompt"),
            reply_markup=kb([[tr(lang, "vehicle_stop_date_unknown")], [tr(lang, "back_portal")]]),
        )
        return True

    if current == "resident_vehicle_stop_date":
        if message_text == tr(lang, "vehicle_stop_date_unknown"):
            effective_date = None
            state["resident_stop_date_unknown"] = True
        else:
            try:
                effective_date = datetime.strptime(message_text, "%Y-%m-%d").date().isoformat()
            except ValueError:
                await update.message.reply_text(tr(lang, "vehicle_stop_date_invalid"))
                return True
        state["resident_change_field"] = "parking_end_date"
        state["resident_change_value"] = effective_date or ""
        state["mode"] = "resident_vehicle_change_save"

    if current == "resident_vehicle_change_value":
        if not message_text:
            await update.message.reply_text(tr(lang, "choose_menu"))
            return True
        state["resident_change_value"] = message_text.strip()
        state["mode"] = "resident_vehicle_change_save"

    if state.get("mode") == "resident_vehicle_change_save":
        data = _account_and_unit(user_id)
        vehicle_id = state.get("resident_change_vehicle_id")
        vehicle = next(
            (row for row in _resident_known_vehicles_for_unit(int(data["unit"]["id"])) if int(row["id"]) == int(vehicle_id or 0)),
            None,
        ) if data and data.get("unit") else None
        if not data or not data.get("unit") or not vehicle or not _self_service_allowed(user_id, data["unit"], "vehicle_change_requests"):
            await update.message.reply_text(tr(lang, "suggest_denied"))
            return True
        snapshot = _vehicle_snapshot(vehicle, lang)
        field = text(state.get("resident_change_field"))
        value = text(state.get("resident_change_value"))
        labels = {
            "license_plate": "госномер",
            "car_model": "марка/модель",
            "car_color": "цвет",
            "parking_time": "режим парковки",
            "parking_end_date": "последний день парковки",
            "other": "другое изменение",
        }
        payload = {
            "schema_version": 1,
            "entity_type": "vehicle",
            "entity_id": int(vehicle["id"]),
            "current": snapshot,
            "proposed": {field: value or None},
            "parking_end_reason": state.get("resident_stop_reason") if field == "parking_end_date" else None,
            "parking_end_date_unknown": bool(state.get("resident_stop_date_unknown")) if field == "parking_end_date" else False,
            "parking_mode_suggestion": state.get("resident_parking_mode_suggestion") if field == "parking_end_date" else None,
        }
        description = (
            "Текущие данные автомобиля:\n"
            f"Номер: {snapshot['plate']}\nМарка/модель: {snapshot['model']}\nРежим парковки: {snapshot['parking']}\n\n"
            "Предложение жителя:\n"
            f"Поле: {labels.get(field, field)}\nНовое значение: {value or 'дата не указана'}"
        )
        if payload["parking_end_reason"]:
            description += f"\nПричина: {payload['parking_end_reason']}"
        task_id = _create_resident_change_task(
            user_id=user_id,
            unit=data["unit"],
            task_type="RESIDENT_VEHICLE_PARKING_END" if field == "parking_end_date" else "RESIDENT_VEHICLE_UPDATE",
            title="Предложение жителя: прекращение парковки" if field == "parking_end_date" else "Предложение жителя: исправить автомобиль",
            description=description,
            vehicle=vehicle,
            payload=payload,
        )
        state.clear()
        await update.message.reply_text(tr(lang, "request_registered", id=task_id))
        await _show_vehicle_list(update, user_states, user_id, lang)
        return True

    if current == "resident_add_vehicle_collision":
        data = _account_and_unit(user_id)
        vehicle_id = state.get("resident_add_vehicle_candidate_vehicle_id")
        proposed_plate = text(state.get("resident_add_vehicle_proposed_plate"))
        source_text = text(state.get("resident_add_vehicle_source_text"))
        vehicle = next(
            (row for row in _resident_known_vehicles_for_unit(int(data["unit"]["id"])) if int(row["id"]) == int(vehicle_id or 0)),
            None,
        ) if data and data.get("unit") else None
        if not data or not data.get("unit") or not vehicle:
            state.clear()
            await show_client_portal(update, user_states, user_id, lang)
            return True
        if message_text == tr(lang, "add_vehicle_use_correction", plate=text(vehicle.get("license_plate_normalized")) or text(vehicle.get("license_plate")) or "-"):
            snapshot = _vehicle_snapshot(vehicle, lang)
            task_id = _create_resident_change_task(
                user_id=user_id,
                unit=data["unit"],
                task_type="RESIDENT_VEHICLE_UPDATE",
                title="Предложение жителя: исправить госномер",
                description=(
                    "Текущие данные автомобиля:\n"
                    f"Номер: {snapshot['plate']}\nМарка/модель: {snapshot['model']}\nРежим парковки: {snapshot['parking']}\n\n"
                    "Предложение жителя:\n"
                    f"Поле: госномер\nНовое значение: {proposed_plate}"
                ),
                vehicle=vehicle,
                payload={
                    "schema_version": 1,
                    "entity_type": "vehicle",
                    "entity_id": int(vehicle["id"]),
                    "current": snapshot,
                    "proposed": {"license_plate": proposed_plate},
                    "source_text": source_text,
                    "detected_as_possible_typo": True,
                },
            )
            state.clear()
            await update.message.reply_text(tr(lang, "request_registered", id=task_id))
            await _show_vehicle_list(update, user_states, user_id, lang)
            return True
        if message_text == tr(lang, "add_vehicle_really_new"):
            task_id = _create_resident_change_task(
                user_id=user_id, unit=data["unit"], task_type="RESIDENT_VEHICLE_ADD",
                title="Предложение жителя: добавить автомобиль", description=source_text,
            )
            state.clear()
            await update.message.reply_text(tr(lang, "request_registered", id=task_id))
            await show_client_portal(update, user_states, user_id, lang)
            return True
        await update.message.reply_text(tr(lang, "choose_menu"))
        return True

    if current in {"resident_change_vehicle_text", "resident_change_profile_text"}:
        data = _account_and_unit(user_id)
        if not data or not data.get("unit") or not message_text:
            await show_client_portal(update, user_states, user_id, lang)
            return True
        if current == "resident_change_vehicle_text":
            vehicle_id = state.get("resident_change_vehicle_id")
            vehicle = next((row for row in _resident_known_vehicles_for_unit(int(data["unit"]["id"])) if int(row["id"]) == int(vehicle_id or 0)), None)
            if not _self_service_allowed(user_id, data["unit"], "vehicle_change_requests"):
                await update.message.reply_text(tr(lang, "suggest_denied"))
                return True
            # Do not let a one-character typo silently become an "add vehicle"
            # request.  The resident still may explicitly say it is a different car.
            if not vehicle:
                proposed_plate = _plate_from_resident_text(message_text)
                known = _resident_known_vehicles_for_unit(int(data["unit"]["id"]))
                close_match = next((row for row in known if proposed_plate and (
                    text(row.get("license_plate_normalized")) == proposed_plate
                    or _plate_distance(text(row.get("license_plate_normalized")), proposed_plate) == 1
                )), None)
                if close_match:
                    existing_plate = text(close_match.get("license_plate_normalized")) or text(close_match.get("license_plate")) or "-"
                    state["mode"] = "resident_add_vehicle_collision"
                    state["resident_add_vehicle_candidate_vehicle_id"] = int(close_match["id"])
                    state["resident_add_vehicle_proposed_plate"] = proposed_plate
                    state["resident_add_vehicle_source_text"] = message_text
                    await update.message.reply_text(
                        tr(lang, "add_vehicle_possible_correction", proposed=proposed_plate, existing=existing_plate),
                        reply_markup=kb([
                            [tr(lang, "add_vehicle_use_correction", plate=existing_plate)],
                            [tr(lang, "add_vehicle_really_new")],
                            [tr(lang, "back_portal")],
                        ]),
                    )
                    return True
            task_id = _create_resident_change_task(
                user_id=user_id, unit=data["unit"], task_type="RESIDENT_VEHICLE_CHANGE" if vehicle else "RESIDENT_VEHICLE_ADD",
                title="Предложение жителя: автомобиль" if vehicle else "Предложение жителя: добавить автомобиль",
                description=message_text, vehicle=vehicle,
            )
        else:
            if not _self_service_allowed(user_id, data["unit"], "resident_profile_change_requests"):
                await update.message.reply_text(tr(lang, "suggest_denied"))
                return True
            task_id = _create_resident_change_task(
                user_id=user_id, unit=data["unit"], task_type="RESIDENT_PROFILE_CHANGE",
                title="Предложение жителя: личные данные", description=message_text,
            )
        state.clear()
        await update.message.reply_text(tr(lang, "request_registered", id=task_id))
        await show_client_portal(update, user_states, user_id, lang)
        return True

    if current == "client_vehicles" and message_text == tr(lang, "confirm_vehicles"):
        data = _account_and_unit(user_id)
        if not data or not data.get("unit") or not _self_service_allowed(user_id, data["unit"], "vehicle_change_requests"):
            await update.message.reply_text(tr(lang, "suggest_denied"))
            return True
        vehicles = _resident_known_vehicles_for_unit(int(data["unit"]["id"]))
        if not vehicles:
            await update.message.reply_text(tr(lang, "vehicle_none"))
            return True
        task_id = _create_resident_change_task(
            user_id=user_id,
            unit=data["unit"],
            task_type="RESIDENT_VEHICLES_CONFIRMED",
            title="Житель подтвердил автомобили квартиры",
            description="Житель подтвердил перечень:\n" + _format_vehicles(vehicles, lang),
        )
        await update.message.reply_text(tr(lang, "request_registered", id=task_id))
        await _show_vehicle_list(update, user_states, user_id, lang)
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
