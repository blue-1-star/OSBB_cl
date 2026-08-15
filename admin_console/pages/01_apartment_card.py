# G:\Programming\OSBB_cl\admin_console\pages\01_apartment_card.py
"""
Карточка квартиры: жильцы, автомобили, долги по парковке.
"""

import sys
from pathlib import Path

STREAMLIT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(STREAMLIT_ROOT))

import streamlit as st
import pandas as pd
from utils.db import get_conn

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
        telegram_first_name || ' ' || telegram_last_name AS фио,
        telegram_username,
        status,
        verified_at
    FROM resident_accounts
    WHERE apartment_id = ?
    ORDER BY telegram_user_id
""", (apartment_id,))

residents = cur.fetchall()

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
        COALESCE(SUM(c.amount), 0) - COALESCE(SUM(pa.amount), 0) AS долг
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
        status = r[2] or "new"
        verified = "✅" if r[3] else "⏳"
        st.markdown(f"- **{name}** | {username} | {verified} {status}")
else:
    st.info("Нет жильцов")

# ==========================================
# АВТОМОБИЛИ
# ==========================================
st.markdown("### 🚗 Автомобили")

if vehicles:
    data = []
    for v in vehicles:
        plate = v[1] or "—"
        model = v[2] or "—"
        mode = v[3] or "❓"
        debt = v[6] or 0.0
        data.append({
            "Номер": plate,
            "Марка": model,
            "Режим": mode,
            "Долг (грн)": round(debt, 2)
        })
    
    df = pd.DataFrame(data)
    st.dataframe(df, use_container_width=True)
    
    # Итоговый долг по квартире
    total_debt = sum(row["Долг (грн)"] for row in data)
    st.metric("💰 Итого долг по парковке", f"{total_debt:,.2f} UAH")
else:
    st.info("Нет автомобилей")
