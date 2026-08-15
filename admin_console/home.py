# G:\Programming\OSBB_cl\admin_console\home.py
"""
Главная страница OSBB Admin Console
"""

import sys
from pathlib import Path

STREAMLIT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(STREAMLIT_ROOT))

import streamlit as st
from utils.db import get_conn

st.set_page_config(
    page_title="OSBB Admin Console",
    page_icon="🏢",
    layout="wide"
)

st.title("🏢 OSBB Admin Console")
st.caption("Просмотр и редактирование данных проекта OSBB")

# Проверка подключения
conn = get_conn()
cur = conn.cursor()
cur.execute("SELECT COUNT(*) FROM apartments")
count = cur.fetchone()[0]
conn.close()

st.success(f"✅ Подключено к БД. Квартир: {count}")

# ==========================================
# НАВИГАЦИЯ
# ==========================================

st.markdown("## 📚 Доступные разделы")

col1, col2, col3, col4 = st.columns(4)

with col1:
    if st.button("🏠 Карточка квартиры", use_container_width=True):
        st.switch_page("pages/01_apartment_card.py")

with col2:
    if st.button("💰 Платежи", use_container_width=True):
        st.switch_page("pages/03_payments_viewer.py")

with col3:
    if st.button("🏦 Правка кассы", use_container_width=True):
        st.switch_page("pages/04_cashbox_editor.py")

with col4:
    if st.button("🔎 Поиск (ФИО/авто/тел.)", use_container_width=True):
        st.switch_page("pages/05_universal_search.py")

# Информация о проекте
with st.expander("ℹ️ О проекте"):
    st.markdown("""
    **OSBB Admin Console** — инструмент для просмотра и точечного
    редактирования данных проекта OSBB.

    - База данных: `osbb_test.db`
    - Количество таблиц: ~100+
    - Интерфейс: Streamlit
    - Разделы с записью в БД (⚠️ действуют осторожно, с audit_log
      и пересчётом связанных балансов): «Правка кассы»
    """)