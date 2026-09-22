"""
Главная страница OSBB Admin Console
"""

import sys
from pathlib import Path

STREAMLIT_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = STREAMLIT_ROOT.parent
# ``streamlit run admin_console/home.py`` places admin_console itself on
# sys.path, not its parent.  The package name ``admin_console`` therefore
# needs the project root explicitly, both on a fresh launch and on navigation.
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import streamlit as st
from admin_console.utils.db import get_conn

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
# Technical/test units are maintained in the same registry but are not homes
# and must not inflate the headline count shown to ordinary operators.
cur.execute(
    """
    SELECT COUNT(*) FROM apartments
    WHERE COALESCE(unit_type, '') <> 'TECHNICAL'
      AND COALESCE(record_status, '') <> 'TEST'
    """
)
count = cur.fetchone()[0]
conn.close()

st.success(f"✅ Подключено к БД. Квартир: {count}")

# ==========================================
# НАВИГАЦИЯ
# ==========================================

st.markdown("## 📚 Доступные разделы")

col1, col2, col3, col4, col5, col6, col7, col8, col9, col10, col11 = st.columns(11)

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

with col5:
    if st.button("📊 Отчёты", use_container_width=True):
        st.switch_page("pages/06_reports.py")

with col6:
    if st.button("📥 История импорта", use_container_width=True):
        st.switch_page("pages/07_import_history.py")

with col7:
    if st.button("✅ Верификация авто", use_container_width=True):
        st.switch_page("pages/08_vehicle_verification.py")

with col8:
    if st.button("📨 Заявки жителей", use_container_width=True):
        st.switch_page("pages/09_resident_requests.py")

with col9:
    if st.button("👥 Пользователи и доступ", use_container_width=True):
        st.switch_page("pages/10_user_access.py")

with col10:
    if st.button("📦 Исполнение заказов", use_container_width=True):
        st.switch_page("pages/11_order_fulfillment.py")

with col11:
    if st.button("📚 Каталог услуг", use_container_width=True):
        st.switch_page("pages/12_service_catalog.py")

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
    - «Отчёты» работают только на чтение; примечания для ручной сверки
      добавляются в выгруженном Excel, а не в базе данных.
    """)
