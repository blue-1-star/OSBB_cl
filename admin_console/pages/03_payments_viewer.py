# G:\Programming\OSBB_cl\admin_console\pages\03_payments_viewer.py
"""
Страница просмотра платежей.
"""

import sys
from pathlib import Path

STREAMLIT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(STREAMLIT_ROOT))

import streamlit as st
import pandas as pd
from utils.db import get_conn

st.set_page_config(page_title="Платежи", layout="wide")
st.title("💰 Платежи")

# ==========================================
# БОКОВАЯ ПАНЕЛЬ — ФИЛЬТРЫ
# ==========================================

with st.sidebar:
    st.header("🔍 Фильтры")
    
    view_mode = st.radio(
        "Режим просмотра",
        [
            "📋 Все платежи",
            "🏠 По квартире",
            "🚗 По автомобилю",
            "📅 По дате",
        ]
    )
    
    if view_mode == "🏠 По квартире":
        apartment = st.text_input("Номер квартиры", placeholder="например: 105")
        
    elif view_mode == "🚗 По автомобилю":
        plate = st.text_input("Номер автомобиля", placeholder="например: AA8098MM")
        
    elif view_mode == "📅 По дате":
        date_from = st.date_input("С даты")
        date_to = st.date_input("По дату")


# ==========================================
# ЗАПРОСЫ
# ==========================================

def execute_query(sql: str, params=()) -> pd.DataFrame:
    """Выполняет запрос и возвращает DataFrame"""
    conn = get_conn()
    try:
        df = pd.read_sql_query(sql, conn, params=params)
        return df
    except Exception as e:
        st.error(f"Ошибка: {e}")
        return pd.DataFrame()
    finally:
        conn.close()


def show_result(df: pd.DataFrame, title: str):
    """Отображает результат"""
    if df.empty:
        st.info("Нет данных")
        return
    
    st.subheader(f"{title} ({len(df)} записей)")
    st.dataframe(df, use_container_width=True)
    
    # Сводка
    if 'amount' in df.columns:
        total = df['amount'].sum()
        st.metric("Общая сумма", f"{total:,.2f} UAH")


# ==========================================
# ОСНОВНАЯ ЛОГИКА
# ==========================================

if view_mode == "📋 Все платежи":
    sql = """
        SELECT 
            p.id,
            p.payment_date AS дата,
            p.period_code AS период,
            p.apartment_number AS квартира,
            COALESCE(v.license_plate_normalized, p.source_ref, '—') AS номер_авто,
            p.amount AS сумма,
            p.base_service_code AS услуга,
            p.payment_method AS способ,
            p.comment AS комментарий
        FROM payments p
        LEFT JOIN vehicles v ON v.id = p.vehicle_id
        ORDER BY p.payment_date DESC
        LIMIT 100
    """
    df = execute_query(sql)
    show_result(df, "Все платежи")


elif view_mode == "🏠 По квартире" and apartment:
    sql = """
        SELECT 
            p.id,
            p.payment_date AS дата,
            p.period_code AS период,
            p.apartment_number AS квартира,
            COALESCE(v.license_plate_normalized, p.source_ref, '—') AS номер_авто,
            p.amount AS сумма,
            p.base_service_code AS услуга,
            p.payment_method AS способ,
            p.comment AS комментарий
        FROM payments p
        LEFT JOIN vehicles v ON v.id = p.vehicle_id
        WHERE p.apartment_number = ?
        ORDER BY p.payment_date DESC
        LIMIT 100
    """
    df = execute_query(sql, (apartment,))
    show_result(df, f"Платежи по квартире {apartment}")


elif view_mode == "🚗 По автомобилю" and plate:
    plate_norm = plate.upper().strip()
    sql = """
        SELECT 
            p.id,
            p.payment_date AS дата,
            p.period_code AS период,
            p.apartment_number AS квартира,
            v.license_plate_normalized AS номер_авто,
            p.amount AS сумма,
            p.base_service_code AS услуга,
            p.payment_method AS способ,
            p.comment AS комментарий
        FROM payments p
        JOIN vehicles v ON v.id = p.vehicle_id
        WHERE v.license_plate_normalized = ? OR v.license_plate = ?
        ORDER BY p.payment_date DESC
        LIMIT 100
    """
    df = execute_query(sql, (plate_norm, plate_norm))
    show_result(df, f"Платежи по автомобилю {plate}")


elif view_mode == "📅 По дате":
    date_from_str = date_from.strftime("%Y-%m-%d")
    date_to_str = date_to.strftime("%Y-%m-%d")
    sql = """
        SELECT 
            p.id,
            p.payment_date AS дата,
            p.period_code AS период,
            p.apartment_number AS квартира,
            COALESCE(v.license_plate_normalized, p.source_ref, '—') AS номер_авто,
            p.amount AS сумма,
            p.base_service_code AS услуга,
            p.payment_method AS способ,
            p.comment AS комментарий
        FROM payments p
        LEFT JOIN vehicles v ON v.id = p.vehicle_id
        WHERE date(p.payment_date) BETWEEN ? AND ?
        ORDER BY p.payment_date DESC
        LIMIT 100
    """
    df = execute_query(sql, (date_from_str, date_to_str))
    show_result(df, f"Платежи с {date_from_str} по {date_to_str}")
