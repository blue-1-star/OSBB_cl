"""Default administrator dashboard for the OSBB admin console."""

from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

STREAMLIT_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = STREAMLIT_ROOT.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import streamlit as st

from admin_console.utils.db import get_conn
from bot_security_watch import recent_events

st.set_page_config(page_title="Рабочий стол администратора", page_icon="🏢", layout="wide")


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def scalar(conn: sqlite3.Connection, sql: str) -> int | float:
    return conn.execute(sql).fetchone()[0] or 0


def dashboard_data() -> dict[str, int | float]:
    """Load only actionable figures. Missing optional modules read as zero."""
    conn = get_conn()
    try:
        data: dict[str, int | float] = dict.fromkeys((
            "apartments", "remote_interest", "remote_interest_qty", "remote_paid",
            "remote_paid_qty", "verification_vehicle", "verification_apartment",
            "improvement_paid", "improvement_sum"), 0)
        if table_exists(conn, "apartments"):
            data["apartments"] = scalar(conn, """SELECT COUNT(*) FROM apartments
                WHERE COALESCE(unit_type, '') <> 'TECHNICAL' AND COALESCE(record_status, '') <> 'TEST'""")
        if table_exists(conn, "service_order_interests"):
            data["remote_interest"] = scalar(conn, """SELECT COUNT(*) FROM service_order_interests
                WHERE service_item_code='REMOTE_NEW' AND interest_status IN ('INTEREST', 'PAYMENT_NOTICE')""")
            data["remote_interest_qty"] = scalar(conn, """SELECT COALESCE(SUM(quantity), 0) FROM service_order_interests
                WHERE service_item_code='REMOTE_NEW' AND interest_status IN ('INTEREST', 'PAYMENT_NOTICE')""")
        if table_exists(conn, "service_orders"):
            unpaid_batch = """service_item_code='REMOTE_NEW' AND payment_status='CONFIRMED'
                AND order_status NOT IN ('COMPLETED', 'CANCELLED')
                AND NOT EXISTS (SELECT 1 FROM remote_supplier_batch_links l WHERE l.service_order_id=service_orders.id)"""
            data["remote_paid"] = scalar(conn, f"SELECT COUNT(*) FROM service_orders WHERE {unpaid_batch}")
            data["remote_paid_qty"] = scalar(conn, f"SELECT COALESCE(SUM(quantity), 0) FROM service_orders WHERE {unpaid_batch}")
        if table_exists(conn, "verification_tasks"):
            vehicle_condition = "(task_group='VEHICLE' OR task_type LIKE '%vehicle%' OR object_table='vehicles')"
            data["verification_vehicle"] = scalar(conn, f"SELECT COUNT(*) FROM verification_tasks WHERE status IN ('new', 'in_progress') AND {vehicle_condition}")
            data["verification_apartment"] = scalar(conn, f"SELECT COUNT(*) FROM verification_tasks WHERE status IN ('new', 'in_progress') AND NOT {vehicle_condition}")
        if table_exists(conn, "payments"):
            data["improvement_paid"] = scalar(conn, "SELECT COUNT(*) FROM payments WHERE base_service_code='IMPROVEMENT'")
            data["improvement_sum"] = scalar(conn, "SELECT COALESCE(SUM(amount), 0) FROM payments WHERE base_service_code='IMPROVEMENT'")
        return data
    finally:
        conn.close()


data = dashboard_data()
st.title("🏢 Рабочий стол администратора")
st.caption("Открывается первым. Здесь только то, что требует решения или контроля сейчас.")

security_checks = [event for event in recent_events(limit=200)
                   if event.get("kind") in {"OK", "ALERT", "CONFLICT", "UNAUTHORIZED"}]
latest_security = security_checks[-1] if security_checks else None
if latest_security:
    try:
        security_age = datetime.now(timezone.utc) - datetime.fromisoformat(latest_security["at"])
    except (KeyError, ValueError):
        security_age = None
    if latest_security["kind"] != "OK":
        st.error("🚨 Контроль бота: обнаружена тревога. Откройте подробности.")
    elif security_age is None or security_age.total_seconds() > 600:
        st.warning("🛡️ Контроль бота: последняя проверка старше 10 минут. Проверьте состояние.")
    else:
        st.success("🛡️ Контроль бота: профиль и вебхук соответствуют эталону.")
else:
    st.warning("🛡️ Контроль бота ещё не выполнялся.")
if st.button("Открыть контроль безопасности бота", use_container_width=True):
    st.switch_page("pages/18_bot_security.py")

st.markdown("### Срочно и в работе")
left, middle, right = st.columns(3)
with left:
    st.error(f"📦 **Новые пульты — {data['remote_interest_qty']} шт.**")
    st.caption(f"{data['remote_interest']} предзаказ(а) ожидают оплаты или подтверждения.")
    if st.button("Открыть предзаказы пультов", type="primary", use_container_width=True):
        st.session_state["fulfillment_demand_item"] = "REMOTE_NEW"
        st.switch_page("pages/11_order_fulfillment.py")
with middle:
    st.warning(f"💳 **Оплачено — {data['remote_paid_qty']} шт.**")
    st.caption(f"{data['remote_paid']} заказ(а) ещё не включены в заказ поставщику.")
    if st.button("Сформировать заказ поставщику", use_container_width=True):
        st.session_state["fulfillment_demand_item"] = "REMOTE_NEW"
        st.session_state["show_supplier_readiness_REMOTE_NEW"] = True
        st.switch_page("pages/11_order_fulfillment.py")
with right:
    total = int(data["verification_vehicle"]) + int(data["verification_apartment"])
    st.info(f"✅ **Пробелы в данных — {total}**")
    st.caption(f"Автомобили: {data['verification_vehicle']} · квартиры и прочее: {data['verification_apartment']}.")
    if st.button("Открыть верификацию", use_container_width=True):
        st.session_state["dashboard_verification_status"] = "Новые"
        st.switch_page("pages/08_vehicle_verification.py")

st.markdown("### Сборы и контроль")
fund_col, request_col, access_col = st.columns(3)
with fund_col:
    st.metric("🏗 Сбор на благоустройство", f"{float(data['improvement_sum']):,.2f} грн")
    st.caption(f"Проведено платежей: {data['improvement_paid']}.")
    if st.button("Открыть сбор на благоустройство", use_container_width=True):
        st.session_state["dashboard_payment_view"] = "🏗 Благоустройство"
        st.switch_page("pages/03_payments_viewer.py")
with request_col:
    st.markdown("**📨 Заявки жителей**")
    st.caption("Изменения от жильцов и вопросы, требующие решения оператора.")
    if st.button("Открыть заявки", use_container_width=True):
        st.switch_page("pages/09_resident_requests.py")
with access_col:
    st.markdown("**👥 Доступ и жители**")
    st.caption(f"В реестре жилых квартир: {data['apartments']}.")
    if st.button("Открыть пользователей", use_container_width=True):
        st.switch_page("pages/10_user_access.py")

st.divider()
with st.expander("Все разделы админ-консоли"):
    pages = [("🏠 Карточка квартиры", "pages/01_apartment_card.py"), ("💰 Платежи", "pages/03_payments_viewer.py"),
             ("🏦 Правка кассы", "pages/04_cashbox_editor.py"), ("🔎 Поиск", "pages/05_universal_search.py"),
             ("📊 Отчёты", "pages/06_reports.py"), ("📥 История импорта", "pages/07_import_history.py"),
             ("✅ Верификация", "pages/08_vehicle_verification.py"), ("📦 Исполнение заказов", "pages/11_order_fulfillment.py"),
             ("📚 Каталог услуг", "pages/12_service_catalog.py")]
    columns = st.columns(3)
    for index, (label, path) in enumerate(pages):
        with columns[index % 3]:
            if st.button(label, key=f"nav_{index}", use_container_width=True):
                st.switch_page(path)
