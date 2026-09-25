"""Read-only operational reports for OSBB administrators."""

from datetime import date
import getpass
import json
from io import BytesIO
import sys
from pathlib import Path

import pandas as pd
import streamlit as st
from openpyxl.utils import get_column_letter

STREAMLIT_ROOT = Path(__file__).resolve().parent.parent
PROJECT_ROOT = STREAMLIT_ROOT.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from admin_console.utils.db import get_conn
from data_quality_report import RULES, build_quality_issues, quality_summary
from data_quality_proposals import create_proposal, list_proposals, reply_to_clarification


st.set_page_config(page_title="Отчёты", page_icon="📊", layout="wide")
st.title("📊 Отчёты")

report_name = st.selectbox(
    "Отчёт",
    ["🚗 Все автомобили", "📅 Автомобили, добавленные за период", "🧩 Пробелы в данных"],
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

elif report_name == "🧩 Пробелы в данных":
    st.subheader("🧩 Пробелы в данных")
    st.caption("Живой отчёт по объявленным правилам; он не создаёт задач верификации и не меняет БД.")
    conn = get_conn()
    try:
        issues = build_quality_issues(conn)
    finally:
        conn.close()
    summary = quality_summary(issues)
    metric1, metric2, metric3 = st.columns(3)
    metric1.metric("Пробелов", summary["issues"])
    metric2.metric("Затронуто записей", summary["records"])
    metric3.metric("Важных и критичных", sum(summary["by_severity"].get(level, 0) for level in ("Критично", "Важно")))
    apartment_filter = st.text_input("Квартира", placeholder="Например, 160").strip()
    severity_filter = st.selectbox("Важность", ["Все", "Критично", "Важно", "Дополнить"])
    rule_filter = st.selectbox(
        "Вид пробела", ["Все"] + list(RULES),
        format_func=lambda code: "Все" if code == "Все" else f"{RULES[code][0]} · {RULES[code][1]}",
    )
    visible = [i for i in issues if
               (not apartment_filter or i["apartment"] == apartment_filter)
               and (severity_filter == "Все" or i["severity"] == severity_filter)
               and (rule_filter == "Все" or i["rule_code"] == rule_filter)]
    st.write(f"Показано: {len(visible)}")
    selection = st.dataframe(pd.DataFrame([{
        "Важность": i["severity"], "Квартира": i["apartment"],
        "Госномер": i["plate"], "Запись": f"{i['entity']} #{i['object_id']}",
        "Что уточнить": i["field"], "Источник": i["source"],
    } for i in visible]), hide_index=True, use_container_width=True,
        on_select="rerun", selection_mode="single-row", key="quality_issue_selection")
    selected_rows = selection.selection.rows
    if selected_rows and 0 <= selected_rows[0] < len(visible):
        issue = visible[selected_rows[0]]
        st.markdown(f"#### {issue['entity']} #{issue['object_id']} · кв.{issue['apartment']}")
        st.write(f"Проверить: **{issue['field']}**. Источник записи: {issue['source']}.")
        if issue["apartment"] != "—" and st.button("🏠 Открыть карточку квартиры"):
            st.session_state["_jump_apartment_number"] = issue["apartment"]
            st.switch_page("pages/01_apartment_card.py")
        st.caption("Локальный режим: автор в аудите — учётная запись macOS. Предложение не меняет реестр.")
        with st.form(f"quality_proposal_{issue['rule_code']}_{issue['object_id']}"):
            proposed_value = st.text_input("Предлагаемое значение *")
            evidence = st.text_area("Откуда известно исправление *", placeholder="Например: бумажная анкета от 12.09; лично проверено у жителя")
            submitted = st.form_submit_button("📝 Зарегистрировать предложение", type="primary")
        if submitted:
            try:
                proposal_id = create_proposal(
                    rule_code=issue["rule_code"], object_id=issue["object_id"],
                    apartment=issue["apartment"], proposed_value=proposed_value,
                    evidence=evidence, actor=f"local_mac:{getpass.getuser()}",
                )
                st.success(f"Предложение #{proposal_id} зарегистрировано. Данные не изменены; требуется решение SUPER_ADMIN.")
            except Exception as exc:
                st.error(str(exc))
    else:
        st.caption("Нажмите строку, чтобы открыть запись и доступное действие.")
    with st.expander("📝 Зарегистрированные предложения"):
        proposals = list_proposals()
        if proposals:
            st.dataframe(pd.DataFrame([{
                "№": row["id"], "Квартира": row["apartment_number"],
                "Что исправить": row["title"], "Статус": row["status"],
                "Кем внесено": row["created_by"], "Когда": row["created_at"],
            } for row in proposals]), hide_index=True, use_container_width=True)
            local_actor = f"local_mac:{getpass.getuser()}"
            mine = [row for row in proposals if row["created_by"] == local_actor]
            if mine:
                selected_proposal_id = st.selectbox(
                    "Открыть своё предложение", [int(row["id"]) for row in mine],
                    format_func=lambda task_id: f"#{task_id} · {next(row['status'] for row in mine if int(row['id']) == task_id)}",
                )
                selected_proposal = next(row for row in mine if int(row["id"]) == selected_proposal_id)
                payload = json.loads(selected_proposal["payload_json"] or "{}")
                st.write(f"Было: {payload.get('old_value') or '—'} → предложено: {payload.get('proposed_value') or '—'}")
                for item in payload.get("dialogue") or []:
                    st.write(f"{item.get('kind')}: {item.get('text')}")
                if selected_proposal["close_note"]:
                    st.write(f"Решение: {selected_proposal['close_note']}")
                if selected_proposal["status"] == "NEEDS_CLARIFICATION":
                    with st.form(f"quality_reply_{selected_proposal_id}"):
                        reply = st.text_area("Ответ супер­админу")
                        send_reply = st.form_submit_button("Отправить ответ")
                    if send_reply:
                        try:
                            reply_to_clarification(task_id=selected_proposal_id, actor=local_actor, reply=reply)
                            st.success("Ответ сохранён; предложение снова ожидает решения.")
                            st.rerun()
                        except Exception as exc:
                            st.error(str(exc))
            st.caption("Принимать исправления может только SUPER_ADMIN через авторизованный Telegram-бот.")
        else:
            st.caption("Предложений пока нет.")
    with st.expander("Что входит в отчёт"):
        st.write("Проверяются действующие авто и жилые квартиры: квартира, номер, марка, режим, цвет, площадь, наличие ФИО и контакта.")
        st.warning("Связь конкретного автомобиля с его владельцем пока не хранится в структуре БД. Этот пробел нельзя автоматически закрыть заполнением ФИО квартиры.")
