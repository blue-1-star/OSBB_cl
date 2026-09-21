"""Operator inbox for structured resident self-service proposals.

The page shows the same current → proposed payload that the resident
submitted, lets an operator take a request into work, and records each
decision in audit_log. A sale/parking-end request has a separate reviewed
proration path: it creates the partial-month charge and its explicit payment
allocation before the vehicle can be archived.
"""

from __future__ import annotations

import json
import importlib.util
import re
import sqlite3
import sys
from calendar import monthrange
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


def triage_candidates(request: dict) -> list[dict]:
    """Requests of the same apartment that may be the actual subject of text."""
    apartment = str(request.get("apartment_number") or "").strip()
    if not apartment:
        return []
    conn = get_conn()
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT id, task_type, status, vehicle_id, plate, title, created_at, close_note
            FROM operator_task_queue
            WHERE apartment_number=? AND id != ?
            ORDER BY id DESC
            """,
            (apartment, request["id"]),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def resolve_as_superseded(request: dict, replacement_id: int, note: str) -> None:
    """Close an imprecise request in favour of a verified related request.

    The source text stays in operator_task_queue.description.  This function
    only records the editorial decision and never changes registry or billing
    data.
    """
    conn = get_conn()
    try:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        source = cur.execute("SELECT * FROM operator_task_queue WHERE id=?", (request["id"],)).fetchone()
        replacement = cur.execute("SELECT * FROM operator_task_queue WHERE id=?", (replacement_id,)).fetchone()
        if not source or not replacement:
            raise RuntimeError("Исходная или заменяющая заявка не найдена.")
        if source["status"] not in {"PENDING", "IN_PROGRESS", "NEEDS_CLARIFICATION"}:
            raise RuntimeError("Эта заявка уже закрыта.")
        if str(source["apartment_number"] or "") != str(replacement["apartment_number"] or ""):
            raise RuntimeError("Связывать можно только заявки одной квартиры.")
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        # The replacement ID is a fact, not an editable comment.  Keeping it
        # out of the Streamlit text-input default prevents a stale widget value
        # from recording a different application than the selected one.
        close_note = f"Заменена заявкой №{replacement_id}; исходный текст сохранён."
        if note.strip():
            close_note += f" {note.strip()}"
        cur.execute(
            """
            UPDATE operator_task_queue
            SET status='RESOLVED', updated_at=?, closed_at=?, close_note=?, assigned_to=NULL
            WHERE id=?
            """,
            (timestamp, timestamp, close_note, request["id"]),
        )
        cur.execute(
            """
            INSERT INTO audit_log(event_time, username, table_name, record_id, action, field_name,
                old_value, new_value, comment, actor_role, actor_name, source, telegram_user_id)
            VALUES (?, 'operator', 'operator_task_queue', ?, 'resident_request_superseded', 'status', ?, 'RESOLVED', ?,
                    'operator', 'Streamlit admin', 'admin_console/resident_requests', ?)
            """,
            (timestamp, str(request["id"]), source["status"], close_note, source["telegram_user_id"]),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def resolve_as_informational(request: dict, note: str) -> None:
    """Close a received confirmation that requires no registry action."""
    conn = get_conn()
    try:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        source = cur.execute("SELECT * FROM operator_task_queue WHERE id=?", (request["id"],)).fetchone()
        if not source or source["status"] not in {"PENDING", "IN_PROGRESS", "NEEDS_CLARIFICATION"}:
            raise RuntimeError("Эта заявка уже закрыта.")
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        close_note = note.strip() or "Подтверждение получено; изменений в реестре не требуется."
        cur.execute(
            """
            UPDATE operator_task_queue
            SET status='RESOLVED', updated_at=?, closed_at=?, close_note=?, assigned_to=NULL WHERE id=?
            """, (timestamp, timestamp, close_note, request["id"]),
        )
        cur.execute(
            """
            INSERT INTO audit_log(event_time, username, table_name, record_id, action, field_name,
                old_value, new_value, comment, actor_role, actor_name, source, telegram_user_id)
            VALUES (?, 'operator', 'operator_task_queue', ?, 'resident_request_informational', 'status', ?, 'RESOLVED', ?,
                    'operator', 'Streamlit admin', 'admin_console/resident_requests', ?)
            """,
            (timestamp, str(request["id"]), source["status"], close_note, source["telegram_user_id"]),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def reject_resident_request(request: dict, note: str) -> None:
    """Reject a concrete resident proposal without changing registry data."""
    message = note.strip()
    if not message:
        raise RuntimeError("Для отказа укажите понятный комментарий жителю.")
    conn = get_conn()
    try:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        task = cur.execute("SELECT * FROM operator_task_queue WHERE id=?", (request["id"],)).fetchone()
        if not task or task["status"] not in {"PENDING", "IN_PROGRESS", "NEEDS_CLARIFICATION"}:
            raise RuntimeError("Эта заявка уже закрыта.")
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cur.execute(
            """
            UPDATE operator_task_queue
            SET status='REJECTED', updated_at=?, closed_at=?, close_note=?, assigned_to=NULL WHERE id=?
            """,
            (timestamp, timestamp, message, request["id"]),
        )
        audit_status_change(cur, request["id"], task["status"], "REJECTED", message)
        cur.execute(
            """
            INSERT INTO audit_log(event_time, username, table_name, record_id, action, field_name,
                old_value, new_value, comment, actor_role, actor_name, source, telegram_user_id)
            VALUES (?, 'operator', 'operator_task_queue', ?, 'resident_request_rejected', 'status', ?, 'REJECTED', ?,
                    'operator', 'Streamlit admin', 'admin_console/resident_requests', ?)
            """,
            (timestamp, str(request["id"]), task["status"], message, task["telegram_user_id"]),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def request_messages(request_id: int) -> list[dict]:
    conn = get_conn()
    try:
        conn.row_factory = sqlite3.Row
        if not table_exists(conn, "resident_request_messages"):
            return []
        rows = conn.execute(
            """
            SELECT id, direction, message_text, delivery_status, created_by, created_at, sent_at, read_at,
                   delivery_error, COALESCE(message_kind, 'CLARIFICATION') AS message_kind
            FROM resident_request_messages WHERE request_id=? ORDER BY id
            """, (request_id,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def send_clarification_to_resident(request: dict, note: str) -> None:
    """Queue an auditable Telegram question and put this request on hold."""
    conn = get_conn()
    try:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        task = cur.execute("SELECT * FROM operator_task_queue WHERE id=?", (request["id"],)).fetchone()
        if not task or task["status"] not in {"PENDING", "IN_PROGRESS", "NEEDS_CLARIFICATION"}:
            raise RuntimeError("Уточнение можно запросить только по актуальной открытой заявке.")
        if not table_exists(conn, "resident_request_messages"):
            raise RuntimeError("Не применена миграция 2026-09-21_016 для переписки по заявкам.")
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        message = note.strip() or (
            "Уточните, пожалуйста, данные в вашем обращении."
        )
        cur.execute(
            """
            UPDATE operator_task_queue
            SET status='NEEDS_CLARIFICATION', assigned_to=NULL, updated_at=?, close_note=? WHERE id=?
            """, (timestamp, message, request["id"]),
        )
        cur.execute(
            """
            INSERT INTO resident_request_messages(
                request_id, telegram_user_id, direction, message_text, delivery_status, created_by, created_at
            ) VALUES (?, ?, 'OPERATOR_TO_RESIDENT', ?, 'READY', ?, ?)
            """, (request["id"], task["telegram_user_id"], message, ACTOR, timestamp),
        )
        message_id = int(cur.lastrowid)
        audit_status_change(cur, request["id"], task["status"], "NEEDS_CLARIFICATION", message)
        cur.execute(
            """
            INSERT INTO audit_log(event_time, username, table_name, record_id, action, field_name,
                old_value, new_value, comment, actor_role, actor_name, source, telegram_user_id)
            VALUES (?, 'operator', 'resident_request_messages', ?, 'resident_clarification_queued', 'delivery_status',
                    '', 'READY', ?, 'operator', 'Streamlit admin', 'admin_console/resident_requests', ?)
            """, (timestamp, str(message_id), f"Вопрос по заявке #{request['id']} поставлен в очередь Telegram.", task["telegram_user_id"]),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def request_parking_mode_clarification(request: dict, note: str) -> None:
    """Compatibility wrapper for the parking-end screen."""
    send_clarification_to_resident(request, note)


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
    """Obtain a standard plate from structured or older free-text proposals."""
    raw = (payload.get("proposed") or {}).get("license_plate") or request.get("plate") or request.get("description")
    value, status = normalize_plate(raw)
    if value and status == "STANDARD":
        return value
    # A resident often writes a sentence such as "my car AA 1234 BB".  The
    # whole sentence cannot be normalised as a plate, but its plate fragment can.
    for candidate in re.findall(r"(?i)[A-ZА-ЯІЇЄҐ]{2}\s*\d{4}\s*[A-ZА-ЯІЇЄҐ]{2}", str(raw or "")):
        value, status = normalize_plate(candidate)
        if value and status == "STANDARD":
            return value
    return None


def _plate_distance(left: str, right: str) -> int:
    if len(left) < len(right):
        left, right = right, left
    previous = list(range(len(right) + 1))
    for index, left_char in enumerate(left, start=1):
        current = [index]
        for right_index, right_char in enumerate(right, start=1):
            current.append(min(
                current[-1] + 1,
                previous[right_index] + 1,
                previous[right_index - 1] + (left_char != right_char),
            ))
        previous = current
    return previous[-1]


def vehicle_plate_clarification_hint(request: dict, payload: dict) -> str | None:
    """Prepare a resident question only when registry/video evidence conflicts."""
    if request.get("task_type") != "RESIDENT_VEHICLE_UPDATE" or not request.get("vehicle_id"):
        return None
    proposed = proposal_plate(request, payload)
    current = (payload.get("current") or {}).get("license_plate")
    current, current_status = normalize_plate(current)
    if not proposed or not current or current_status != "STANDARD" or proposed == current:
        return None
    conn = get_conn()
    try:
        conn.row_factory = sqlite3.Row
        if not table_exists(conn, "video_plate_evidence"):
            return None
        vehicle = conn.execute("SELECT car_model FROM vehicles WHERE id=?", (request["vehicle_id"],)).fetchone()
        rows = conn.execute(
            """
            SELECT plate_normalized, consensus_model, observation_count, evidence_status
            FROM video_plate_evidence
            WHERE evidence_status='CONSENSUS' AND observation_count>=3
            """
        ).fetchall()
        best = None
        for row in rows:
            plate = str(row["plate_normalized"] or "")
            distance = min(_plate_distance(plate, current), _plate_distance(plate, proposed))
            model_match = bool(
                vehicle and vehicle["car_model"] and row["consensus_model"]
                and str(vehicle["car_model"]).upper().split()[0] in str(row["consensus_model"]).upper()
            )
            if distance <= 2 and (model_match or distance <= 1):
                score = (distance, -int(row["observation_count"] or 0))
                if best is None or score < best[0]:
                    best = (score, dict(row))
        if not best:
            return None
        evidence = best[1]
        return (
            "Уточните, пожалуйста, госномер автомобиля. "
            f"В реестре сейчас указан {current}, в вашем предложении — {proposed}. "
            f"В видео-наблюдениях {int(evidence['observation_count'])} раз распознан "
            f"{evidence['plate_normalized']} ({evidence['consensus_model'] or 'модель не определена'}). "
            "Подтвердите правильный номер или напишите другой."
        )
    finally:
        conn.close()


def vehicle_add_recommendation(request: dict, payload: dict) -> dict:
    """Prepare an operator recommendation without ever creating a vehicle.

    Green means that a *new* standard plate has independent corroboration.  It
    deliberately does not mean automatic insertion: the operator still presses
    the final confirmation button.  An exact registry match is RED, because it
    is a duplicate-risk signal, not evidence for a second vehicle.
    """
    plate = proposal_plate(request, payload)
    result: dict = {
        "level": "YELLOW", "plate": plate, "model": None, "color": None,
        "reasons": [], "video": None, "historic_rows": [], "registry_rows": [],
    }
    if not plate:
        result["reasons"].append("Из текста не выделен госномер стандартного формата AA1234BB.")
        return result

    conn = get_conn()
    try:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        result["registry_rows"] = [dict(row) for row in cur.execute(
            """
            SELECT v.id, v.lifecycle_status, a.apartment_number
            FROM vehicles v LEFT JOIN apartments a ON a.id=v.apartment_id
            WHERE v.license_plate_normalized=?
            """, (plate,)
        ).fetchall()]
        if result["registry_rows"]:
            locations = ", ".join(
                f"#{row['id']} (кв. {row['apartment_number'] or '—'}, {row['lifecycle_status']})"
                for row in result["registry_rows"]
            )
            result["level"] = "RED"
            result["reasons"].append(f"Номер уже есть в главном реестре: {locations}. Новый автомобиль создавать нельзя.")
            return result

        if table_exists(conn, "video_plate_evidence"):
            row = cur.execute(
                """
                SELECT plate_normalized, consensus_model, matching_observations, model_agreement,
                       evidence_status, observation_count
                FROM video_plate_evidence WHERE plate_normalized=?
                """, (plate,)
            ).fetchone()
            if row:
                result["video"] = dict(row)
                if row["consensus_model"]:
                    result["model"] = normalize_car_model(row["consensus_model"]) or row["consensus_model"]

        # The historical old-bot import is a distinct source from the new
        # resident's current message.  It only corroborates a new vehicle when
        # it names the same apartment; other apartments remain a yellow warning.
        if table_exists(conn, "tbot_parking_import"):
            historic_rows = cur.execute(
                "SELECT apartment_number, license_plate, car_model, car_color, source FROM tbot_parking_import"
            ).fetchall()
            for row in historic_rows:
                normalised, status = normalize_plate(row["license_plate"])
                if normalised == plate and status == "STANDARD":
                    result["historic_rows"].append(dict(row))
            for row in result["historic_rows"]:
                if str(row.get("apartment_number") or "").strip() == str(request.get("apartment_number") or "").strip():
                    if not result["model"] and row.get("car_model"):
                        result["model"] = normalize_car_model(row["car_model"]) or row["car_model"]
                    if row.get("car_color"):
                        result["color"] = normalize_color(row["car_color"]) or row["car_color"]

        video = result["video"] or {}
        reliable_video = (
            int(video.get("observation_count") or 0) >= 2
            # DOMINANT_REVIEW is intentionally not enough: its model agreement
            # is below the documented 75% reliability threshold.
            and video.get("evidence_status") == "CONSENSUS"
        )
        same_apartment_history = any(
            str(row.get("apartment_number") or "").strip() == str(request.get("apartment_number") or "").strip()
            for row in result["historic_rows"]
        )
        if reliable_video:
            result["reasons"].append(
                f"Точное совпадение в видео: {video['observation_count']} наблюдения, статус модели {video['evidence_status']}."
            )
        elif video:
            result["reasons"].append(
                f"В видео номер встретился {video['observation_count']} раз, но этого пока недостаточно для зелёной рекомендации."
            )
        if same_apartment_history:
            result["reasons"].append("Тот же номер указан для этой квартиры в историческом импорте старого бота.")
        elif result["historic_rows"]:
            result["reasons"].append("Номер есть в историческом импорте, но у другой квартиры: требуется ручная проверка.")

        if reliable_video or same_apartment_history:
            result["level"] = "GREEN"
        else:
            result["reasons"].append("Номер очищен и подготовлен для оператора, но независимого подтверждения пока нет.")
        return result
    finally:
        conn.close()


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


def save_confirmed_vehicle_field(request: dict, field: str, confirmed_value: str, note: str) -> None:
    """Apply exactly one structured resident-proposed vehicle field, with audit."""
    vehicle_id = int(request.get("vehicle_id") or 0)
    allowed = {"license_plate", "car_model", "car_color", "parking_time"}
    if not vehicle_id or field not in allowed:
        raise RuntimeError("Заявка не содержит поддерживаемого поля автомобиля.")
    conn = get_conn()
    try:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        task = cur.execute("SELECT * FROM operator_task_queue WHERE id=?", (request["id"],)).fetchone()
        vehicle = cur.execute("SELECT * FROM vehicles WHERE id=?", (vehicle_id,)).fetchone()
        if not task or not vehicle or task["status"] != "IN_PROGRESS":
            raise RuntimeError("Сначала возьмите актуальную заявку в работу.")
        payload = json.loads(task["payload_json"] or "{}")
        if field not in (payload.get("proposed") or {}):
            raise RuntimeError("Выбранное поле отсутствует в структурированном заявлении жителя.")
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        old_value = None
        if field == "license_plate":
            normalized, status = normalize_plate(confirmed_value)
            if not normalized or status != "STANDARD":
                raise RuntimeError("Введите проверенный номер в формате AA1234BB.")
            duplicate = cur.execute(
                "SELECT id FROM vehicles WHERE license_plate_normalized=? AND id<>?", (normalized, vehicle_id)
            ).fetchone()
            if duplicate:
                raise RuntimeError(f"Номер {normalized} уже закреплён за автомобилем #{duplicate['id']}.")
            old_value, new_value = vehicle["license_plate"], normalized
            cur.execute(
                """UPDATE vehicles SET license_plate=?, license_plate_normalized=?, plate_format_status=?,
                   review_status='VERIFIED_OPERATOR', updated_at=?, updated_by=? WHERE id=?""",
                (normalized, normalized, status, timestamp, ACTOR, vehicle_id),
            )
        elif field == "car_model":
            value = confirmed_value.strip()
            if not value:
                raise RuntimeError("Укажите марку/модель или верните заявку для уточнения.")
            old_value, new_value = vehicle["car_model"], value
            cur.execute(
                """UPDATE vehicles SET car_model=?, car_model_normalized=?, review_status='VERIFIED_OPERATOR',
                   updated_at=?, updated_by=? WHERE id=?""",
                (value, normalize_car_model(value), timestamp, ACTOR, vehicle_id),
            )
        elif field == "car_color":
            value = confirmed_value.strip()
            if not value:
                raise RuntimeError("Укажите цвет или верните заявку для уточнения.")
            old_value, new_value = vehicle["car_color"], value
            cur.execute(
                """UPDATE vehicles SET car_color=?, car_color_normalized=?, review_status='VERIFIED_OPERATOR',
                   updated_at=?, updated_by=? WHERE id=?""",
                (value, normalize_color(value), timestamp, ACTOR, vehicle_id),
            )
        else:
            value = confirmed_value.strip()
            if value not in {"Day", "Night", "Inactive"}:
                raise RuntimeError("Выберите режим Day, Night или Inactive.")
            old_value, new_value = vehicle["parking_time"], value
            cur.execute(
                """UPDATE vehicles SET parking_time=?, review_status='VERIFIED_OPERATOR', updated_at=?, updated_by=?
                   WHERE id=?""", (value, timestamp, ACTOR, vehicle_id),
            )
        close_note = note.strip() or f"Оператор подтвердил поле {field}: {new_value}."
        cur.execute(
            """UPDATE operator_task_queue SET status='RESOLVED', updated_at=?, closed_at=?, close_note=? WHERE id=?""",
            (timestamp, timestamp, close_note, request["id"]),
        )
        cur.execute(
            """
            INSERT INTO audit_log(event_time, username, table_name, record_id, action, field_name,
                old_value, new_value, comment, actor_role, actor_name, source, telegram_user_id)
            VALUES (?, 'operator', 'vehicles', ?, 'resident_vehicle_field_confirmed', ?, ?, ?, ?,
                    'operator', 'Streamlit admin', 'admin_console/resident_requests', ?)
            """,
            (timestamp, str(vehicle_id), field, str(old_value or ""), str(new_value), close_note, task["telegram_user_id"]),
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
    parking_time: str, note: str, recommendation: dict | None = None,
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
                            "request_id": request["id"],
                            "operator_recommendation": recommendation or {}}, ensure_ascii=False),
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


def vehicle_removal_safety(vehicle_id: int, *, exclude_task_id: int | None = None) -> dict:
    """Return every persistent link that makes physical vehicle deletion unsafe."""
    conn = get_conn()
    try:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        vehicle = cur.execute("SELECT * FROM vehicles WHERE id=?", (vehicle_id,)).fetchone()
        if not vehicle:
            raise RuntimeError("Автомобиль уже отсутствует в реестре.")
        checks = [
            ("payments", "Оплаты", "vehicle_id=?"),
            ("charges", "Начисления", "vehicle_id=?"),
            ("cashbox_operations", "Кассовые операции", "vehicle_id=?"),
            ("adjustment_assignments", "Корректировки", "vehicle_id=?"),
            ("resident_profile_change_requests", "Заявки профиля", "vehicle_id=?"),
            ("parking_time_review_tasks", "Задачи режима парковки", "vehicle_id=?"),
            ("vehicle_import_batch_items", "Строки партий импорта", "vehicle_id=?"),
        ]
        blockers: list[dict] = []
        financial_count = 0
        for table, label, where in checks:
            if not table_exists(conn, table):
                continue
            count = int(cur.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}", (vehicle_id,)).fetchone()[0] or 0)
            if count:
                blockers.append({"table": table, "label": label, "count": count})
                if table in {"payments", "charges", "cashbox_operations", "adjustment_assignments"}:
                    financial_count += count
        if table_exists(conn, "operator_task_queue"):
            params: list[object] = [vehicle_id]
            where = "vehicle_id=? AND status IN ('PENDING', 'IN_PROGRESS')"
            if exclude_task_id is not None:
                where += " AND id<>?"
                params.append(exclude_task_id)
            count = int(cur.execute(f"SELECT COUNT(*) FROM operator_task_queue WHERE {where}", tuple(params)).fetchone()[0] or 0)
            if count:
                blockers.append({"table": "operator_task_queue", "label": "Другие открытые заявки", "count": count})
        return {"vehicle": dict(vehicle), "blockers": blockers, "financial_count": financial_count}
    finally:
        conn.close()


def reclassify_as_mistaken_vehicle_remove(request: dict) -> None:
    """Turn a legacy free-text 'other' request into an explicit remove request."""
    if not request.get("vehicle_id"):
        raise RuntimeError("В заявке не указан автомобиль для удаления.")
    conn = get_conn()
    try:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        task = cur.execute("SELECT * FROM operator_task_queue WHERE id=?", (request["id"],)).fetchone()
        vehicle = cur.execute("SELECT * FROM vehicles WHERE id=?", (request["vehicle_id"],)).fetchone()
        if not task or not vehicle or task["status"] not in {"PENDING", "IN_PROGRESS"}:
            raise RuntimeError("Заявка или автомобиль больше не доступны для переопределения.")
        payload = json.loads(task["payload_json"] or "{}")
        payload["proposed"] = {"action": "REMOVE"}
        payload["removal_reason"] = "MISTAKEN_ENTRY"
        payload["reclassified_from"] = task["task_type"]
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cur.execute(
            """
            UPDATE operator_task_queue
            SET task_type='RESIDENT_VEHICLE_REMOVE', title='Предложение жителя: автомобиль внесён ошибочно',
                payload_json=?, updated_at=? WHERE id=?
            """,
            (json.dumps(payload, ensure_ascii=False, sort_keys=True), timestamp, request["id"]),
        )
        cur.execute(
            """
            INSERT INTO audit_log(event_time, username, table_name, record_id, action, field_name,
                old_value, new_value, comment, actor_role, actor_name, source, telegram_user_id)
            VALUES (?, 'operator', 'operator_task_queue', ?, 'reclassify_request', 'task_type', ?, ?, ?,
                    'operator', 'Streamlit admin', 'admin_console/resident_requests', ?)
            """,
            (timestamp, str(request["id"]), task["task_type"], "RESIDENT_VEHICLE_REMOVE",
             "Свободный текст жителя переопределён оператором как «автомобиль внесён ошибочно»; исходный текст сохранён в заявке.",
             task["telegram_user_id"]),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def resolve_mistaken_vehicle_remove(request: dict, *, delete: bool, note: str) -> None:
    """Delete an unlinked mistaken vehicle or archive a historically linked one."""
    vehicle_id = int(request.get("vehicle_id") or 0)
    if not vehicle_id:
        raise RuntimeError("В заявке не указан автомобиль.")
    conn = get_conn()
    try:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        task = cur.execute("SELECT * FROM operator_task_queue WHERE id=?", (request["id"],)).fetchone()
        vehicle = cur.execute("SELECT * FROM vehicles WHERE id=?", (vehicle_id,)).fetchone()
        if not task or not vehicle or task["task_type"] != "RESIDENT_VEHICLE_REMOVE" or task["status"] != "IN_PROGRESS":
            raise RuntimeError("Нужна актуальная заявка RESIDENT_VEHICLE_REMOVE в статусе IN_PROGRESS.")

        def count(table: str) -> int:
            if not table_exists(conn, table):
                return 0
            return int(cur.execute(f"SELECT COUNT(*) FROM {table} WHERE vehicle_id=?", (vehicle_id,)).fetchone()[0] or 0)

        blocked = {table: count(table) for table in (
            "payments", "charges", "cashbox_operations", "adjustment_assignments",
            "resident_profile_change_requests", "parking_time_review_tasks", "vehicle_import_batch_items",
        )}
        other_open_tasks = int(cur.execute(
            """
            SELECT COUNT(*) FROM operator_task_queue
            WHERE vehicle_id=? AND id<>? AND status IN ('PENDING', 'IN_PROGRESS')
            """, (vehicle_id, request["id"])
        ).fetchone()[0] or 0)
        if other_open_tasks:
            blocked["other_open_operator_tasks"] = other_open_tasks
        blocked = {table: value for table, value in blocked.items() if value}
        if delete and blocked:
            details = ", ".join(f"{table}: {value}" for table, value in blocked.items())
            raise RuntimeError(f"Физическое удаление запрещено: найдены связанные записи ({details}). Используйте архивирование.")

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        old = dict(vehicle)
        if delete:
            cur.execute("DELETE FROM vehicles WHERE id=?", (vehicle_id,))
            action, outcome = "resident_vehicle_deleted_mistaken_entry", "DELETED"
            close_note = note.strip() or "Автомобиль был внесён ошибочно и удалён: финансовых и иных связей нет."
            new_value = {"deleted": True, "vehicle_id": vehicle_id, "blockers": {}}
        else:
            cur.execute(
                """
                UPDATE vehicles SET lifecycle_status='ARCHIVED', status='ARCHIVED', archived_at=?,
                    archive_reason='MISTAKEN_ENTRY_WITH_HISTORY', archived_by=?, updated_at=?, updated_by=?
                WHERE id=?
                """,
                (timestamp, ACTOR, timestamp, ACTOR, vehicle_id),
            )
            action, outcome = "resident_vehicle_archived_mistaken_entry", "ARCHIVED"
            close_note = note.strip() or "Автомобиль отмечен ошибочно внесённым и архивирован: связанная история сохранена."
            new_value = {"lifecycle_status": "ARCHIVED", "archive_reason": "MISTAKEN_ENTRY_WITH_HISTORY", "blockers": blocked}
        cur.execute(
            """
            UPDATE operator_task_queue
            SET status='RESOLVED', updated_at=?, closed_at=?, close_note=? WHERE id=?
            """, (timestamp, timestamp, close_note, request["id"]),
        )
        cur.execute(
            """
            INSERT INTO audit_log(event_time, username, table_name, record_id, action, field_name,
                old_value, new_value, comment, actor_role, actor_name, source, telegram_user_id)
            VALUES (?, 'operator', 'vehicles', ?, ?, '*', ?, ?, ?, 'operator', 'Streamlit admin',
                    'admin_console/resident_requests', ?)
            """,
            (timestamp, str(vehicle_id), action, json.dumps(old, ensure_ascii=False),
             json.dumps(new_value, ensure_ascii=False), close_note, task["telegram_user_id"]),
        )
        audit_status_change(cur, request["id"], "IN_PROGRESS", "RESOLVED", close_note)
        conn.commit()
        return outcome
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def parking_end_context(request: dict, payload: dict) -> dict:
    """Read facts needed before a parking end can be financially resolved."""
    vehicle_id = int(request.get("vehicle_id") or 0)
    if not vehicle_id:
        raise RuntimeError("В заявке не указан автомобиль.")
    end_date = str((payload.get("proposed") or {}).get("parking_end_date") or "").strip()
    try:
        effective_date = datetime.strptime(end_date, "%Y-%m-%d").date()
    except ValueError as exc:
        raise RuntimeError("В заявке нет корректной даты последнего дня парковки.") from exc
    conn = get_conn()
    try:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        vehicle = cur.execute("SELECT * FROM vehicles WHERE id=?", (vehicle_id,)).fetchone()
        if not vehicle:
            raise RuntimeError("Автомобиль уже отсутствует в реестре.")
        charges = []
        if table_exists(conn, "charges"):
            charges = [dict(row) for row in cur.execute(
                "SELECT id, period_code, amount, net_amount, status FROM charges WHERE vehicle_id=? ORDER BY period_code, id",
                (vehicle_id,),
            ).fetchall()]
        payments = []
        if table_exists(conn, "payments"):
            payments = [dict(row) for row in cur.execute(
                "SELECT id, payment_date, period_code, amount FROM payments WHERE vehicle_id=? ORDER BY id",
                (vehicle_id,),
            ).fetchall()]
        apartment_number = str(request.get("apartment_number") or "").strip()
        apartment_charges = []
        apartment_payments = []
        if apartment_number and table_exists(conn, "charges"):
            apartment_charges = [dict(row) for row in cur.execute(
                "SELECT id, period_code, vehicle_id, amount, status FROM charges WHERE apartment_number=? ORDER BY period_code, id",
                (apartment_number,),
            ).fetchall()]
        if apartment_number and table_exists(conn, "payments"):
            apartment_payments = [dict(row) for row in cur.execute(
                """SELECT id, payment_date, period_code, vehicle_id, amount, service_type,
                          base_service_code, service_item_code
                   FROM payments WHERE apartment_number=? ORDER BY id""",
                (apartment_number,),
            ).fetchall()]
        mode_signals = {
            "Night" if "NIGHT" in " ".join(str(payment.get(key) or "") for key in ("service_type", "base_service_code", "service_item_code")).upper()
            else "Day" if "DAY" in " ".join(str(payment.get(key) or "") for key in ("service_type", "base_service_code", "service_item_code")).upper()
            else ""
            for payment in apartment_payments
        }
        mode_signals.discard("")
        mode_suggestion = None
        if len(mode_signals) == 1:
            suggested_mode = next(iter(mode_signals))
            supporting = [payment for payment in apartment_payments if suggested_mode.upper() in " ".join(
                str(payment.get(key) or "") for key in ("service_type", "base_service_code", "service_item_code")
            ).upper()]
            mode_suggestion = {
                "mode": suggested_mode, "payment_count": len(supporting),
                "periods": sorted({str(payment.get("period_code") or "") for payment in supporting if payment.get("period_code")}),
                "source": "apartment_payment_service_type",
            }
        return {
            "vehicle": dict(vehicle), "effective_date": effective_date.isoformat(),
            "reason": str(payload.get("parking_end_reason") or "NO_LONGER_PARKS"),
            "charges": charges, "payments": payments,
            "apartment_charges": apartment_charges, "apartment_payments": apartment_payments,
            "mode_suggestion": mode_suggestion,
            "mode": str(vehicle["parking_time"] or "").strip(),
        }
    finally:
        conn.close()


def accept_parking_mode_suggestion(request: dict, suggestion: dict, note: str) -> None:
    """Accept an evidenced yellow recommendation; never invent a tariff mode."""
    mode = str(suggestion.get("mode") or "")
    if mode not in {"Day", "Night"}:
        raise RuntimeError("Нет однозначной рекомендации режима для подтверждения.")
    vehicle_id = int(request.get("vehicle_id") or 0)
    conn = get_conn()
    try:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        task = cur.execute("SELECT * FROM operator_task_queue WHERE id=?", (request["id"],)).fetchone()
        vehicle = cur.execute("SELECT * FROM vehicles WHERE id=?", (vehicle_id,)).fetchone()
        if not task or not vehicle or task["status"] != "IN_PROGRESS":
            raise RuntimeError("Нужна актуальная заявка в статусе IN_PROGRESS.")
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        evidence = (
            f"Подтверждена рекомендация {mode}: {suggestion.get('payment_count', 0)} оплат(ы), "
            f"периоды {', '.join(suggestion.get('periods') or []) or '—'}, источник {suggestion.get('source')}."
        )
        cur.execute(
            "UPDATE vehicles SET parking_time=?, updated_at=?, updated_by=?, review_status='VERIFIED_OPERATOR' WHERE id=?",
            (mode, timestamp, ACTOR, vehicle_id),
        )
        cur.execute(
            """
            INSERT INTO audit_log(event_time, username, table_name, record_id, action, field_name,
                old_value, new_value, comment, actor_role, actor_name, source, telegram_user_id)
            VALUES (?, 'operator', 'vehicles', ?, 'parking_mode_accepted_from_payment_evidence', 'parking_time', ?, ?, ?,
                    'operator', 'Streamlit admin', 'admin_console/resident_requests', ?)
            """,
            (timestamp, str(vehicle_id), vehicle["parking_time"] or "", mode,
             (note.strip() + " " if note.strip() else "") + evidence, task["telegram_user_id"]),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def close_duplicate_parking_end_tasks(
    cur: sqlite3.Cursor, request: dict, vehicle_id: int, effective_date: str, timestamp: str
) -> None:
    """Close only same-date sale duplicates once one of them has been resolved."""
    duplicate_rows = cur.execute(
        """
        SELECT id, status, payload_json FROM operator_task_queue
        WHERE id != ? AND vehicle_id=? AND task_type='RESIDENT_VEHICLE_PARKING_END'
          AND status IN ('PENDING', 'IN_PROGRESS', 'NEEDS_CLARIFICATION')
        """,
        (request["id"], vehicle_id),
    ).fetchall()
    for duplicate in duplicate_rows:
        try:
            duplicate_payload = json.loads(duplicate["payload_json"] or "{}")
            duplicate_date = str((duplicate_payload.get("proposed") or {}).get("parking_end_date") or "")
        except (TypeError, ValueError):
            duplicate_date = ""
        if duplicate_date != effective_date:
            continue
        duplicate_note = (
            f"Дубликат заявления закрыт: продажа и подневный расчёт выполнены "
            f"по заявке #{request['id']}."
        )
        cur.execute(
            "UPDATE operator_task_queue SET status='RESOLVED', updated_at=?, closed_at=?, close_note=? WHERE id=?",
            (timestamp, timestamp, duplicate_note, duplicate["id"]),
        )
        audit_status_change(cur, duplicate["id"], duplicate["status"], "RESOLVED", duplicate_note)


def archive_parking_end_pending_reconciliation(request: dict, payload: dict, note: str) -> int:
    """Accept the sale date without making a resident wait for cash reconciliation.

    Billing is deliberately moved to a system operator task.  No charge,
    allocation, refund or payment record is guessed or changed here.
    """
    context = parking_end_context(request, payload)
    vehicle_id = int(request.get("vehicle_id") or 0)
    if not vehicle_id:
        raise RuntimeError("В заявке не указан автомобиль.")
    conn = get_conn()
    try:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        task = cur.execute("SELECT * FROM operator_task_queue WHERE id=?", (request["id"],)).fetchone()
        vehicle = cur.execute("SELECT * FROM vehicles WHERE id=?", (vehicle_id,)).fetchone()
        if not task or not vehicle or task["status"] != "IN_PROGRESS":
            raise RuntimeError("Нужна актуальная заявка в статусе IN_PROGRESS.")
        columns = {row[1] for row in cur.execute("PRAGMA table_info(vehicles)")}
        required = {"parking_end_date", "parking_end_reason", "parking_end_recorded_at", "parking_end_financial_status"}
        if not required.issubset(columns):
            raise RuntimeError("Не применена миграция 2026-09-21_015 для хранения даты прекращения парковки.")
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        mode = context["mode"] or "UNSPECIFIED"
        financial_status = (
            "PENDING_RECONCILIATION" if mode in {"Day", "Night"}
            else "PENDING_MODE_AND_RECONCILIATION"
        )
        old = dict(vehicle)
        cur.execute(
            """
            UPDATE vehicles SET lifecycle_status='ARCHIVED', status='ARCHIVED', archived_at=?, archive_reason=?,
                archived_by=?, parking_end_date=?, parking_end_reason=?, parking_end_recorded_at=?,
                parking_end_financial_status=?, updated_at=?, updated_by=? WHERE id=?
            """,
            (timestamp, context["reason"], ACTOR, context["effective_date"], context["reason"], timestamp,
             financial_status, timestamp, ACTOR, vehicle_id),
        )
        existing = cur.execute(
            """
            SELECT id FROM operator_task_queue
            WHERE vehicle_id=? AND task_type='PARKING_END_FINANCIAL_RECONCILIATION'
              AND status IN ('PENDING', 'IN_PROGRESS', 'NEEDS_CLARIFICATION')
            ORDER BY id DESC LIMIT 1
            """, (vehicle_id,),
        ).fetchone()
        if existing:
            finance_task_id = int(existing["id"])
        else:
            finance_payload = {
                "schema_version": 1,
                "source_resident_request_id": request["id"],
                "vehicle_id": vehicle_id,
                "apartment_number": request.get("apartment_number"),
                "parking_end_date": context["effective_date"],
                "parking_mode": mode,
                "reason": context["reason"],
                "direct_charges": context["charges"],
                "direct_payments": context["payments"],
                "apartment_charges": context["apartment_charges"],
                "apartment_payments": context["apartment_payments"],
            }
            cur.execute(
                """
                INSERT INTO operator_task_queue(
                    priority, task_type, status, apartment_number, vehicle_id, plate,
                    title, description, origin, created_by, created_at, updated_at, payload_json
                ) VALUES ('HIGH', 'PARKING_END_FINANCIAL_RECONCILIATION', 'PENDING', ?, ?, ?, ?, ?,
                          'SYSTEM', ?, ?, ?, ?)
                """,
                (
                    request.get("apartment_number"), vehicle_id, vehicle["license_plate"],
                    f"Сверка расчётов после продажи авто #{vehicle_id}",
                    f"Автомобиль архивирован по заявлению жителя; последний день парковки: "
                    f"{context['effective_date']} включительно. Финансы требуют отдельной сверки.",
                    ACTOR, timestamp, timestamp, json.dumps(finance_payload, ensure_ascii=False),
                ),
            )
            finance_task_id = int(cur.lastrowid)
        close_note = note.strip() or (
            f"Продажа принята: автомобиль исключён из реестра с {context['effective_date']} включительно. "
            f"Финансовая сверка вынесена в задачу #{finance_task_id}."
        )
        cur.execute(
            "UPDATE operator_task_queue SET status='RESOLVED', updated_at=?, closed_at=?, close_note=? WHERE id=?",
            (timestamp, timestamp, close_note, request["id"]),
        )
        close_duplicate_parking_end_tasks(cur, request, vehicle_id, context["effective_date"], timestamp)
        cur.execute(
            """
            INSERT INTO audit_log(event_time, username, table_name, record_id, action, field_name,
                old_value, new_value, comment, actor_role, actor_name, source, telegram_user_id)
            VALUES (?, 'operator', 'vehicles', ?, 'resident_vehicle_archived_pending_reconciliation', '*', ?, ?, ?,
                    'operator', 'Streamlit admin', 'admin_console/resident_requests', ?)
            """,
            (timestamp, str(vehicle_id), json.dumps(old, ensure_ascii=False),
             json.dumps({"lifecycle_status": "ARCHIVED", "parking_end_date": context["effective_date"],
                         "parking_end_financial_status": financial_status, "finance_task_id": finance_task_id}, ensure_ascii=False),
             close_note, task["telegram_user_id"]),
        )
        audit_status_change(cur, request["id"], "IN_PROGRESS", "RESOLVED", close_note)
        conn.commit()
        return finance_task_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def resolve_parking_end(request: dict, payload: dict, note: str) -> None:
    """Archive a sold/no-longer-parking vehicle after tariff mode is known.

    The final parking day is inclusive.  Existing charges are not silently
    rewritten: if they exist, this function refuses to close the task until a
    dedicated, reviewed prorating workflow is present.
    """
    context = parking_end_context(request, payload)
    if context["mode"] not in {"Day", "Night"}:
        raise RuntimeError("Режим парковки не определён; финансовое решение невозможно.")
    if (
        context["charges"]
        or context["payments"]
        or context["apartment_charges"]
        or context["apartment_payments"]
    ):
        raise RuntimeError(
            "Есть начисления или оплаты по автомобилю либо квартире. "
            "Нельзя архивировать и закрыть заявку до подневной корректировки."
        )
    conn = get_conn()
    try:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        task = cur.execute("SELECT * FROM operator_task_queue WHERE id=?", (request["id"],)).fetchone()
        vehicle = cur.execute("SELECT * FROM vehicles WHERE id=?", (request["vehicle_id"],)).fetchone()
        if not task or not vehicle or task["status"] != "IN_PROGRESS":
            raise RuntimeError("Нужна актуальная заявка в статусе IN_PROGRESS.")
        columns = {row[1] for row in cur.execute("PRAGMA table_info(vehicles)")}
        required = {"parking_end_date", "parking_end_reason", "parking_end_recorded_at", "parking_end_financial_status"}
        if not required.issubset(columns):
            raise RuntimeError("Не применена миграция 2026-09-21_015 для хранения даты прекращения парковки.")
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        old = dict(vehicle)
        financial_status = "NO_CHARGES_NO_PAYMENTS"
        cur.execute(
            """
            UPDATE vehicles SET lifecycle_status='ARCHIVED', status='ARCHIVED', archived_at=?, archive_reason=?,
                archived_by=?, parking_end_date=?, parking_end_reason=?, parking_end_recorded_at=?,
                parking_end_financial_status=?, updated_at=?, updated_by=? WHERE id=?
            """,
            (timestamp, context["reason"], ACTOR, context["effective_date"], context["reason"], timestamp,
             financial_status, timestamp, ACTOR, request["vehicle_id"]),
        )
        close_note = note.strip() or (
            f"Автомобиль архивирован как {context['reason']}; последний оплачиваемый день — "
            f"{context['effective_date']} включительно. Начислений и оплат по автомобилю нет."
        )
        cur.execute(
            "UPDATE operator_task_queue SET status='RESOLVED', updated_at=?, closed_at=?, close_note=? WHERE id=?",
            (timestamp, timestamp, close_note, request["id"]),
        )
        close_duplicate_parking_end_tasks(
            cur, request, int(request["vehicle_id"]), context["effective_date"], timestamp
        )
        cur.execute(
            """
            INSERT INTO audit_log(event_time, username, table_name, record_id, action, field_name,
                old_value, new_value, comment, actor_role, actor_name, source, telegram_user_id)
            VALUES (?, 'operator', 'vehicles', ?, 'resident_vehicle_parking_end_confirmed', '*', ?, ?, ?,
                    'operator', 'Streamlit admin', 'admin_console/resident_requests', ?)
            """,
            (timestamp, str(request["vehicle_id"]), json.dumps(old, ensure_ascii=False),
             json.dumps({"lifecycle_status": "ARCHIVED", "parking_end_date": context["effective_date"],
                         "parking_end_reason": context["reason"], "parking_end_financial_status": financial_status}, ensure_ascii=False),
             close_note, task["telegram_user_id"]),
        )
        audit_status_change(cur, request["id"], "IN_PROGRESS", "RESOLVED", close_note)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def parking_end_proration_preview(context: dict) -> dict:
    """Build a reviewable monthly-parking proration; this does not write data.

    A payment at apartment level may be selected only when it names the same
    parking service and billing month.  It remains an operator confirmation,
    not an inferred historical vehicle link.
    """
    mode = context["mode"]
    if mode not in {"Day", "Night"}:
        raise RuntimeError("Сначала подтвердите режим Day или Night.")
    end_date = datetime.strptime(context["effective_date"], "%Y-%m-%d").date()
    period_code = end_date.strftime("%Y-%m")
    days_in_month = monthrange(end_date.year, end_date.month)[1]
    parking_days = end_date.day  # Последний день парковки включается в расчёт.
    service_code = f"PARKING_{mode.upper()}"

    conn = get_conn()
    try:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        tariff_row = cur.execute(
            """
            SELECT amount FROM service_tariffs
            WHERE service_code=? AND COALESCE(is_active, 1)=1
              AND valid_from<=? AND (valid_to IS NULL OR valid_to>=?)
            ORDER BY valid_from DESC LIMIT 1
            """,
            (service_code, context["effective_date"], context["effective_date"]),
        ).fetchone()
        if not tariff_row:
            raise RuntimeError(f"Не найден тариф {service_code} на {period_code}.")
        monthly_amount = round(float(tariff_row["amount"]), 2)
        prorated_amount = round(monthly_amount * parking_days / days_in_month, 2)

        candidates = []
        apartment = str(context["vehicle"].get("apartment_id") or "")
        # Context already narrowed payments to the apartment; retain only the
        # exact month and exact parking service as eligible candidates.
        for payment in context["apartment_payments"]:
            values = " ".join(str(payment.get(key) or "") for key in (
                "service_type", "base_service_code", "service_item_code"
            )).upper()
            payment_mode = "Night" if "NIGHT" in values else "Day" if "DAY" in values else ""
            if str(payment.get("period_code") or "") != period_code or payment_mode != mode:
                continue
            amount = round(float(payment.get("amount") or 0), 2)
            if amount >= prorated_amount:
                candidates.append({**payment, "amount": amount})
        return {
            "period_code": period_code,
            "service_code": service_code,
            "parking_days": parking_days,
            "days_in_month": days_in_month,
            "monthly_amount": monthly_amount,
            "prorated_amount": prorated_amount,
            "payment_candidates": candidates,
        }
    finally:
        conn.close()


def resolve_parking_end_with_proration(request: dict, payload: dict, payment_id: int, note: str) -> None:
    """Create the reviewed partial-month charge, allocate one payment, archive.

    The operation is one transaction: a charge, an allocation, archive state,
    request closure, and audits either all appear together or none appear.
    """
    context = parking_end_context(request, payload)
    preview = parking_end_proration_preview(context)
    selected = next((row for row in preview["payment_candidates"] if int(row["id"]) == int(payment_id)), None)
    if not selected:
        raise RuntimeError("Выберите оплату этой квартиры за тот же период и режим парковки.")
    vehicle_id = int(request["vehicle_id"])
    conn = get_conn()
    try:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        task = cur.execute("SELECT * FROM operator_task_queue WHERE id=?", (request["id"],)).fetchone()
        vehicle = cur.execute("SELECT * FROM vehicles WHERE id=?", (vehicle_id,)).fetchone()
        payment = cur.execute("SELECT * FROM payments WHERE id=?", (int(payment_id),)).fetchone()
        if not task or not vehicle or task["status"] != "IN_PROGRESS":
            raise RuntimeError("Нужна актуальная заявка в статусе IN_PROGRESS.")
        if not payment or str(payment["apartment_number"] or "") != str(request.get("apartment_number") or ""):
            raise RuntimeError("Оплата не принадлежит квартире из заявки.")
        if cur.execute(
            "SELECT 1 FROM charges WHERE vehicle_id=? AND period_code=? AND service_code=?",
            (vehicle_id, preview["period_code"], preview["service_code"]),
        ).fetchone():
            raise RuntimeError("Для этого автомобиля уже есть начисление парковки за указанный период.")
        allocated = cur.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM payment_allocations WHERE payment_id=?", (int(payment_id),)
        ).fetchone()[0]
        available = round(float(payment["amount"] or 0) - float(allocated or 0), 2)
        if available + 0.00001 < preview["prorated_amount"]:
            raise RuntimeError("В выбранной оплате недостаточно нераспределённого остатка.")

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        explanation = (
            f"Продажа: последний день {context['effective_date']} включительно; "
            f"{preview['parking_days']} из {preview['days_in_month']} дней; "
            f"{preview['monthly_amount']:.2f} грн/месяц → {preview['prorated_amount']:.2f} грн."
        )
        cur.execute(
            """
            INSERT INTO charges(
                period_code, apartment_number, vehicle_id, service_code, quantity, unit_price, amount,
                currency, status, source, created_by, comment, created_at, updated_at,
                service_item_code, base_service_code, service_type, gross_amount, net_amount
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'UAH', 'paid', ?, ?, ?, ?, ?, ?, ?, 'MONTHLY', ?, ?)
            """,
            (
                preview["period_code"], request["apartment_number"], vehicle_id, preview["service_code"],
                preview["parking_days"], round(preview["monthly_amount"] / preview["days_in_month"], 6),
                preview["prorated_amount"], "resident_vehicle_parking_end_proration", ACTOR, explanation,
                timestamp, timestamp, payment["service_item_code"], preview["service_code"],
                preview["prorated_amount"], preview["prorated_amount"],
            ),
        )
        charge_id = int(cur.lastrowid)
        cur.execute(
            """
            INSERT INTO payment_allocations(payment_id, charge_id, amount, created_at,
                service_item_code, base_service_code, service_type)
            VALUES (?, ?, ?, ?, ?, ?, 'MONTHLY')
            """,
            (int(payment_id), charge_id, preview["prorated_amount"], timestamp,
             payment["service_item_code"], preview["service_code"]),
        )
        financial_status = "PRORATED_AND_ALLOCATED"
        old = dict(vehicle)
        cur.execute(
            """
            UPDATE vehicles SET lifecycle_status='ARCHIVED', status='ARCHIVED', archived_at=?, archive_reason=?,
                archived_by=?, parking_end_date=?, parking_end_reason=?, parking_end_recorded_at=?,
                parking_end_financial_status=?, updated_at=?, updated_by=? WHERE id=?
            """,
            (timestamp, context["reason"], ACTOR, context["effective_date"], context["reason"], timestamp,
             financial_status, timestamp, ACTOR, vehicle_id),
        )
        payment_remainder = round(available - preview["prorated_amount"], 2)
        close_note = note.strip() or (
            f"{explanation} Из оплаты #{payment_id} распределено {preview['prorated_amount']:.2f} грн; "
            f"нераспределённый остаток оплаты квартиры: {payment_remainder:.2f} грн."
        )
        cur.execute(
            "UPDATE operator_task_queue SET status='RESOLVED', updated_at=?, closed_at=?, close_note=? WHERE id=?",
            (timestamp, timestamp, close_note, request["id"]),
        )
        close_duplicate_parking_end_tasks(
            cur, request, vehicle_id, context["effective_date"], timestamp
        )
        cur.execute(
            """
            INSERT INTO audit_log(event_time, username, table_name, record_id, action, field_name,
                old_value, new_value, comment, actor_role, actor_name, source, telegram_user_id)
            VALUES (?, 'operator', 'charges', ?, 'resident_parking_end_proration', '*', NULL, ?, ?,
                    'operator', 'Streamlit admin', 'admin_console/resident_requests', ?)
            """,
            (timestamp, str(charge_id), json.dumps({"amount": preview["prorated_amount"], "payment_id": payment_id}, ensure_ascii=False),
             close_note, task["telegram_user_id"]),
        )
        cur.execute(
            """
            INSERT INTO audit_log(event_time, username, table_name, record_id, action, field_name,
                old_value, new_value, comment, actor_role, actor_name, source, telegram_user_id)
            VALUES (?, 'operator', 'vehicles', ?, 'resident_vehicle_parking_end_confirmed', '*', ?, ?, ?,
                    'operator', 'Streamlit admin', 'admin_console/resident_requests', ?)
            """,
            (timestamp, str(vehicle_id), json.dumps(old, ensure_ascii=False),
             json.dumps({"lifecycle_status": "ARCHIVED", "parking_end_date": context["effective_date"],
                         "parking_end_financial_status": financial_status}, ensure_ascii=False),
             close_note, task["telegram_user_id"]),
        )
        audit_status_change(cur, request["id"], "IN_PROGRESS", "RESOLVED", close_note)
        conn.commit()
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

status_filter = st.radio("Показать", ["Ожидающие", "Обработанные"], horizontal=True)
rows = load_requests("Все")
if status_filter == "Ожидающие":
    rows = rows[rows["Статус"].isin(["PENDING", "IN_PROGRESS", "NEEDS_CLARIFICATION"])]
else:
    rows = rows[rows["Статус"].isin(["RESOLVED", "REJECTED", "CLOSED"])]

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

if request.get("status") == "IN_PROGRESS":
    next_step = {
        "RESIDENT_VEHICLE_ADD": "подтвердить создание либо вернуть заявку в ожидание",
        "RESIDENT_VEHICLE_REMOVE": "проверить связи автомобиля и принять решение об удалении или архивировании",
        "RESIDENT_VEHICLE_PARKING_END": "проверить дату прекращения парковки",
    }.get(request.get("task_type"), "выбрать и применить структурированное решение")
    st.info(f"🔵 Шаг 2 из 3. Заявка взята в работу. Следующий шаг: **{next_step}**.")

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

thread = request_messages(int(request["id"]))
if thread:
    st.markdown("#### 💬 Переписка по заявке")
    for item in thread:
        if item["direction"] == "OPERATOR_TO_RESIDENT" and item.get("message_kind") == "RESOLUTION":
            author = "Система: результат"
        else:
            author = "Оператор" if item["direction"] == "OPERATOR_TO_RESIDENT" else "Житель"
        delivery = ""
        if item["direction"] == "OPERATOR_TO_RESIDENT":
            delivery = f" · доставка: {item['delivery_status']}"
            if item.get("delivery_error"):
                delivery += f" · ошибка: {item['delivery_error']}"
        st.markdown(f"**{author} · {item['created_at']}{delivery}**")
        st.write(item["message_text"])

# A legacy free-text "other" request cannot safely change registry data.  The
# operator may consciously classify it as an erroneous vehicle entry; the
# original text stays in description and the reclassification is audited.
if (
    request.get("task_type") == "RESIDENT_VEHICLE_UPDATE"
    and (payload.get("proposed") or {}).get("other")
    and request.get("vehicle_id")
    and request.get("status") in {"PENDING", "IN_PROGRESS"}
):
    st.warning("Это свободный текст, а не структурированное действие. Реестр по нему не изменяется автоматически.")
    if st.button("🗑️ Переопределить как «автомобиль внесён ошибочно»"):
        try:
            reclassify_as_mistaken_vehicle_remove(request)
            st.success("Заявка переопределена. Теперь доступна безопасная проверка удаления.")
            st.rerun()
        except RuntimeError as exc:
            st.error(str(exc))

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
    st.markdown("#### Продажа / прекращение парковки")
    try:
        end_context = parking_end_context(request, payload)
        st.write(
            f"Последний день парковки: **{end_context['effective_date']} включительно**. "
            f"Причина: **{end_context['reason']}**."
        )
        if request.get("status") in {"RESOLVED", "CLOSED"}:
            st.success(f"✅ Заявка уже решена: {request.get('close_note') or 'результат записан в аудит.'}")
        elif end_context["mode"] not in {"Day", "Night"}:
            suggestion = end_context["mode_suggestion"]
            if suggestion:
                st.warning(
                    f"🟡 Рекомендация режима: **{suggestion['mode']}**. Она получена из "
                    f"{suggestion['payment_count']} оплат квартиры за периоды "
                    f"{', '.join(suggestion['periods']) or '—'}, помеченных этим парковочным режимом."
                )
            else:
                st.warning(
                    "🟡 Финансовый блокер: режим парковки не определён. Дата продажи известна, "
                    "но без Day/Night нельзя обоснованно построить начисления или подневную корректировку."
                )
            st.caption(
                f"По квартире: начислений — {len(end_context['apartment_charges'])}, "
                f"оплат — {len(end_context['apartment_payments'])}. "
                "Оплата квартиры не доказывает, к какому автомобилю она относится; распределять её догадкой нельзя."
            )
            if end_context["apartment_payments"]:
                st.dataframe(
                    pd.DataFrame(end_context["apartment_payments"]),
                    hide_index=True, use_container_width=True,
                )
            if request.get("status") == "IN_PROGRESS":
                if suggestion:
                    suggestion_note = st.text_input(
                        "Комментарий подтверждения", value="Режим подтверждён по истории оплат квартиры.",
                        key=f"parking_end_suggestion_note_{request['id']}",
                    )
                    if st.button(f"✅ Подтвердить {suggestion['mode']} и перейти к расчёту", type="primary"):
                        try:
                            accept_parking_mode_suggestion(request, suggestion, suggestion_note)
                            st.success("Режим подтверждён по финансовому доказательству. Продолжаем решение продажи.")
                            st.rerun()
                        except RuntimeError as exc:
                            st.error(str(exc))
                else:
                    clarification_note = st.text_input(
                        "Сообщение жителю", value=(
                            "Уточните режим парковки Day или Night. Без него нельзя проверить начисления и оплаты "
                            "перед прекращением парковки."
                        ), key=f"parking_end_clarification_{request['id']}",
                    )
                    if st.button("↩️ Запросить у жителя режим парковки", type="primary"):
                        try:
                            request_parking_mode_clarification(request, clarification_note)
                            st.success("Заявка возвращена жителю для структурированного уточнения режима. Реестр и финансы не менялись.")
                            st.rerun()
                        except RuntimeError as exc:
                            st.error(str(exc))
                st.caption(
                    "Либо примите факт продажи сейчас: режим и платежи останутся внутренней задачей сверки, "
                    "а жителю не потребуется ждать её завершения."
                )
                archive_pending_note = st.text_input(
                    "Комментарий принятия продажи", value="Продажа подтверждена; финансовая сверка передана оператору.",
                    key=f"parking_end_pending_finance_note_{request['id']}",
                )
                archive_pending_confirm = st.checkbox(
                    f"Подтверждаю архивирование с датой {end_context['effective_date']} и передачу финансов на сверку.",
                    key=f"parking_end_pending_finance_confirm_{request['id']}",
                )
                if st.button(
                    "✅ Архивировать авто и передать финансы на сверку",
                    type="primary", disabled=not archive_pending_confirm,
                    key=f"parking_end_pending_finance_apply_{request['id']}",
                ):
                    try:
                        finance_task_id = archive_parking_end_pending_reconciliation(
                            request, payload, archive_pending_note
                        )
                        st.success(
                            f"Продажа применена: автомобиль архивирован. Финансовая сверка создана как задача #{finance_task_id}."
                        )
                        st.rerun()
                    except RuntimeError as exc:
                        st.error(str(exc))
            else:
                st.caption("Заявка ожидает уточнения режима от жителя. После появления подтверждённого режима её можно возобновить.")
        else:
            charges_count, payments_count = len(end_context["charges"]), len(end_context["payments"])
            apartment_charges_count = len(end_context["apartment_charges"])
            apartment_payments_count = len(end_context["apartment_payments"])
            if charges_count or payments_count or apartment_charges_count or apartment_payments_count:
                st.warning(
                    f"Найдены финансовые записи: прямых по авто — начислений {charges_count}, оплат {payments_count}; "
                    f"по квартире — начислений {apartment_charges_count}, оплат {apartment_payments_count}. "
                    "До закрытия нужна подневная корректировка; квартирные оплаты не исключаются только из-за пустого vehicle_id."
                )
                if end_context["charges"]:
                    st.dataframe(pd.DataFrame(end_context["charges"]), hide_index=True, use_container_width=True)
                if end_context["apartment_payments"]:
                    st.dataframe(pd.DataFrame(end_context["apartment_payments"]), hide_index=True, use_container_width=True)
                if request.get("status") == "IN_PROGRESS":
                    st.markdown("##### Предлагаемый подневный расчёт")
                    try:
                        proration = parking_end_proration_preview(end_context)
                        st.info(
                            f"Период: **{proration['period_code']}** · режим: **{end_context['mode']}**. "
                            f"Последний день включается: {proration['parking_days']} из {proration['days_in_month']} дней. "
                            f"Тариф {proration['monthly_amount']:.2f} грн → начисление **{proration['prorated_amount']:.2f} грн**."
                        )
                        candidates = proration["payment_candidates"]
                        if candidates:
                            payment_labels = {
                                int(row["id"]): (
                                    f"Оплата #{row['id']} · {row.get('payment_date') or 'дата не указана'} · "
                                    f"{row['amount']:.2f} грн · {row.get('service_item_code') or proration['service_code']}"
                                )
                                for row in candidates
                            }
                            payment_id = st.selectbox(
                                "Какую оплату разнести на это начисление?",
                                list(payment_labels), format_func=payment_labels.get,
                                key=f"parking_end_payment_{request['id']}",
                            )
                            selected_payment = next(row for row in candidates if int(row["id"]) == int(payment_id))
                            remainder = round(float(selected_payment["amount"]) - proration["prorated_amount"], 2)
                            st.caption(
                                f"Будет создано начисление {proration['prorated_amount']:.2f} грн для авто "
                                f"и разнесено из оплаты #{payment_id}. Остаток {remainder:.2f} грн останется "
                                "нераспределённой оплатой квартиры — его не приписываем другому авто автоматически."
                            )
                            proration_note = st.text_input(
                                "Комментарий решения", value="Продажа и подневный расчёт подтверждены оператором.",
                                key=f"parking_end_proration_note_{request['id']}",
                            )
                            proration_confirm = st.checkbox(
                                "Подтверждаю расчёт, частичное разнесение оплаты и архивирование автомобиля.",
                                key=f"parking_end_proration_confirm_{request['id']}",
                            )
                            if st.button(
                                "✅ Применить расчёт, разнести оплату и закрыть заявку",
                                type="primary", disabled=not proration_confirm,
                            ):
                                try:
                                    resolve_parking_end_with_proration(
                                        request, payload, int(payment_id), proration_note
                                    )
                                    st.success(
                                        "Подневный расчёт применён: начисление и разнесение оплаты сохранены, "
                                        "автомобиль архивирован, заявка закрыта."
                                    )
                                    st.rerun()
                                except RuntimeError as exc:
                                    st.error(str(exc))
                        else:
                            st.warning(
                                "Нет оплаты того же периода и режима, из которой можно разнести рассчитанную сумму. "
                                "Проверьте период оплаты или передайте финансовую часть на отдельную сверку."
                            )
                            archive_pending_note = st.text_input(
                                "Комментарий принятия продажи", value="Продажа подтверждена; оплату и расчёт передать на сверку.",
                                key=f"parking_end_no_payment_note_{request['id']}",
                            )
                            archive_pending_confirm = st.checkbox(
                                f"Подтверждаю архивирование с датой {end_context['effective_date']} без автоматического разнесения оплаты.",
                                key=f"parking_end_no_payment_confirm_{request['id']}",
                            )
                            if st.button(
                                "✅ Архивировать авто и передать финансы на сверку",
                                type="primary", disabled=not archive_pending_confirm,
                                key=f"parking_end_no_payment_apply_{request['id']}",
                            ):
                                try:
                                    finance_task_id = archive_parking_end_pending_reconciliation(
                                        request, payload, archive_pending_note
                                    )
                                    st.success(
                                        f"Продажа применена: автомобиль архивирован. Финансовая сверка создана как задача #{finance_task_id}."
                                    )
                                    st.rerun()
                                except RuntimeError as exc:
                                    st.error(str(exc))
                    except RuntimeError as exc:
                        st.error(str(exc))
            else:
                st.success(
                    "🟢 Режим определён, начислений и оплат по этому автомобилю нет. "
                    "Подневная корректировка не требуется; можно завершить продажу."
                )
                if request.get("status") == "IN_PROGRESS":
                    end_note = st.text_input(
                        "Комментарий решения", value="Продажа подтверждена оператором.",
                        key=f"parking_end_note_{request['id']}",
                    )
                    end_confirm = st.checkbox(
                        f"Подтверждаю архивирование автомобиля с последним оплачиваемым днём {end_context['effective_date']}.",
                        key=f"parking_end_confirm_{request['id']}",
                    )
                    if st.button("✅ Архивировать автомобиль и закрыть заявку", type="primary", disabled=not end_confirm):
                        try:
                            resolve_parking_end(request, payload, end_note)
                            st.success("Продажа применена: дата сохранена, автомобиль архивирован, заявка закрыта и аудит записан.")
                            st.rerun()
                        except RuntimeError as exc:
                            st.error(str(exc))
            if request.get("status") == "PENDING":
                st.caption("Шаг 1: нажмите «Взять в работу и перейти к решению». Реестр и финансы пока не меняются.")
    except RuntimeError as exc:
        st.error(str(exc))
elif request.get("task_type") in {"RESIDENT_VEHICLE_UPDATE", "RESIDENT_VEHICLE_CHANGE", "RESIDENT_VEHICLE_ADD"}:
    st.info("После проверки оператор применит данные в карточке автомобиля. Автоматического изменения реестра нет.")

if request.get("task_type") == "RESIDENT_VEHICLE_REMOVE":
    st.markdown("#### Ошибочно внесённый автомобиль")
    if request.get("status") in {"RESOLVED", "CLOSED"}:
        st.success(f"✅ Заявка уже решена: {request.get('close_note') or 'результат записан в аудит.'}")
    elif not request.get("vehicle_id"):
        st.error("В заявке отсутствует ссылка на автомобиль; удалить запись небезопасно.")
    else:
        try:
            removal_safety = vehicle_removal_safety(int(request["vehicle_id"]), exclude_task_id=int(request["id"]))
            vehicle = removal_safety["vehicle"]
            blockers = removal_safety["blockers"]
            st.write(
                f"Автомобиль #{vehicle['id']}: **{vehicle.get('license_plate_normalized') or vehicle.get('license_plate') or '—'}**. "
                "Перед решением проверены платежи, начисления, касса, корректировки и связанные рабочие записи."
            )
            if blockers:
                st.warning("Физическое удаление запрещено: связанная история должна остаться в БД.")
                st.dataframe(pd.DataFrame(blockers), hide_index=True, use_container_width=True)
            else:
                st.success("🟢 Финансовых и иных связанных записей нет. Автомобиль можно удалить физически без потери истории.")

            if request.get("status") == "IN_PROGRESS":
                remove_note = st.text_input(
                    "Комментарий решения", value="Автомобиль был внесён ошибочно.",
                    key=f"resident_remove_note_{request['id']}",
                )
                if blockers:
                    confirm_archive = st.checkbox(
                        "Подтверждаю архивирование ошибочной записи с сохранением истории.",
                        key=f"resident_remove_archive_confirm_{request['id']}",
                    )
                    if st.button("📦 Архивировать автомобиль и закрыть заявку", type="primary", disabled=not confirm_archive):
                        try:
                            resolve_mistaken_vehicle_remove(request, delete=False, note=remove_note)
                            st.success("Автомобиль архивирован, история сохранена; заявка закрыта и аудит записан.")
                            st.rerun()
                        except RuntimeError as exc:
                            st.error(str(exc))
                else:
                    confirm_delete = st.checkbox(
                        "Подтверждаю физическое удаление ошибочно внесённого автомобиля.",
                        key=f"resident_remove_delete_confirm_{request['id']}",
                    )
                    if st.button("🗑️ Удалить автомобиль и закрыть заявку", type="primary", disabled=not confirm_delete):
                        try:
                            resolve_mistaken_vehicle_remove(request, delete=True, note=remove_note)
                            st.success("Автомобиль удалён, заявка закрыта; полное решение записано в аудит.")
                            st.rerun()
                        except RuntimeError as exc:
                            st.error(str(exc))
            elif request.get("status") == "PENDING":
                st.caption("Шаг 1: нажмите «Взять в работу и перейти к решению». Данные автомобиля пока не меняются.")
        except RuntimeError as exc:
            st.error(str(exc))

if request.get("task_type") == "RESIDENT_VEHICLE_ADD" and request.get("status") in {"RESOLVED", "CLOSED"}:
    st.success(
        f"✅ Заявка уже закрыта. Автомобиль #{request.get('vehicle_id') or '—'} "
        f"с номером {request.get('plate') or '—'} создан ранее."
    )
    if request.get("close_note"):
        st.caption(f"Комментарий решения: {request['close_note']}")

add_recommendation: dict | None = None
if request.get("task_type") == "RESIDENT_VEHICLE_ADD" and request.get("status") not in {"RESOLVED", "CLOSED", "REJECTED"}:
    st.markdown("#### Добавление автомобиля оператором")
    add_recommendation = vehicle_add_recommendation(request, payload)
    suggested_add_plate = add_recommendation["plate"]
    evidence_text = "\n\n".join(f"• {reason}" for reason in add_recommendation["reasons"])
    if not suggested_add_plate:
        st.error("Из текста не выделен госномер стандартного формата AA1234BB.")
        if request.get("status") in {"PENDING", "IN_PROGRESS", "NEEDS_CLARIFICATION"}:
            if st.button("⛔ Отказать: номер автомобиля не распознан", key=f"resident_add_reject_no_plate_{request['id']}"):
                try:
                    reject_resident_request(
                        request,
                        "Заявка отклонена: из текста не удалось выделить госномер в формате AA1234BB. "
                        "Подайте новое обращение с номером автомобиля.",
                    )
                    st.success("Заявка отклонена без изменения реестра; жителю будет отправлена причина.")
                    st.rerun()
                except RuntimeError as exc:
                    st.error(str(exc))
    elif add_recommendation["level"] == "GREEN":
        st.success(
            "🟢 Система подготовила рекомендацию для подтверждения. "
            "Номер стандартный, в главном реестре его нет, а данные подтверждены независимым источником.\n\n"
            + evidence_text
        )
    elif add_recommendation["level"] == "RED":
        st.error("🔴 Создание нового автомобиля заблокировано.\n\n" + evidence_text)
    else:
        st.warning(
            "🟡 Номер очищен нормализатором, но система не получила достаточного независимого подтверждения. "
            "Оператору предложены подготовленные значения; их нужно сверить с источником.\n\n" + evidence_text
        )
    if suggested_add_plate and request.get("status") == "IN_PROGRESS":
        is_green = add_recommendation["level"] == "GREEN"
        if add_recommendation["level"] == "RED":
            st.caption("Откройте существующий автомобиль или свяжите заявку с ним; новый дубль не создавайте.")
        else:
            field_hint = (
                "Подготовлено системой; при необходимости исправьте только после проверки."
                if is_green else "Очищенное значение из заявления; при необходимости уточните у жителя или по источнику."
            )
            st.caption(field_hint)
        # Initialise once per recommendation.  A request that was opened
        # before this feature existed may already have empty widget state;
        # replacing it once prevents the normalised recommendation being hidden
        # behind that stale blank field, while later operator edits are kept.
        plate_key = f"resident_add_plate_{request['id']}"
        model_key = f"resident_add_model_{request['id']}"
        color_key = f"resident_add_color_{request['id']}"
        recommendation_key = f"resident_add_recommendation_{request['id']}"
        signature = json.dumps({
            "plate": suggested_add_plate, "model": add_recommendation["model"],
            "color": add_recommendation["color"], "level": add_recommendation["level"],
        }, ensure_ascii=False, sort_keys=True)
        if st.session_state.get(recommendation_key) != signature:
            st.session_state[plate_key] = suggested_add_plate or ""
            st.session_state[model_key] = add_recommendation["model"] or ""
            st.session_state[color_key] = add_recommendation["color"] or ""
            st.session_state[recommendation_key] = signature
        confirmed_add_plate = st.text_input(
            "Госномер для создания", placeholder="Например: AA6325PH", key=plate_key,
        )
        add_col1, add_col2 = st.columns(2)
        with add_col1:
            add_model = st.text_input(
                "Марка / модель (если известна)", key=model_key,
            )
        with add_col2:
            add_color = st.text_input(
                "Цвет (если известен)", key=color_key,
            )
        parking_choice = st.selectbox(
            "Режим парковки", ["Не указан", "Day", "Night", "Inactive"],
            key=f"resident_add_parking_{request['id']}",
        )
        add_note = st.text_input(
            "Комментарий оператора", value="Автомобиль проверен и добавлен оператором.",
            key=f"resident_add_note_{request['id']}",
        )
        confirm_add = True if is_green else st.checkbox(
            f"Подтверждаю добавление автомобиля в кв. {request.get('apartment_number') or '—'}",
            key=f"resident_add_confirm_{request['id']}",
        )
        button_title = (
            "✅ Подтвердить рекомендацию и добавить автомобиль"
            if is_green else "✅ Создать автомобиль и закрыть заявку"
        )
        if st.button(button_title, type="primary", disabled=(not confirm_add or add_recommendation["level"] == "RED")):
            try:
                vehicle_id = create_vehicle_from_resident_request(
                    request, confirmed_add_plate, add_model, add_color,
                    None if parking_choice == "Не указан" else parking_choice, add_note,
                    recommendation={
                        "level": add_recommendation["level"], "reasons": add_recommendation["reasons"],
                        "video": add_recommendation["video"],
                        "historic_rows": add_recommendation["historic_rows"],
                    },
                )
                st.success(f"Создан автомобиль #{vehicle_id}; заявка закрыта, операция записана в аудит.")
                st.rerun()
            except RuntimeError as exc:
                st.error(str(exc))
    elif suggested_add_plate and request.get("status") == "PENDING":
        st.caption("Сначала нажмите «Взять в работу», затем станет доступен редактор добавления автомобиля.")

is_unparseable_add_request = bool(
    request.get("task_type") == "RESIDENT_VEHICLE_ADD"
    and add_recommendation is not None
    and not add_recommendation.get("plate")
)
proposed_fields = payload.get("proposed") or {}
supported_vehicle_fields = [field for field in ("license_plate", "car_model", "car_color", "parking_time") if field in proposed_fields]
plate_clarification = vehicle_plate_clarification_hint(request, payload)
if plate_clarification and request.get("status") in {"PENDING", "IN_PROGRESS", "NEEDS_CLARIFICATION"}:
    st.warning("🟡 Автоматическая проверка нашла противоречие между заявкой, реестром и видео-наблюдениями.")
    st.write(plate_clarification)
    if st.button("✉️ Отправить подготовленный вопрос жителю", type="primary", key=f"plate_clarification_{request['id']}"):
        try:
            send_clarification_to_resident(request, plate_clarification)
            st.success("Вопрос поставлен в очередь Telegram. Заявка ожидает ответа жителя.")
            st.rerun()
        except RuntimeError as exc:
            st.error(str(exc))
if request.get("task_type") == "RESIDENT_VEHICLE_UPDATE" and request.get("vehicle_id") and supported_vehicle_fields:
    field = supported_vehicle_fields[0]
    labels = {
        "license_plate": "госномер", "car_model": "марка / модель",
        "car_color": "цвет", "parking_time": "режим парковки",
    }
    current_values = payload.get("current") or {}
    current_value = current_values.get(field)
    if field == "license_plate":
        current_value = current_value or current_values.get("plate")
    proposed_value = proposed_fields.get(field)
    st.markdown(f"#### Исправление поля: {labels[field]}")
    st.write(
        f"Автомобиль #{request['vehicle_id']}: в реестре — **{current_value or '—'}**; "
        f"житель указал — **{proposed_value or '—'}**."
    )
    st.caption("Значение жителя — предложение, а не автоматическое изменение реестра.")
    if request.get("status") == "IN_PROGRESS":
        if field == "parking_time":
            confirmed_value = st.selectbox(
                "Подтверждённый режим парковки", ["Day", "Night", "Inactive"],
                index=["Day", "Night", "Inactive"].index(proposed_value) if proposed_value in {"Day", "Night", "Inactive"} else 0,
                key=f"resident_request_confirmed_{field}_{request['id']}",
            )
        else:
            confirmed_value = st.text_input(
                f"Подтверждённое значение: {labels[field]}", value=str(proposed_value or ""),
                key=f"resident_request_confirmed_{field}_{request['id']}",
            )
        operator_note = st.text_input(
            "Комментарий оператору / жителю", value=f"{labels[field].capitalize()} проверен(а) оператором.",
            key=f"resident_request_resolution_{request['id']}",
        )
        if st.button("✅ Сохранить подтверждённое значение", type="primary"):
            try:
                save_confirmed_vehicle_field(request, field, confirmed_value, operator_note)
                st.success("Значение сохранено, заявка закрыта; операция записана в аудит.")
                st.rerun()
            except RuntimeError as exc:
                st.error(str(exc))
    elif request.get("status") == "PENDING":
        st.caption("Шаг 1: нажмите «Взять в работу и перейти к решению». Реестр пока не меняется.")

    if request.get("status") in {"PENDING", "IN_PROGRESS", "NEEDS_CLARIFICATION"}:
        st.markdown("**Если предложение не принимается**")
        rejection_note = st.text_area(
            "Комментарий жителю (обязателен)",
            placeholder="Например: цвет не подтверждён по доступным данным. Уточните его, пожалуйста.",
            key=f"resident_request_reject_{request['id']}",
        )
        if st.button(
            "⛔ Отклонить заявку без изменения реестра",
            disabled=not rejection_note.strip(),
            key=f"resident_request_reject_apply_{request['id']}",
        ):
            try:
                reject_resident_request(request, rejection_note)
                st.success("Заявка отклонена; реестр не менялся, комментарий будет отправлен жителю.")
                st.rerun()
            except RuntimeError as exc:
                st.error(str(exc))

is_structured_vehicle_field_request = bool(
    request.get("task_type") == "RESIDENT_VEHICLE_UPDATE"
    and request.get("vehicle_id")
    and supported_vehicle_fields
)
if request.get("status") in {"PENDING", "IN_PROGRESS", "NEEDS_CLARIFICATION"} and not is_structured_vehicle_field_request and not is_unparseable_add_request:
    with st.expander("🧭 Разобрать неточно сформулированное обращение"):
        st.caption(
            "Исходный текст не удаляется. Здесь можно связать его с правильной заявкой "
            "той же квартиры либо вернуть жителю на уточнение. Реестр и финансы на этом шаге не меняются."
        )
        candidates = triage_candidates(request)
        if candidates:
            candidate_labels = {
                int(item["id"]): (
                    f"#{item['id']} · {item['task_type']} · {item['status']} · "
                    f"{item.get('plate') or 'без номера'} · {item.get('title') or 'без заголовка'}"
                )
                for item in candidates
            }
            replacement_id = st.selectbox(
                "Какая заявка отражает фактическое обращение?",
                list(candidate_labels), format_func=candidate_labels.get,
                key=f"triage_replacement_{request['id']}",
            )
            supersede_note = st.text_input(
                "Дополнительный комментарий (необязательно)",
                value="",
                key=f"triage_note_{request['id']}",
            )
            supersede_confirm = st.checkbox(
                "Подтверждаю закрытие этой заявки как заменённой.",
                key=f"triage_confirm_{request['id']}",
            )
            if st.button(
                "✅ Закрыть как заменённую", disabled=not supersede_confirm,
                key=f"triage_apply_{request['id']}",
            ):
                try:
                    resolve_as_superseded(request, int(replacement_id), supersede_note)
                    st.success(f"Заявка закрыта как заменённая заявкой №{replacement_id}; исходный текст и аудит сохранены.")
                    st.rerun()
                except RuntimeError as exc:
                    st.error(str(exc))
        else:
            st.info("Для этой квартиры пока нет другой заявки, с которой можно связать обращение.")

        st.markdown("**Если это только подтверждение или информация**")
        informational_note = st.text_input(
            "Комментарий закрытия", value="Подтверждение получено; изменений в реестре не требуется.",
            key=f"triage_info_note_{request['id']}",
        )
        informational_confirm = st.checkbox(
            "Подтверждаю: это обращение не требует действий в реестре.",
            key=f"triage_info_confirm_{request['id']}",
        )
        if st.button(
            "✅ Закрыть как информационное", disabled=not informational_confirm,
            key=f"triage_info_apply_{request['id']}",
        ):
            try:
                resolve_as_informational(request, informational_note)
                st.success("Информационное обращение закрыто; исходный текст и аудит сохранены.")
                st.rerun()
            except RuntimeError as exc:
                st.error(str(exc))

        clarification_note = st.text_input(
            "Текст запроса уточнения", value="Уточните, пожалуйста, к каким данным относится это сообщение.",
            key=f"triage_clarification_note_{request['id']}",
        )
        if st.button("↩️ Вернуть жителю на уточнение", key=f"triage_clarification_apply_{request['id']}"):
            try:
                send_clarification_to_resident(request, clarification_note)
                st.success("Вопрос поставлен в очередь Telegram; заявка ожидает ответа жителя.")
                st.rerun()
            except RuntimeError as exc:
                st.error(str(exc))

if request.get("status") == "PENDING" and not is_unparseable_add_request:
    if st.button("▶️ Взять в работу и перейти к решению", type="primary"):
        set_status(int(request["id"]), "IN_PROGRESS", "Оператор взял заявку жителя в работу.")
        st.success("Заявка взята в работу. Данные реестра не менялись; открыт следующий шаг решения.")
        st.rerun()
elif request.get("status") == "IN_PROGRESS":
    if st.button("↩️ Вернуть в ожидание"):
        set_status(int(request["id"]), "PENDING", "Заявка возвращена в ожидание оператора.")
        st.success("Заявка снова ожидает рассмотрения.")
        st.rerun()
elif request.get("status") == "NEEDS_CLARIFICATION":
    if st.button("▶️ Возобновить работу после уточнения", type="primary"):
        set_status(int(request["id"]), "IN_PROGRESS", "Оператор возобновил заявку после уточнения жителя.")
        st.success("Заявка снова в работе. Проверьте режим и финансовые данные.")
        st.rerun()
