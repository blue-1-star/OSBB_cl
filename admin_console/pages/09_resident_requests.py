"""Operator inbox for structured resident self-service proposals.

This page deliberately does not apply vehicle or billing changes.  It shows
the same current → proposed payload that the resident submitted, lets an
operator take a request into work, and records that action in audit_log.
Applying a sale/parking-end request requires the forthcoming effective-dated
parking-period model and must not be simulated by changing a vehicle status.
"""

from __future__ import annotations

import json
import importlib.util
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

# Do not use ``from utils.db`` here: the project root also contains
# ``utils.py`` and Streamlit can keep that module from a previously opened
# page.  The fully-qualified package name is unambiguous.
from admin_console.utils.db import get_conn


ACTOR = "admin_console/resident_requests"

# In a Streamlit page ``utils`` resolves to ``admin_console/utils``.  The
# registry normalizer belongs to the project-level ``utils.py``.
_registry_utils_spec = importlib.util.spec_from_file_location(
    "osbb_registry_utils_for_resident_requests", PROJECT_ROOT / "utils.py"
)
if not _registry_utils_spec or not _registry_utils_spec.loader:
    raise RuntimeError("Не удалось загрузить нормализатор госномеров.")
_registry_utils = importlib.util.module_from_spec(_registry_utils_spec)
_registry_utils_spec.loader.exec_module(_registry_utils)
normalize_plate = _registry_utils.normalize_plate
normalize_car_model = _registry_utils.normalize_car_model
normalize_color = _registry_utils.normalize_color

st.set_page_config(page_title="Заявки жителей", page_icon="📨", layout="wide")
st.title("📨 Заявки жителей")
st.caption("Предложения из Telegram-кабинета. Автомобили и начисления не меняются до отдельного подтверждённого решения оператора.")


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def load_requests(status_filter: str) -> pd.DataFrame:
    where = "WHERE q.origin='RESIDENT_PORTAL'"
    params: list[str] = []
    if status_filter != "Все":
        where += " AND q.status=?"
        params.append(status_filter)
    conn = get_conn()
    try:
        if not table_exists(conn, "operator_task_queue"):
            return pd.DataFrame()
        return pd.read_sql_query(
            f"""
            SELECT q.id AS "ID", q.status AS "Статус", q.apartment_number AS "Квартира",
                   q.plate AS "Номер", q.task_type AS "Тип", q.title AS "Заявка",
                   COALESCE(a.telegram_first_name || ' ', '') ||
                   COALESCE(a.telegram_last_name, a.telegram_username, q.telegram_user_id) AS "Житель",
                   q.created_at AS "Создана", q.updated_at AS "Обновлена"
            FROM operator_task_queue q
            LEFT JOIN resident_accounts a ON CAST(a.telegram_user_id AS TEXT)=q.telegram_user_id
            {where}
            ORDER BY CASE q.status WHEN 'PENDING' THEN 0 WHEN 'IN_PROGRESS' THEN 1 ELSE 2 END,
                     q.id DESC
            """,
            conn,
            params=params,
        )
    finally:
        conn.close()


def load_request(request_id: int) -> dict | None:
    conn = get_conn()
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT q.*, a.telegram_username, a.telegram_first_name, a.telegram_last_name
            FROM operator_task_queue q
            LEFT JOIN resident_accounts a ON CAST(a.telegram_user_id AS TEXT)=q.telegram_user_id
            WHERE q.id=? AND q.origin='RESIDENT_PORTAL'
            """,
            (request_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def audit_status_change(cur: sqlite3.Cursor, request_id: int, old: str, new: str, note: str) -> None:
    cur.execute(
        """
        INSERT INTO audit_log(
            event_time, username, table_name, record_id, action, field_name,
            old_value, new_value, comment, actor_role, actor_name, source, telegram_user_id
        ) VALUES (?, 'operator', 'operator_task_queue', ?, 'status', 'status', ?, ?, ?,
                  'operator', 'Streamlit admin', 'admin_console/resident_requests', NULL)
        """,
        (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), str(request_id), old, new, note),
    )


def set_status(request_id: int, target: str, note: str) -> None:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        row = cur.execute(
            "SELECT status FROM operator_task_queue WHERE id=? AND origin='RESIDENT_PORTAL'", (request_id,)
        ).fetchone()
        if not row:
            raise RuntimeError("Заявка не найдена или не относится к кабинету жителя.")
        old = row[0]
        if old == target:
            return
        cur.execute(
            "UPDATE operator_task_queue SET status=?, updated_at=?, assigned_to=? WHERE id=?",
            (target, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), ACTOR if target == "IN_PROGRESS" else None, request_id),
        )
        audit_status_change(cur, request_id, old, target, note)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def apartment_vehicles(apartment_number: str | None) -> list[dict]:
    """Vehicles from the registry, used only to link a legacy free-text request."""
    if not apartment_number:
        return []
    conn = get_conn()
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT v.id, v.license_plate, v.license_plate_normalized,
                   v.car_model, v.lifecycle_status
            FROM vehicles v
            JOIN apartments a ON a.id=v.apartment_id
            WHERE a.apartment_number=? AND COALESCE(v.lifecycle_status, 'ACTIVE')='ACTIVE'
            ORDER BY v.id
            """,
            (str(apartment_number),),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def proposal_plate(request: dict, payload: dict) -> str | None:
    """Obtain a plate from either the new payload or an old text-only request."""
    raw = (payload.get("proposed") or {}).get("license_plate") or request.get("plate") or request.get("description")
    value, status = normalize_plate(raw)
    return value if value and status == "STANDARD" else None


def link_legacy_request_for_plate_review(request: dict, vehicle_id: int, proposed_plate: str) -> None:
    """Link an ambiguous legacy request for review; no value is confirmed here."""
    conn = get_conn()
    try:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        task = cur.execute(
            "SELECT * FROM operator_task_queue WHERE id=? AND origin='RESIDENT_PORTAL'", (request["id"],)
        ).fetchone()
        vehicle = cur.execute("SELECT * FROM vehicles WHERE id=?", (vehicle_id,)).fetchone()
        if not task or not vehicle:
            raise RuntimeError("Заявка или выбранный автомобиль не найдены.")
        normalized, plate_status = normalize_plate(proposed_plate)
        duplicate = cur.execute(
            "SELECT id FROM vehicles WHERE license_plate_normalized=? AND id != ?", (normalized, vehicle_id)
        ).fetchone()
        if duplicate:
            raise RuntimeError(f"Номер {normalized} уже закреплён за автомобилем #{duplicate[0]}.")
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        payload = {
            "current": {"license_plate": vehicle["license_plate"] or "—"},
            "proposed": {"license_plate": normalized},
            "source": "legacy resident free-text request",
            "plate_format_status": plate_status,
        }
        cur.execute(
            """
            UPDATE operator_task_queue
            SET task_type='RESIDENT_VEHICLE_UPDATE', vehicle_id=?, plate=?,
                title='Предложение жителя: проверить госномер', payload_json=?, updated_at=?
            WHERE id=?
            """,
            (vehicle_id, normalized, json.dumps(payload, ensure_ascii=False), timestamp, request["id"]),
        )
        cur.execute(
            """
            INSERT INTO audit_log(event_time, username, table_name, record_id, action, field_name,
                old_value, new_value, comment, actor_role, actor_name, source, telegram_user_id)
            VALUES (?, 'operator', 'operator_task_queue', ?, 'reclassify_request', '*', ?, ?, ?,
                    'operator', 'Streamlit admin', 'admin_console/resident_requests', ?)
            """,
            (
                timestamp, str(request["id"]),
                json.dumps({"task_type": task["task_type"], "vehicle_id": task["vehicle_id"], "plate": task["plate"]}, ensure_ascii=False),
                json.dumps({"task_type": "RESIDENT_VEHICLE_UPDATE", "vehicle_id": vehicle_id, "plate": normalized}, ensure_ascii=False),
                f"Старая заявка «добавить автомобиль» связана с авто #{vehicle_id} для проверки номера. "
                f"Житель указал {normalized}; реестр ещё не менялся и номер не подтверждён.",
                task["telegram_user_id"],
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def save_confirmed_plate(request: dict, confirmed_plate: str, note: str) -> None:
    """Save the operator-confirmed plate, never the resident suggestion by default."""
    vehicle_id = request.get("vehicle_id")
    if not vehicle_id:
        raise RuntimeError("В заявке нет связанного автомобиля.")
    normalized, plate_status = normalize_plate(confirmed_plate)
    if not normalized or plate_status != "STANDARD":
        raise RuntimeError("Введите проверенный номер в формате AA1234BB.")
    conn = get_conn()
    try:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        task = cur.execute(
            "SELECT * FROM operator_task_queue WHERE id=? AND origin='RESIDENT_PORTAL'", (request["id"],)
        ).fetchone()
        vehicle = cur.execute("SELECT * FROM vehicles WHERE id=?", (vehicle_id,)).fetchone()
        if not task or not vehicle or task["status"] != "IN_PROGRESS":
            raise RuntimeError("Сначала возьмите актуальную заявку в работу.")
        duplicate = cur.execute(
            "SELECT id FROM vehicles WHERE license_plate_normalized=? AND id != ?", (normalized, vehicle_id)
        ).fetchone()
        if duplicate:
            raise RuntimeError(f"Номер {normalized} уже закреплён за автомобилем #{duplicate[0]}.")
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        old_plate = vehicle["license_plate"]
        cur.execute(
            """
            UPDATE vehicles SET license_plate=?, license_plate_normalized=?, plate_format_status=?,
                updated_at=?, updated_by=?, review_status='VERIFIED_OPERATOR'
            WHERE id=?
            """,
            (normalized, normalized, plate_status, timestamp, ACTOR, vehicle_id),
        )
        close_note = note.strip() or f"Оператор проверил и сохранил госномер {normalized}."
        cur.execute(
            """
            UPDATE operator_task_queue
            SET status='RESOLVED', updated_at=?, closed_at=?, close_note=?
            WHERE id=?
            """,
            (timestamp, timestamp, close_note, request["id"]),
        )
        cur.execute(
            """
            INSERT INTO audit_log(event_time, username, table_name, record_id, action, field_name,
                old_value, new_value, comment, actor_role, actor_name, source, telegram_user_id)
            VALUES (?, 'operator', 'vehicles', ?, 'resident_plate_confirmed', 'license_plate', ?, ?, ?,
                    'operator', 'Streamlit admin', 'admin_console/resident_requests', ?)
            """,
            (timestamp, str(vehicle_id), old_plate, normalized, close_note, task["telegram_user_id"]),
        )
        audit_status_change(cur, request["id"], "IN_PROGRESS", "RESOLVED", close_note)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def create_vehicle_from_resident_request(
    request: dict, confirmed_plate: str, car_model: str, car_color: str,
    parking_time: str, note: str,
) -> int:
    """Create one confirmed vehicle and resolve a resident add-vehicle request."""
    apartment_number = str(request.get("apartment_number") or "").strip()
    normalized_plate, plate_status = normalize_plate(confirmed_plate)
    if not apartment_number:
        raise RuntimeError("В заявке не указана квартира.")
    if not normalized_plate or plate_status != "STANDARD":
        raise RuntimeError("Введите проверенный номер в формате AA1234BB.")
    model = (car_model or "").strip() or None
    color = (car_color or "").strip() or None
    model_normalized = normalize_car_model(model)
    color_normalized = normalize_color(color)
    conn = get_conn()
    try:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        task = cur.execute(
            "SELECT * FROM operator_task_queue WHERE id=? AND origin='RESIDENT_PORTAL'", (request["id"],)
        ).fetchone()
        if not task or task["status"] != "IN_PROGRESS":
            raise RuntimeError("Сначала возьмите актуальную заявку в работу.")
        apartment = cur.execute(
            "SELECT id FROM apartments WHERE apartment_number=?", (apartment_number,)
        ).fetchone()
        if not apartment:
            raise RuntimeError("Квартира из заявки не найдена в реестре.")
        duplicate = cur.execute(
            "SELECT id, apartment_id FROM vehicles WHERE license_plate_normalized=?", (normalized_plate,)
        ).fetchone()
        if duplicate:
            raise RuntimeError(
                f"Номер {normalized_plate} уже закреплён за автомобилем #{duplicate['id']}; новый дубль не создан."
            )
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cur.execute(
            """
            INSERT INTO vehicles(
                apartment_id, license_plate, license_plate_normalized, plate_format_status,
                car_model, car_model_normalized, car_color, car_color_normalized,
                parking_time, status, source, notes, created_at, created_by,
                lifecycle_status, review_status, created_source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'ACTIVE', 'resident_portal', ?, ?, ?,
                      'ACTIVE', 'VERIFIED_OPERATOR', ?)
            """,
            (
                apartment["id"], normalized_plate, normalized_plate, plate_status,
                model, model_normalized, color, color_normalized,
                parking_time or None, note.strip() or None, timestamp, ACTOR,
                f"resident request #{request['id']}",
            ),
        )
        vehicle_id = int(cur.lastrowid)
        close_note = note.strip() or f"Оператор подтвердил и добавил автомобиль {normalized_plate}."
        cur.execute(
            """
            UPDATE operator_task_queue
            SET status='RESOLVED', vehicle_id=?, plate=?, updated_at=?, closed_at=?, close_note=?
            WHERE id=?
            """,
            (vehicle_id, normalized_plate, timestamp, timestamp, close_note, request["id"]),
        )
        cur.execute(
            """
            INSERT INTO audit_log(event_time, username, table_name, record_id, action, field_name,
                old_value, new_value, comment, actor_role, actor_name, source, telegram_user_id)
            VALUES (?, 'operator', 'vehicles', ?, 'resident_vehicle_added', '*', '', ?, ?,
                    'operator', 'Streamlit admin', 'admin_console/resident_requests', ?)
            """,
            (
                timestamp, str(vehicle_id),
                json.dumps({"apartment_number": apartment_number, "license_plate": normalized_plate,
                            "car_model": model, "parking_time": parking_time or None,
                            "request_id": request["id"]}, ensure_ascii=False),
                close_note, task["telegram_user_id"],
            ),
        )
        audit_status_change(cur, request["id"], "IN_PROGRESS", "RESOLVED", close_note)
        conn.commit()
        return vehicle_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if "resident_request_id" not in st.session_state:
    st.session_state.resident_request_id = None

conn = get_conn()
try:
    queue_ready = table_exists(conn, "operator_task_queue")
finally:
    conn.close()
if not queue_ready:
    st.error("Таблица operator_task_queue пока не подключена к этой БД.")
    st.stop()

status_filter = st.radio("Показать", ["Открытые", "PENDING", "IN_PROGRESS", "Все"], horizontal=True)
if status_filter == "Открытые":
    rows = load_requests("Все")
    rows = rows[rows["Статус"].isin(["PENDING", "IN_PROGRESS"])]
else:
    rows = load_requests(status_filter)

if st.session_state.resident_request_id is None:
    if rows.empty:
        st.info("Заявок жителей в этом представлении нет.")
        st.stop()
    view = rows.copy()
    view.insert(0, "Открыть", False)
    edited = st.data_editor(
        view,
        hide_index=True,
        use_container_width=True,
        disabled=[column for column in view.columns if column != "Открыть"],
        column_config={"Открыть": st.column_config.CheckboxColumn("Открыть", default=False)},
        key="resident_request_grid",
    )
    selected = edited.loc[edited["Открыть"], "ID"].tolist()
    if len(selected) > 1:
        st.warning("Выберите одну заявку.")
    elif selected:
        if st.button("Открыть выбранную заявку", type="primary"):
            st.session_state.resident_request_id = int(selected[0])
            st.rerun()
    st.caption(f"Всего в представлении: {len(rows)}")
    st.stop()

request = load_request(int(st.session_state.resident_request_id))
if not request:
    st.error("Заявка не найдена.")
    if st.button("← К списку"):
        st.session_state.resident_request_id = None
        st.rerun()
    st.stop()

if st.button("← К списку"):
    st.session_state.resident_request_id = None
    st.rerun()

st.subheader(f"Заявка #{request['id']} — кв. {request.get('apartment_number') or '—'}")
left, right = st.columns(2)
with left:
    st.write(f"**Статус:** {request.get('status') or '—'}")
    st.write(f"**Тип:** {request.get('task_type') or '—'}")
    st.write(f"**Автомобиль:** {request.get('plate') or '—'}")
with right:
    resident = " ".join(part for part in [request.get("telegram_first_name"), request.get("telegram_last_name")] if part) or request.get("telegram_username") or request.get("telegram_user_id") or "—"
    st.write(f"**Житель:** {resident}")
    st.write(f"**Отправлено:** {request.get('created_at') or '—'}")
    st.write(f"**Заголовок:** {request.get('title') or '—'}")

payload: dict = {}
if request.get("payload_json"):
    try:
        payload = json.loads(request["payload_json"])
    except json.JSONDecodeError:
        st.warning("Структурированные данные заявки повреждены; используйте текст ниже.")

if payload:
    st.markdown("#### Данные из заявления жителя")
    current = payload.get("current") or {}
    proposed = payload.get("proposed") or {}
    comparison = pd.DataFrame([
        {"Поле": key, "Сейчас в реестре": value, "Житель указал": proposed.get(key, "—")}
        for key, value in current.items()
    ] + [
        {"Поле": key, "Сейчас в реестре": "—", "Житель указал": value}
        for key, value in proposed.items() if key not in current
    ])
    st.dataframe(comparison, hide_index=True, use_container_width=True)
    if payload.get("parking_end_reason"):
        st.write(f"**Причина прекращения парковки:** {payload['parking_end_reason']}")
    if payload.get("parking_end_date_unknown"):
        st.warning("Житель не указал точную дату последнего дня парковки.")

st.markdown("#### Текст заявления")
st.text(request.get("description") or "—")

# Earlier versions of the resident portal had only a free-text button
# "add a vehicle".  A resident may have used it to correct a typo instead;
# turn that ambiguous item into an explicit correction before touching data.
is_legacy_free_text = (
    request.get("task_type") == "RESIDENT_VEHICLE_CHANGE"
    and not request.get("vehicle_id")
    and not payload
)
if is_legacy_free_text:
    suggested_plate = proposal_plate(request, payload)
    candidates = apartment_vehicles(request.get("apartment_number"))
    st.markdown("#### Уточнить смысл старой заявки")
    if suggested_plate and candidates:
        st.info(
            f"Житель указал номер **{suggested_plate}**. Автомобиль уже может быть в реестре: "
            "выберите его, если заявление может относиться к этому авто, а не к добавлению нового."
        )
        labels = {
            vehicle["id"]: f"#{vehicle['id']} · {vehicle['license_plate'] or 'без номера'} · {vehicle['car_model'] or 'марка не указана'}"
            for vehicle in candidates
        }
        selected_vehicle_id = st.selectbox(
            "Какой автомобиль житель имел в виду?", list(labels), format_func=labels.get
        )
        selected_vehicle = next(vehicle for vehicle in candidates if vehicle["id"] == selected_vehicle_id)
        st.caption(
            f"После связывания оператор увидит: в реестре — {selected_vehicle['license_plate'] or '—'}, "
            f"житель указал — {suggested_plate}. Сам реестр на этом шаге не изменится."
        )
        if st.button("Связать для проверки номера", type="primary"):
            try:
                link_legacy_request_for_plate_review(request, int(selected_vehicle_id), suggested_plate)
                st.success("Заявка связана с автомобилем для проверки. Теперь возьмите её в работу и внесите проверенный номер.")
                st.rerun()
            except RuntimeError as exc:
                st.error(str(exc))
    elif not suggested_plate:
        st.warning("Из текста заявления нельзя получить корректный госномер. Уточните его у жителя.")
    else:
        st.warning("В реестре этой квартиры нет активных автомобилей для связывания.")

if request.get("task_type") == "RESIDENT_VEHICLE_PARKING_END":
    st.info("Эта заявка пока не меняет авто и начисления: для применения нужен операторский расчёт по периодам парковки и подневной корректировке.")
elif request.get("task_type") in {"RESIDENT_VEHICLE_UPDATE", "RESIDENT_VEHICLE_CHANGE", "RESIDENT_VEHICLE_ADD"}:
    st.info("После проверки оператор применит данные в карточке автомобиля. Автоматического изменения реестра нет.")

if request.get("task_type") == "RESIDENT_VEHICLE_ADD":
    st.markdown("#### Добавление автомобиля оператором")
    suggested_add_plate = proposal_plate(request, payload)
    if suggested_add_plate:
        st.write(f"Житель указал: **{suggested_add_plate}**. Это версия для проверки, а не номер для автоматического сохранения.")
    else:
        st.warning("Из текста заявления не удалось извлечь номер стандартного формата. Уточните его у жителя.")
    if request.get("status") == "IN_PROGRESS":
        confirmed_add_plate = st.text_input(
            "Проверенный госномер", placeholder="Например: AA6325PH",
            key=f"resident_add_plate_{request['id']}",
        )
        add_col1, add_col2 = st.columns(2)
        with add_col1:
            add_model = st.text_input("Марка / модель (если известна)", key=f"resident_add_model_{request['id']}")
        with add_col2:
            add_color = st.text_input("Цвет (если известен)", key=f"resident_add_color_{request['id']}")
        parking_choice = st.selectbox(
            "Режим парковки", ["Не указан", "Day", "Night", "Inactive"],
            key=f"resident_add_parking_{request['id']}",
        )
        add_note = st.text_input(
            "Комментарий оператора", value="Автомобиль проверен и добавлен оператором.",
            key=f"resident_add_note_{request['id']}",
        )
        confirm_add = st.checkbox(
            f"Подтверждаю добавление автомобиля в кв. {request.get('apartment_number') or '—'}",
            key=f"resident_add_confirm_{request['id']}",
        )
        if st.button("✅ Создать автомобиль и закрыть заявку", type="primary", disabled=not confirm_add):
            try:
                vehicle_id = create_vehicle_from_resident_request(
                    request, confirmed_add_plate, add_model, add_color,
                    None if parking_choice == "Не указан" else parking_choice, add_note,
                )
                st.success(f"Создан автомобиль #{vehicle_id}; заявка закрыта, операция записана в аудит.")
                st.rerun()
            except RuntimeError as exc:
                st.error(str(exc))
    elif request.get("status") == "PENDING":
        st.caption("Сначала нажмите «Взять в работу», затем станет доступен редактор добавления автомобиля.")

proposed_plate = (payload.get("proposed") or {}).get("license_plate")
current_plate = (payload.get("current") or {}).get("license_plate") or (payload.get("current") or {}).get("plate")
if request.get("task_type") == "RESIDENT_VEHICLE_UPDATE" and request.get("vehicle_id") and proposed_plate:
    st.markdown("#### Проверка номера оператором")
    st.write(f"Автомобиль #{request['vehicle_id']}: в реестре — **{current_plate or '—'}**; житель указал — **{proposed_plate}**.")
    st.warning("Номер из заявления не является подтверждённым. Сверьте его с надёжным источником и введите результат вручную.")
    if request.get("status") == "IN_PROGRESS":
        confirmed_plate = st.text_input(
            "Проверенный госномер", placeholder="Например: KA5740PH",
            key=f"resident_request_confirmed_plate_{request['id']}",
        )
        operator_note = st.text_input(
            "Комментарий оператору / жителю", value="Номер проверен оператором.",
            key=f"resident_request_resolution_{request['id']}",
        )
        if st.button("✅ Сохранить проверенный номер", type="primary"):
            try:
                save_confirmed_plate(request, confirmed_plate, operator_note)
                st.success("Проверенный номер сохранён, заявка закрыта; обе операции записаны в аудит.")
                st.rerun()
            except RuntimeError as exc:
                st.error(str(exc))
    elif request.get("status") == "PENDING":
        st.caption("Сначала нажмите «Взять в работу», затем станет доступно подтверждение исправления.")

if request.get("status") == "PENDING":
    if st.button("▶️ Взять в работу", type="primary"):
        set_status(int(request["id"]), "IN_PROGRESS", "Оператор взял заявку жителя в работу.")
        st.success("Заявка переведена в работу. Данные БД не менялись.")
        st.rerun()
elif request.get("status") == "IN_PROGRESS":
    if st.button("↩️ Вернуть в ожидание"):
        set_status(int(request["id"]), "PENDING", "Заявка возвращена в ожидание оператора.")
        st.success("Заявка снова ожидает рассмотрения.")
        st.rerun()
