"""Read-only history of traceable database import batches."""

import pandas as pd
import streamlit as st

from admin_console.utils.db import get_conn


st.set_page_config(page_title="История наполнения БД", page_icon="📥", layout="wide")
st.title("📥 История наполнения БД")
st.caption("Показывает только контролируемые партии. Каждая строка хранит файл, дату и путь поступления данных.")


def table_exists(conn, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


conn = get_conn()
try:
    if not table_exists(conn, "data_import_batches"):
        st.info("Контролируемых партий импорта пока нет.")
        st.stop()

    batches = pd.read_sql_query(
        """
        SELECT id AS "Партия", entity_type AS "Данные", source_file_name AS "Источник",
               pipeline AS "Путь", source_records_count AS "Строк в источнике",
               selected_records_count AS "Отобрано", inserted_records_count AS "Добавлено",
               skipped_records_count AS "Пропущено", status AS "Статус",
               applied_at AS "Дата", notes AS "Примечание"
        FROM data_import_batches
        ORDER BY id DESC
        """,
        conn,
    )
    if batches.empty:
        st.info("Контролируемых партий импорта пока нет.")
        st.stop()

    total_added = int(batches["Добавлено"].fillna(0).sum())
    c1, c2 = st.columns(2)
    c1.metric("Партий импорта", len(batches))
    c2.metric("Автомобилей добавлено партиями", total_added)
    st.dataframe(batches, use_container_width=True, hide_index=True)

    batch_id = st.selectbox("Показать состав партии", batches["Партия"].tolist())
    entity_type = batches.loc[batches["Партия"] == batch_id, "Данные"].iloc[0]
    if entity_type == "vehicle_verification_tasks":
        items = pd.read_sql_query(
            """
            SELECT id AS "ID задачи", apartment_number AS "Квартира",
                   COALESCE(normalized_candidate_value, candidate_value, '—') AS "Кандидат",
                   main_value AS "Сейчас в БД", source_record_id AS "Строка карантина",
                   status AS "Статус", task_type AS "Тип задачи"
            FROM verification_tasks
            WHERE import_batch_id=?
            ORDER BY id
            """,
            conn,
            params=(batch_id,),
        )
    else:
        items = pd.read_sql_query(
            """
            SELECT i.source_record_id AS "Строка карантина", i.apartment_number AS "Квартира",
                   i.license_plate_normalized AS "Госномер",
                   COALESCE(NULLIF(v.car_model_normalized, ''), NULLIF(v.car_model, ''), '—') AS "Марка",
                   COALESCE(NULLIF(v.car_color_normalized, ''), NULLIF(v.car_color, ''), '—') AS "Цвет",
                   COALESCE(NULLIF(v.car_model, ''), '—') AS "Марка в источнике",
                   COALESCE(NULLIF(v.car_color, ''), '—') AS "Цвет в источнике",
                   i.decision AS "Решение",
                   i.decision_reason AS "Основание", i.vehicle_id AS "ID автомобиля"
            FROM vehicle_import_batch_items i
            LEFT JOIN vehicles v ON v.id=i.vehicle_id
            WHERE i.batch_id=?
            ORDER BY i.id
            """,
            conn,
            params=(batch_id,),
        )
    st.subheader(f"Состав партии #{batch_id}")
    st.dataframe(items, use_container_width=True, hide_index=True)
finally:
    conn.close()
