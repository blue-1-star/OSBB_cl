"""Read-only operational reports for OSBB administrators."""

from datetime import date
from io import BytesIO
import sys
from pathlib import Path

import pandas as pd
import streamlit as st
from openpyxl.utils import get_column_letter

STREAMLIT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(STREAMLIT_ROOT))

from admin_console.utils.db import get_conn


st.set_page_config(page_title="Отчёты", page_icon="📊", layout="wide")
st.title("📊 Отчёты")

report_name = st.selectbox(
    "Отчёт",
    ["🚗 Все автомобили", "📅 Автомобили, добавленные за период"],
)


def load_all_vehicles() -> pd.DataFrame:
    """Return exactly one row per vehicle, including vehicles without an apartment."""
    sql = """
        WITH people_by_apartment AS (
            SELECT apartment_id, group_concat(full_name, '; ') AS full_name
            FROM (
                SELECT DISTINCT
                    apartment_id,
                    TRIM(full_name) AS full_name
                FROM persons
                WHERE full_name IS NOT NULL
                  AND TRIM(full_name) <> ''
                ORDER BY full_name
            )
            GROUP BY apartment_id
        )
        SELECT
            COALESCE(a.apartment_number, '—') AS "Квартира",
            COALESCE(NULLIF(v.license_plate_normalized, ''),
                     NULLIF(v.license_plate, ''), '—') AS "Гос номер",
            COALESCE(NULLIF(v.car_model_normalized, ''),
                     NULLIF(v.car_model, ''), '—') AS "Марка",
            COALESCE(NULLIF(v.parking_time, ''), '—') AS "Тариф",
            COALESCE(p.full_name, '—') AS "ФИО",
            '' AS "Примечание"
        FROM vehicles v
        LEFT JOIN apartments a ON a.id = v.apartment_id
        LEFT JOIN people_by_apartment p ON p.apartment_id = v.apartment_id
        ORDER BY
            CASE
                WHEN a.apartment_number GLOB '[0-9]*'
                THEN CAST(a.apartment_number AS INTEGER)
                ELSE 999999
            END,
            a.apartment_number,
            v.id
    """
    conn = get_conn()
    try:
        return pd.read_sql_query(sql, conn)
    finally:
        conn.close()


def build_excel(frame: pd.DataFrame) -> bytes:
    """Create an editable spreadsheet without writing any report data back to SQLite."""
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        frame.to_excel(writer, index=False, sheet_name="Все автомобили")
        worksheet = writer.book["Все автомобили"]
        worksheet.freeze_panes = "A2"
        worksheet.auto_filter.ref = worksheet.dimensions

        for index, column in enumerate(frame.columns, start=1):
            values = [str(column)] + [str(value) for value in frame[column].fillna("")]
            width = min(max(len(value) for value in values) + 2, 60)
            worksheet.column_dimensions[get_column_letter(index)].width = width

    return buffer.getvalue()


def load_vehicles_added_between(date_from: date, date_to: date) -> pd.DataFrame:
    """Vehicles created during the inclusive calendar-date interval."""
    sql = """
        SELECT
            substr(COALESCE(v.created_at, ''), 1, 10) AS "Дата добавления",
            COALESCE(a.apartment_number, '—') AS "Квартира",
            COALESCE(NULLIF(v.license_plate_normalized, ''),
                     NULLIF(v.license_plate, ''), '—') AS "Гос номер",
            COALESCE(NULLIF(v.car_model_normalized, ''),
                     NULLIF(v.car_model, ''), '—') AS "Марка",
            COALESCE(NULLIF(v.source, ''), '—') AS "Источник",
            COALESCE(NULLIF(v.created_source, ''), '—') AS "Цепочка источника",
            COALESCE(NULLIF(v.review_status, ''), '—') AS "Статус проверки"
        FROM vehicles v
        LEFT JOIN apartments a ON a.id = v.apartment_id
        WHERE v.created_at >= ?
          AND v.created_at < date(?, '+1 day')
        ORDER BY v.created_at, a.apartment_number, v.id
    """
    conn = get_conn()
    try:
        return pd.read_sql_query(sql, conn, params=(date_from.isoformat(), date_to.isoformat()))
    finally:
        conn.close()


if report_name == "🚗 Все автомобили":
    st.subheader("🚗 Все зарегистрированные автомобили")
    st.caption(
        "Отчёт только для чтения. Включает автомобили без квартиры; «Примечание» "
        "предназначено для ручной правки только в выгруженном Excel."
    )

    vehicles = load_all_vehicles()
    st.metric("Всего автомобилей", len(vehicles))

    if vehicles.empty:
        st.info("В базе пока нет автомобилей.")
        st.stop()

    st.dataframe(vehicles, use_container_width=True, hide_index=True)

    st.download_button(
        "⬇️ Скачать Excel для ручной сверки",
        data=build_excel(vehicles),
        file_name=f"OSBB_Все_автомобили_{date.today():%Y-%m-%d}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
    )

elif report_name == "📅 Автомобили, добавленные за период":
    st.subheader("📅 Автомобили, добавленные за период")
    st.caption(
        "Период определяется по полю `vehicles.created_at`. Для контролируемых "
        "партий дополнительно показывается путь происхождения данных."
    )
    date_from, date_to = st.date_input(
        "Период добавления",
        value=(date.today(), date.today()),
        format="DD.MM.YYYY",
    )
    if date_from > date_to:
        st.error("Дата начала не может быть позже даты окончания.")
        st.stop()

    vehicles = load_vehicles_added_between(date_from, date_to)
    st.metric("Добавлено автомобилей", len(vehicles))
    if vehicles.empty:
        st.info("За выбранный период автомобилей не добавляли.")
        st.stop()

    st.dataframe(vehicles, use_container_width=True, hide_index=True)
    st.download_button(
        "⬇️ Скачать Excel за период",
        data=build_excel(vehicles),
        file_name=(
            f"OSBB_добавленные_автомобили_"
            f"{date_from:%Y-%m-%d}_по_{date_to:%Y-%m-%d}.xlsx"
        ),
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
    )
