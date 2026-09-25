# G:\Programming\OSBB_cl\admin_console\pages\01_apartment_card.py
"""
Карточка квартиры: жильцы, автомобили, долги по парковке.
"""

import sys
from pathlib import Path

STREAMLIT_ROOT = Path(__file__).resolve().parent.parent
PROJECT_ROOT = STREAMLIT_ROOT.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import streamlit as st
import pandas as pd
from admin_console.utils.db import get_conn

st.set_page_config(page_title="Карточка квартиры", layout="wide")
st.title("🏠 Карточка квартиры")

# ==========================================
# ВВОД НОМЕРА КВАРТИРЫ
# ==========================================

# Если пришли сюда кнопкой "Открыть полную карточку" со страницы
# универсального поиска (05_universal_search.py) — подставляем номер
# автоматически, один раз (pop, не get — чтобы при следующем обычном
# заходе на страницу поле снова было пустым).
_prefill = st.session_state.pop("_jump_apartment_number", "")

apartment_number = st.text_input(
    "Введите номер квартиры",
    value=_prefill,
    placeholder="например: 105",
    help="Можно ввести номер с подъездом или без"
)

if not apartment_number:
    st.info("ℹ️ Введите номер квартиры для просмотра")
    st.stop()

# ==========================================
# ЗАПРОСЫ
# ==========================================

conn = get_conn()
cur = conn.cursor()

# 1. Проверяем, существует ли квартира
cur.execute("SELECT id, apartment_number, entrance FROM apartments WHERE apartment_number = ?", (apartment_number,))
apartment = cur.fetchone()

if not apartment:
    st.error(f"❌ Квартира {apartment_number} не найдена")
    st.stop()

apartment_id = apartment[0]
apartment_display = apartment[1]
entrance = apartment[2] or "—"

# ==========================================
# 2. Жильцы
# ==========================================
cur.execute("""
    SELECT 
        TRIM(COALESCE(telegram_first_name, '') || ' ' || COALESCE(telegram_last_name, '')) AS фио,
        telegram_username,
        telegram_user_id,
        status,
        verified_at
    FROM resident_accounts
    WHERE apartment_id = ?
    ORDER BY telegram_user_id
""", (apartment_id,))

residents = cur.fetchall()

# Contacts belong to the apartment, not to a specific vehicle. Until the
# vehicle-person relation exists, never label one of these people as owner.
cur.execute("""
    SELECT full_name, phone_raw
    FROM persons
    WHERE apartment_id = ?
    ORDER BY id
""", (apartment_id,))
apartment_people = cur.fetchall()

names = list(dict.fromkeys(
    str(person[0]).strip() for person in apartment_people if person[0] and str(person[0]).strip()
))
phones = list(dict.fromkeys(
    str(person[1]).strip() for person in apartment_people if person[1] and str(person[1]).strip()
))
apartment_names = "; ".join(names) or "—"
apartment_phones = "; ".join(phones) or "—"

# ==========================================
# 3. Автомобили с долгом
# ==========================================
cur.execute("""
    SELECT 
        v.id,
        v.license_plate_normalized AS номер,
        v.car_model AS марка,
        v.parking_time AS режим,
        COALESCE(SUM(c.amount), 0) AS начислено,
        COALESCE(SUM(pa.amount), 0) AS оплачено,
        COALESCE(SUM(c.amount), 0) - COALESCE(SUM(pa.amount), 0) AS долг,
        COALESCE(v.lifecycle_status, 'ACTIVE') AS жизненный_статус,
        v.archived_at,
        v.archive_reason,
        v.parking_end_date
    FROM vehicles v
    LEFT JOIN charges c ON c.vehicle_id = v.id 
        AND c.service_code IN ('PARKING_DAY', 'PARKING_NIGHT')
    LEFT JOIN payment_allocations pa ON pa.charge_id = c.id
    WHERE v.apartment_id = ?
    GROUP BY v.id
    ORDER BY v.id
""", (apartment_id,))

vehicles = cur.fetchall()
conn.close()

# ==========================================
# ВЫВОД КАРТОЧКИ
# ==========================================

# Заголовок
st.markdown(f"## 🏠 Квартира {apartment_display}")
st.caption(f"Подъезд: {entrance}")

# ==========================================
# ЖИЛЬЦЫ
# ==========================================
st.markdown("### 👤 Жильцы")

if residents:
    for r in residents:
        name = r[0] or "Неизвестно"
        username = f"@{r[1]}" if r[1] else "—"
        telegram_id = str(r[2]) if r[2] else "—"
        status = r[3] or "new"
        verified = "✅" if r[4] else "⏳"
        st.markdown(f"- **{name}** | Telegram ID: `{telegram_id}` | {username} | {verified} {status}")
else:
    st.info("Нет жильцов")

# ==========================================
# АВТОМОБИЛИ
# ==========================================
st.markdown("### 🚗 Автомобили")

active_vehicles = [v for v in vehicles if v[7] == "ACTIVE"]
archived_vehicles = [v for v in vehicles if v[7] == "ARCHIVED"]

if active_vehicles:
    data = []
    for v in active_vehicles:
        plate = v[1] or "—"
        model = v[2] or "—"
        mode = v[3] or "❓"
        debt = v[6] or 0.0
        data.append({
            "ФИО": apartment_names,
            "Номер": plate,
            "Марка": model,
            "Режим": mode,
            "Долг (грн)": round(debt, 2),
            "Телефон": apartment_phones,
        })
    
    df = pd.DataFrame(data)
    st.dataframe(df, use_container_width=True)
    st.caption("ФИО и телефоны взяты из записей квартиры. Связь с конкретным автомобилем и право собственности пока не подтверждены.")
    
    # Итоговый долг по квартире
    total_debt = sum(row["Долг (грн)"] for row in data)
    st.metric("💰 Итого долг по парковке", f"{total_debt:,.2f} UAH")
else:
    st.info("Нет текущих автомобилей")

if archived_vehicles:
    with st.expander(f"🗄️ История автомобилей ({len(archived_vehicles)})"):
        history = []
        for v in archived_vehicles:
            history.append({
                "Номер": v[1] or "—",
                "Марка": v[2] or "—",
                "Режим": v[3] or "—",
                "Последний день парковки": v[10] or "—",
                "Архивирован": v[8] or "—",
                "Причина": v[9] or "—",
            })
        st.dataframe(pd.DataFrame(history), hide_index=True, use_container_width=True)
