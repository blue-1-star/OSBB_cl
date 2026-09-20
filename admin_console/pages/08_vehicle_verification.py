"""Operator workspace for vehicle verification tasks.

Only the dedicated TBot task type can create a vehicle directly.  Every action
is transactional and is recorded in ``audit_log``.
"""

from __future__ import annotations

import json
import importlib.util
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

STREAMLIT_ROOT = Path(__file__).resolve().parent.parent
PROJECT_ROOT = STREAMLIT_ROOT.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import paths
from admin_console.utils.db import get_conn

# ``utils`` already denotes admin_console/utils in Streamlit's page runtime.
# Load the project-level utils.py under an unambiguous module name.
_registry_utils_spec = importlib.util.spec_from_file_location(
    "osbb_registry_utils", PROJECT_ROOT / "utils.py"
)
if not _registry_utils_spec or not _registry_utils_spec.loader:
    raise RuntimeError("Не удалось загрузить корневой utils.py проекта.")
_registry_utils = importlib.util.module_from_spec(_registry_utils_spec)
_registry_utils_spec.loader.exec_module(_registry_utils)
normalize_car_model = _registry_utils.normalize_car_model
normalize_color = _registry_utils.normalize_color
normalize_plate = _registry_utils.normalize_plate


TASK_TYPE_NEW_VEHICLE = "new_vehicle_for_populated_apartment"
ACTOR = "admin_console/vehicle_verification"


st.set_page_config(page_title="Верификация автомобилей", page_icon="✅", layout="wide")
st.title("✅ Верификация автомобилей")
st.caption("Операторская очередь. Подтверждение или отклонение меняет БД и создаёт запись аудита.")


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def audit(cur: sqlite3.Cursor, *, task_id: int, action: str, old: dict, new: dict, comment: str) -> None:
    cur.execute(
        """
        INSERT INTO audit_log(
            event_time, username, table_name, record_id, action, field_name,
            old_value, new_value, comment, actor_role, actor_name, source, telegram_user_id
        ) VALUES (?, 'operator', 'verification_tasks', ?, ?, '*', ?, ?, ?,
                  'operator', 'Streamlit admin', 'admin_console', NULL)
        """,
        (
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"), str(task_id), action,
            json.dumps(old, ensure_ascii=False), json.dumps(new, ensure_ascii=False), comment,
        ),
    )


def load_tasks(status_filter: str) -> pd.DataFrame:
    where = "" if status_filter == "Все" else "WHERE t.status=?"
    params: tuple = () if status_filter == "Все" else (status_filter,)
    sql = f"""
        SELECT t.id AS "ID", t.status AS "Статус", t.priority AS "Приоритет",
               t.apartment_number AS "Квартира", t.task_type AS "Тип задачи",
               COALESCE(t.normalized_candidate_value, t.candidate_value, '—') AS "Кандидат",
               t.main_value AS "Сейчас в БД", t.source_name AS "Источник",
               t.source_record_id AS "Строка источника", t.import_batch_id AS "Партия",
               t.created_at AS "Создана", t.created_by AS "Создал"
        FROM verification_tasks t
        {where}
        ORDER BY CASE t.status WHEN 'new' THEN 0 WHEN 'in_progress' THEN 1 ELSE 2 END,
                 t.priority, t.id
    """
    conn = get_conn()
    try:
        return pd.read_sql_query(sql, conn, params=params)
    finally:
        conn.close()


def load_task(task_id: int) -> dict | None:
    conn = get_conn()
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM verification_tasks WHERE id=?", (task_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def levenshtein(left: str, right: str) -> int:
    """Small, deterministic plate-distance metric for the 181 video plates."""
    if len(left) < len(right):
        left, right = right, left
    previous = list(range(len(right) + 1))
    for i, left_char in enumerate(left, start=1):
        current = [i]
        for j, right_char in enumerate(right, start=1):
            current.append(min(
                current[-1] + 1,
                previous[j] + 1,
                previous[j - 1] + (left_char != right_char),
            ))
        previous = current
    return previous[-1]


def task_plate_terms(task: dict) -> list[tuple[str, str]]:
    """Find plate-like fragments in structured fields and legacy task comments."""
    raw_terms = [
        ("Кандидат", task.get("normalized_candidate_value") or task.get("candidate_value")),
        ("Сейчас в БД", task.get("normalized_main_value") or task.get("main_value")),
    ]
    comment = task.get("comment") or ""
    raw_terms.extend(("Комментарий", match) for match in re.findall(
        r"(?i)(?<![A-ZА-ЯІЇЄҐ0-9])[A-ZА-ЯІЇЄҐ]{2}\s*\d{2,4}\s*[A-ZА-ЯІЇЄҐ]{0,2}(?![A-ZА-ЯІЇЄҐ0-9])",
        comment,
    ))
    result: list[tuple[str, str]] = []
    seen = set()
    for origin, value in raw_terms:
        normalized, _status = normalize_plate(value)
        if normalized and normalized not in seen:
            result.append((origin, normalized))
            seen.add(normalized)
    return result


def video_plate_hints(terms: list[tuple[str, str]]) -> pd.DataFrame:
    """Return exact and close video observations without changing registry data."""
    if not terms:
        return pd.DataFrame()
    conn = get_conn()
    try:
        if not table_exists(conn, "video_plate_evidence"):
            return pd.DataFrame()
        rows = pd.read_sql_query(
            """
            SELECT plate_normalized AS "_plate_key", display_plate AS "Номер из видео",
                   consensus_model AS "Модель из видео", matching_observations AS "Совпадений модели",
                   model_observations AS "Наблюдений модели", model_agreement AS "Согласие",
                   evidence_status AS "Статус модели", observation_count AS "Всего появлений",
                   registry_model AS "Модель из реестра", registry_model_relation AS "Видео ↔ реестр",
                   first_seen_date AS "Впервые", last_seen_date AS "Последний раз"
            FROM video_plate_evidence
            """,
            conn,
        )
    finally:
        conn.close()
    if rows.empty:
        return rows
    rows["Согласие, %"] = rows.pop("Согласие").map(lambda value: value * 100 if pd.notna(value) else None)
    matches = []
    for origin, normalized in terms:
        ranked = rows.copy()
        ranked["Искомый номер"] = normalized
        ranked["Из поля задачи"] = origin
        ranked["Расстояние номера"] = ranked["_plate_key"].map(lambda plate: levenshtein(normalized, plate))
        digits = re.sub(r"\D", "", normalized)
        # A legacy task may retain only the numeric core (e.g. 9599).  In
        # that case matching this core is stronger evidence than edit distance
        # against a truncated string with no prefix/suffix letters.
        numeric_core = len(normalized) <= 4 and len(digits) >= 3
        if numeric_core:
            ranked["Тип совпадения"] = "совпадают цифры номера"
            matches.append(ranked[ranked["_plate_key"].str.replace(r"\D", "", regex=True).str.contains(digits, na=False)])
            continue
        ranked["Тип совпадения"] = "похожий номер"
        # An obviously truncated legacy value (for example AA3455) may need
        # one extra insertion to reach its real eight-character plate.
        maximum_distance = 3 if len(normalized) <= 6 else 2
        matches.append(ranked[ranked["Расстояние номера"] <= maximum_distance])
    hints = pd.concat(matches, ignore_index=True) if matches else pd.DataFrame()
    if hints.empty:
        return hints
    hints = hints.sort_values(
        ["Расстояние номера", "Всего появлений", "Согласие, %"],
        ascending=[True, False, False],
        na_position="last",
    ).head(12)
    return hints


def editor_seed(task: dict) -> dict | None:
    """Read the editable vehicle state, preferring the task's trusted source."""
    if task.get("object_table") == "vehicles" and task.get("object_id"):
        conn = get_conn()
        try:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM vehicles WHERE id=?", (task["object_id"],)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()
    if task["task_type"] == TASK_TYPE_NEW_VEHICLE and task["source_name"] == "tbot_parking":
        with sqlite3.connect(paths.OSBB_QUARANTINE_DB_FILE) as quarantine:
            quarantine.row_factory = sqlite3.Row
            row = quarantine.execute(
                "SELECT license_plate, car_model, car_color FROM tbot_parking_import WHERE id=?",
                (task["source_record_id"],),
            ).fetchone()
            if row:
                return {
                    "id": None, "license_plate": row["license_plate"], "car_model": row["car_model"],
                    "car_color": row["car_color"], "parking_time": "", "status": "active", "notes": "",
                }
    return None


def save_vehicle_from_editor(task: dict, values: dict, note: str) -> tuple[int, str]:
    """Create or update one vehicle and close the task in a single audited transaction."""
    def optional_text(value: str | None) -> str | None:
        value = (value or "").strip()
        return value or None

    plate_normalized, plate_status = normalize_plate(values["license_plate"])
    if not plate_normalized:
        raise RuntimeError("Укажите госномер автомобиля.")
    model = optional_text(values["car_model"])
    color = optional_text(values["car_color"])
    model_normalized = normalize_car_model(model)
    color_normalized = normalize_color(color)
    conn = get_conn()
    try:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        current_task = cur.execute("SELECT * FROM verification_tasks WHERE id=?", (task["id"],)).fetchone()
        if not current_task or current_task["status"] == "resolved":
            raise RuntimeError("Задача уже закрыта; обновите страницу.")
        existing_id = task.get("object_id") if task.get("object_table") == "vehicles" else None
        duplicate = cur.execute(
            "SELECT id FROM vehicles WHERE license_plate_normalized=? AND id != COALESCE(?, -1)",
            (plate_normalized, existing_id),
        ).fetchone()
        if duplicate:
            raise RuntimeError(f"Госномер {plate_normalized} уже закреплён за автомобилем #{duplicate[0]}.")
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        payload = {
            "license_plate": values["license_plate"].strip(), "license_plate_normalized": plate_normalized,
            "plate_format_status": plate_status, "car_model": model, "car_model_normalized": model_normalized,
            "car_color": color, "car_color_normalized": color_normalized,
            "parking_time": optional_text(values["parking_time"]), "status": values["status"],
            "notes": optional_text(values["notes"]),
        }
        if existing_id:
            old = dict(cur.execute("SELECT * FROM vehicles WHERE id=?", (existing_id,)).fetchone())
            cur.execute(
                """
                UPDATE vehicles SET license_plate=:license_plate, license_plate_normalized=:license_plate_normalized,
                    plate_format_status=:plate_format_status, car_model=:car_model,
                    car_model_normalized=:car_model_normalized, car_color=:car_color,
                    car_color_normalized=:car_color_normalized, parking_time=:parking_time,
                    status=:status, notes=:notes, updated_at=:updated_at, updated_by=:updated_by,
                    review_status='VERIFIED_OPERATOR'
                WHERE id=:id
                """,
                payload | {"updated_at": timestamp, "updated_by": ACTOR, "id": existing_id},
            )
            vehicle_id, action, resolution = existing_id, "update_from_verification", "UPDATE_VEHICLE"
        else:
            if task["task_type"] != TASK_TYPE_NEW_VEHICLE:
                raise RuntimeError("Эта задача не содержит автомобиля для редактирования или проверенного кандидата для создания.")
            cur.execute(
                """
                INSERT INTO vehicles(
                    apartment_id, license_plate, license_plate_normalized, plate_format_status,
                    car_model, car_model_normalized, car_color, car_color_normalized, parking_time,
                    status, source, notes, created_at, created_by, lifecycle_status, review_status, created_source
                ) VALUES (:apartment_id, :license_plate, :license_plate_normalized, :plate_format_status,
                          :car_model, :car_model_normalized, :car_color, :car_color_normalized, :parking_time,
                          :status, 'verification_task', :notes, :created_at, :created_by,
                          'ACTIVE', 'VERIFIED_OPERATOR', :created_source)
                """,
                payload | {
                    "apartment_id": task["apartment_id"], "created_at": timestamp,
                    "created_by": ACTOR, "created_source": f"verification task #{task['id']}",
                },
            )
            vehicle_id, action, resolution, old = cur.lastrowid, "insert_from_verification", "CREATE_VEHICLE", {}
        cur.execute(
            """UPDATE verification_tasks SET status='resolved', resolved_at=?, resolved_by=?,
                   resolution=?, resolution_comment=? WHERE id=?""",
            (timestamp, ACTOR, resolution, note.strip(), task["id"]),
        )
        new = dict(payload) | {"id": vehicle_id, "task_id": task["id"]}
        cur.execute(
            """
            INSERT INTO audit_log(event_time, username, table_name, record_id, action, field_name,
                old_value, new_value, comment, actor_role, actor_name, source, telegram_user_id)
            VALUES (?, 'operator', 'vehicles', ?, ?, '*', ?, ?, ?, 'operator', 'Streamlit admin',
                    'verification_task', NULL)
            """,
            (timestamp, str(vehicle_id), action, json.dumps(old, ensure_ascii=False), json.dumps(new, ensure_ascii=False),
             note.strip() or f"Автомобиль сохранён при решении задачи #{task['id']}."),
        )
        audit(cur, task_id=task["id"], action=resolution.lower(), old={"status": current_task["status"]},
              new={"status": "resolved", "vehicle_id": vehicle_id, "resolution": resolution},
              comment=note.strip() or "Автомобиль сохранён оператором.")
        conn.commit()
        return vehicle_id, resolution
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def set_in_progress(task_id: int) -> None:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        row = cur.execute("SELECT status FROM verification_tasks WHERE id=?", (task_id,)).fetchone()
        if not row or row[0] != "new":
            raise RuntimeError("Задача уже изменилась; обновите страницу.")
        cur.execute("UPDATE verification_tasks SET status='in_progress' WHERE id=?", (task_id,))
        audit(cur, task_id=task_id, action="start_verification", old={"status": "new"}, new={"status": "in_progress"}, comment="Оператор взял задачу в работу.")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def resolve_rejected(task_id: int, note: str) -> None:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        row = cur.execute("SELECT status FROM verification_tasks WHERE id=?", (task_id,)).fetchone()
        if not row or row[0] == "resolved":
            raise RuntimeError("Задача уже закрыта; обновите страницу.")
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cur.execute(
            """
            UPDATE verification_tasks
            SET status='resolved', resolved_at=?, resolved_by=?, resolution='REJECTED', resolution_comment=?
            WHERE id=?
            """,
            (timestamp, ACTOR, note.strip(), task_id),
        )
        audit(cur, task_id=task_id, action="reject_candidate", old={"status": row[0]}, new={"status": "resolved", "resolution": "REJECTED"}, comment=note.strip() or "Кандидат отклонён оператором.")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def close_without_vehicle(task_id: int, note: str) -> None:
    """Close an old data-quality task without creating a new registry record."""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        row = cur.execute("SELECT status FROM verification_tasks WHERE id=?", (task_id,)).fetchone()
        if not row or row[0] == "resolved":
            raise RuntimeError("Задача уже закрыта; обновите страницу.")
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cur.execute(
            """
            UPDATE verification_tasks
            SET status='resolved', resolved_at=?, resolved_by=?,
                resolution='CLOSED_NO_VEHICLE', resolution_comment=?
            WHERE id=?
            """,
            (timestamp, ACTOR, note.strip(), task_id),
        )
        audit(
            cur,
            task_id=task_id,
            action="close_without_vehicle",
            old={"status": row[0]},
            new={"status": "resolved", "resolution": "CLOSED_NO_VEHICLE"},
            comment=note.strip() or "Задача закрыта без создания автомобиля.",
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def approve_and_create_vehicle(task: dict, note: str) -> int:
    if task["task_type"] != TASK_TYPE_NEW_VEHICLE or task["source_name"] != "tbot_parking":
        raise RuntimeError("Для этого типа задачи прямое добавление автомобиля не разрешено.")
    source_id = task["source_record_id"]
    with sqlite3.connect(paths.OSBB_QUARANTINE_DB_FILE) as quarantine:
        quarantine.row_factory = sqlite3.Row
        source = quarantine.execute(
            """
            SELECT license_plate, license_plate_normalized, car_model, car_color
            FROM tbot_parking_import WHERE id=?
            """,
            (source_id,),
        ).fetchone()
    if not source:
        raise RuntimeError("Исходная строка карантина не найдена.")

    conn = get_conn()
    try:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        current = cur.execute("SELECT * FROM verification_tasks WHERE id=?", (task["id"],)).fetchone()
        if not current or current["status"] == "resolved":
            raise RuntimeError("Задача уже закрыта; обновите страницу.")
        duplicate = cur.execute(
            """
            SELECT id FROM vehicles
            WHERE UPPER(COALESCE(license_plate_normalized, license_plate))=?
            """,
            (source["license_plate_normalized"],),
        ).fetchone()
        if duplicate:
            raise RuntimeError(f"Такой госномер уже есть в реестре: vehicle #{duplicate[0]}.")
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        model_norm = normalize_car_model(source["car_model"])
        color_norm = normalize_color(source["car_color"])
        notes = (
            f"Подтверждено оператором по задаче верификации #{task['id']}; "
            f"строка quarantine #{source_id}. {note.strip()}"
        ).strip()
        cur.execute(
            """
            INSERT INTO vehicles(
                apartment_id, license_plate, license_plate_normalized, plate_format_status,
                car_model, car_model_normalized, car_color, car_color_normalized,
                status, source, notes, created_at, created_by,
                lifecycle_status, review_status, created_source
            ) VALUES (?, ?, ?, 'STANDARD', ?, ?, ?, ?, 'active', 'tbot_parking', ?, ?, ?,
                      'ACTIVE', 'VERIFIED_OPERATOR', ?)
            """,
            (
                task["apartment_id"], source["license_plate"], source["license_plate_normalized"],
                source["car_model"], model_norm, source["car_color"], color_norm,
                notes, timestamp, ACTOR,
                f"parking_tbot2.xlsx → quarantine → verification task #{task['id']}",
            ),
        )
        vehicle_id = cur.lastrowid
        cur.execute(
            """
            UPDATE verification_tasks
            SET status='resolved', resolved_at=?, resolved_by=?, resolution='CREATE_VEHICLE',
                resolution_comment=?
            WHERE id=?
            """,
            (timestamp, ACTOR, note.strip(), task["id"]),
        )
        audit(cur, task_id=task["id"], action="approve_and_create_vehicle", old={"status": task["status"]}, new={"status": "resolved", "resolution": "CREATE_VEHICLE", "vehicle_id": vehicle_id}, comment=note.strip() or "Кандидат подтверждён; автомобиль создан.")
        cur.execute(
            """
            INSERT INTO audit_log(
                event_time, username, table_name, record_id, action, field_name,
                old_value, new_value, comment, actor_role, actor_name, source, telegram_user_id
            ) VALUES (?, 'operator', 'vehicles', ?, 'insert_from_verification', '*', '', ?, ?,
                      'operator', 'Streamlit admin', 'parking_tbot2.xlsx', NULL)
            """,
            (
                timestamp, str(vehicle_id),
                json.dumps({"apartment": task["apartment_number"], "plate": source["license_plate_normalized"]}, ensure_ascii=False),
                f"Создано после подтверждения задачи #{task['id']}.",
            ),
        )
        conn.commit()
        return vehicle_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


conn = get_conn()
try:
    ready = table_exists(conn, "verification_tasks") and "import_batch_id" in {
        row[1] for row in conn.execute("PRAGMA table_info(verification_tasks)")
    }
finally:
    conn.close()

if not ready:
    st.info("Очередь верификации ещё не подготовлена. Сначала нужно применить миграцию партии задач.")
    st.stop()

status_label = st.selectbox("Статус", ["Новые", "В работе", "Решённые", "Все"])
status_map = {"Новые": "new", "В работе": "in_progress", "Решённые": "resolved", "Все": "Все"}
tasks = load_tasks(status_map[status_label])
st.metric("Задач в выборке", len(tasks))
if tasks.empty:
    st.stop()

task_grid = tasks.copy()
task_grid.insert(0, "Выбрать", False)
grid_column, action_column = st.columns([5, 1])
with grid_column:
    edited_grid = st.data_editor(
        task_grid,
        use_container_width=True,
        hide_index=True,
        height=420,
        disabled=[column for column in task_grid.columns if column != "Выбрать"],
        column_config={
            "Выбрать": st.column_config.CheckboxColumn(
                "Выбрать",
                help="Отметьте одну задачу.",
                default=False,
            ),
        },
        key=f"vehicle_verification_tasks_editor_{status_map[status_label]}",
    )
selected_ids = edited_grid.loc[edited_grid["Выбрать"], "ID"].tolist()
if len(selected_ids) > 1:
    st.warning("Отметьте только одну задачу для работы.")
    st.stop()

with action_column:
    if selected_ids:
        st.success(f"Задача #{selected_ids[0]}")
        open_task = st.button("Открыть", type="primary", use_container_width=True)
    else:
        st.info("Выберите одну строку")
        open_task = False

if open_task and selected_ids:
    st.session_state["open_vehicle_verification_task_id"] = int(selected_ids[0])

task_id = st.session_state.get("open_vehicle_verification_task_id")
if not task_id:
    st.stop()

task = load_task(int(task_id))
if not task:
    st.session_state.pop("open_vehicle_verification_task_id", None)
    st.error("Задача не найдена. Обновите страницу.")
    st.stop()

st.subheader(f"Задача #{task['id']} — кв. {task['apartment_number']}")
if st.button("← К очереди"):
    st.session_state.pop("open_vehicle_verification_task_id", None)
    st.rerun()
c1, c2, c3 = st.columns(3)
c1.write(f"**Кандидат:** {task['normalized_candidate_value'] or task['candidate_value'] or '—'}")
c2.write(f"**Состояние БД:** {task['main_value'] or '—'}")
c3.write(f"**Статус:** {task['status']}")
st.write(f"**Источник:** {task['source_name']} / строка #{task['source_record_id']} / партия #{task['import_batch_id']}")
st.write(f"**Комментарий:** {task['comment'] or '—'}")
st.write(f"**Подсказка:** {task['suggestion'] or '—'}")

video_terms = task_plate_terms(task)
video_hints = video_plate_hints(video_terms)
st.subheader("Видео-подсказки")
if not video_terms:
    st.info("В задаче не найдено номера для поиска по видео.")
elif video_hints.empty:
    st.info("Для номеров из задачи нет совпадений или близких номеров в загруженных видео-наблюдениях.")
else:
    st.caption(
        "Сначала показаны наиболее близкие номера. Для старой записи, где сохранились только цифры номера, "
        "ищется совпадение этих цифр в полном номере из видео."
    )
    st.dataframe(
        video_hints.drop(columns=["_plate_key"]),
        use_container_width=True,
        hide_index=True,
        column_config={"Согласие, %": st.column_config.ProgressColumn("Согласие", min_value=0, max_value=100, format="%.0f%%")},
    )

if task["status"] == "new":
    if st.button("▶️ Взять в работу", use_container_width=False):
        try:
            set_in_progress(task["id"])
            st.success("Задача переведена в работу.")
            st.rerun()
        except Exception as error:
            st.error(str(error))

if task["status"] != "resolved":
    seed = editor_seed(task)
    if seed:
        best_video = video_hints.iloc[0] if not video_hints.empty else None
        video_model = ""
        video_plate = ""
        if not video_hints.empty:
            exact = video_hints[video_hints["Расстояние номера"] == 0]
            if not exact.empty:
                video_model = str(exact.iloc[0]["Модель из видео"] or "")
                video_plate = str(exact.iloc[0]["Номер из видео"] or "")
            elif best_video is not None and best_video["Тип совпадения"] == "совпадают цифры номера":
                video_model = str(best_video["Модель из видео"] or "")
                video_plate = str(best_video["Номер из видео"] or "")
        source_model = str(seed.get("car_model") or "").strip()
        source_model_as_plate, source_model_plate_status = normalize_plate(source_model)
        model_looks_like_plate = (
            bool(source_model_as_plate)
            and source_model_plate_status != "MISSING"
            and any(character.isdigit() for character in source_model_as_plate)
        )
        default_model = "" if model_looks_like_plate else source_model
        default_model = default_model or video_model
        plate_widget_key = f"vehicle_editor_plate_{task['id']}"
        model_widget_key = f"vehicle_editor_model_{task['id']}"
        if plate_widget_key not in st.session_state:
            st.session_state[plate_widget_key] = str(seed.get("license_plate") or "")
        if model_widget_key not in st.session_state:
            st.session_state[model_widget_key] = default_model
        st.subheader("Редактор автомобиля")
        st.caption(
            "Значения можно исправить перед сохранением. Видео-подсказка не меняет запись сама: "
            "оператор подтверждает каждое изменение, а БД сохраняет его в аудит." 
        )
        if best_video is not None:
            video_description = f"Видео предлагает номер {best_video['Номер из видео']}"
            if best_video["Модель из видео"]:
                video_description += f" и модель {best_video['Модель из видео']}"
            video_description += f" ({int(best_video['Всего появлений'])} появлений)."
            current_plate, _ = normalize_plate(seed.get("license_plate"))
            if current_plate == best_video["_plate_key"] and (
                not best_video["Модель из видео"] or source_model.upper() == str(best_video["Модель из видео"]).upper()
            ):
                st.success(video_description + " Эти данные уже стоят в редактируемой записи.")
            else:
                st.info(video_description)
                if st.button("Применить подсказку видео к форме", type="primary"):
                    st.session_state[plate_widget_key] = str(best_video["Номер из видео"])
                    if best_video["Модель из видео"]:
                        st.session_state[model_widget_key] = str(best_video["Модель из видео"])
                    st.session_state[f"video_hint_applied_{task['id']}"] = True
                    st.rerun()
                st.caption("Кнопка заполнит форму, но не изменит БД. Затем проверьте поля и отдельно сохраните автомобиль.")
        if st.session_state.get(f"video_hint_applied_{task['id']}"):
            st.success("Подсказка видео подставлена в форму. Проверьте значения и сохраните автомобиль, если они подтверждены.")
        if model_looks_like_plate:
            st.warning(
                f"Текущее поле марки содержит похожее на номер значение «{source_model}». "
                "Оно не подставлено в редактор как модель."
            )
        with st.form("vehicle_editor"):
            e1, e2, e3 = st.columns(3)
            e1.text_input("Квартира", value=str(task["apartment_number"] or "—"), disabled=True)
            license_plate = e2.text_input("Госномер", key=plate_widget_key)
            status = e3.selectbox("Статус", ["active", "inactive"], index=0 if seed.get("status", "active") == "active" else 1)
            car_model = st.text_input("Марка / модель", key=model_widget_key, help="Здесь может быть подставлена модель из видео-подсказки.")
            car_color = st.text_input("Цвет", value=str(seed.get("car_color") or ""))
            parking_time = st.text_input("Время / тариф парковки", value=str(seed.get("parking_time") or ""))
            notes = st.text_area("Примечание к автомобилю", value=str(seed.get("notes") or ""))
            note = st.text_area("Комментарий решения", placeholder="Какие данные подтверждены видео, жильцом или другим источником?")
            saved = st.form_submit_button("Сохранить автомобиль и закрыть задачу", type="primary", use_container_width=True)
        if saved:
            try:
                vehicle_id, resolution = save_vehicle_from_editor(
                    task,
                    {"license_plate": license_plate, "car_model": car_model, "car_color": car_color,
                     "parking_time": parking_time, "status": status, "notes": notes},
                    note,
                )
                verb = "обновлён" if resolution == "UPDATE_VEHICLE" else "создан"
                st.success(f"Автомобиль #{vehicle_id} {verb}, задача закрыта.")
                st.session_state.pop("open_vehicle_verification_task_id", None)
                st.rerun()
            except Exception as error:
                st.error(str(error))
    else:
        st.info("Для этой старой задачи нет автомобиля или проверенного кандидата для редактирования.")
        with st.form("resolve_task"):
            note = st.text_area("Комментарий оператора", placeholder="Почему задача закрыта?")
            submitted = st.form_submit_button("Закрыть без создания автомобиля", use_container_width=True)
        if submitted:
            try:
                close_without_vehicle(task["id"], note)
                st.success("Задача закрыта без создания автомобиля.")
                st.session_state.pop("open_vehicle_verification_task_id", None)
                st.rerun()
            except Exception as error:
                st.error(str(error))
