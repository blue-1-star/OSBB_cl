"""Two-stage quality corrections in the existing operator task queue.

Proposing never changes registry data. Only a current SUPER_ADMIN may apply a
supported correction, with stale-value checking and audit in one transaction.
"""

from __future__ import annotations

from datetime import datetime
import importlib.util
import json
from pathlib import Path
import sqlite3

from access_control import list_user_roles
from audit_logger import audit_field_change
from service_orders_core import get_conn


ORIGIN = "DATA_QUALITY"
TASK_TYPE = "DATA_QUALITY_CORRECTION"
RULE_TARGETS = {
    "VEHICLE_MODEL": ("vehicles", "car_model"),
    "VEHICLE_COLOR": ("vehicles", "car_color"),
    "VEHICLE_PARKING_MODE": ("vehicles", "parking_time"),
    "VEHICLE_PLATE": ("vehicles", "license_plate"),
    "VEHICLE_PLATE_SUSPICIOUS": ("vehicles", "license_plate"),
    "VEHICLE_APARTMENT": ("vehicles", "apartment_id"),
    "APARTMENT_AREA": ("apartments", "total_area"),
    "APARTMENT_PERSON": ("apartments", "person_name"),
    "APARTMENT_CONTACT": ("apartments", "contact_phone"),
}
DIRECT_FIELDS = {"car_model": "car_model_normalized", "car_color": "car_color_normalized"}

_spec = importlib.util.spec_from_file_location("quality_proposal_normalizers", Path(__file__).with_name("utils.py"))
if _spec is None or _spec.loader is None:
    raise RuntimeError("Нормализаторы реестра недоступны.")
_utils = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_utils)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _audit(conn: sqlite3.Connection, *, actor: str, table: str, row_id: int,
           field: str, old: str, new: str, action: str, note: str) -> None:
    audit_field_change(
        conn=conn, table_name=table, row_id=row_id, field_name=field,
        old_value=old, new_value=new, operator_id=actor, user_id=actor,
        action_type=action, source_context="data_quality_proposals", comment=note,
    )
    conn.execute(
        """INSERT INTO audit_log(event_time,username,table_name,record_id,action,
               field_name,old_value,new_value,comment,actor_role,actor_name,source)
           VALUES (?,?,?,?,?,?,?,?,?,'operator',?,'data_quality_proposals')""",
        (_now(), actor, table, str(row_id), action, field, old, new, note, actor),
    )


def _load_task(conn: sqlite3.Connection, task_id: int) -> tuple[sqlite3.Row, dict]:
    task = conn.execute(
        "SELECT * FROM operator_task_queue WHERE id=? AND origin=? AND task_type=?",
        (task_id, ORIGIN, TASK_TYPE),
    ).fetchone()
    if task is None:
        raise ValueError("Предложение не найдено.")
    return task, json.loads(task["payload_json"] or "{}")


def _is_super_admin(user_id: int | str, conn: sqlite3.Connection) -> bool:
    now = _now()
    return any(
        role["role_code"] == "SUPER_ADMIN" and int(role["is_active"] or 0) == 1
        and (not role["valid_from"] or role["valid_from"] <= now)
        and (not role["valid_to"] or role["valid_to"] >= now)
        for role in list_user_roles(user_id, conn=conn)
    )


def is_super_admin(user_id: int | str) -> bool:
    conn = get_conn()
    try:
        return _is_super_admin(user_id, conn)
    finally:
        conn.close()


def create_proposal(*, rule_code: str, object_id: int, apartment: str,
                    proposed_value: str, evidence: str, actor: str,
                    telegram_user_id: int | str | None = None,
                    conn: sqlite3.Connection | None = None) -> int:
    """Save an auditable proposal, not a registry edit."""
    if rule_code not in RULE_TARGETS:
        raise ValueError("Неизвестный вид пробела.")
    proposed_value, evidence, actor = proposed_value.strip(), evidence.strip(), actor.strip()
    if not proposed_value or not evidence or not actor:
        raise ValueError("Нужны предлагаемое значение, источник и исполнитель.")
    table, field = RULE_TARGETS[rule_code]
    owns = conn is None
    conn = conn or get_conn()
    try:
        conn.row_factory = sqlite3.Row
        if owns:
            conn.execute("BEGIN IMMEDIATE")
        if field in {"person_name", "contact_phone"}:
            exists = conn.execute("SELECT id FROM apartments WHERE id=?", (object_id,)).fetchone()
            old = ""
        else:
            exists = conn.execute(f"SELECT {field} FROM {table} WHERE id=?", (object_id,)).fetchone()
            old = str(exists[field] or "") if exists else ""
        if not exists:
            raise ValueError("Исходная запись больше не существует.")
        if old.strip() == proposed_value:
            raise ValueError("Предлагаемое значение уже записано в реестре.")
        for existing_task in conn.execute(
            """SELECT id,payload_json FROM operator_task_queue
               WHERE origin=? AND task_type=? AND status IN ('PENDING','NEEDS_CLARIFICATION','IN_PROGRESS')""",
            (ORIGIN, TASK_TYPE),
        ):
            existing = json.loads(existing_task["payload_json"] or "{}")
            if (existing.get("object_table"), existing.get("object_id"),
                existing.get("field_name"), existing.get("proposed_value")) == (
                table, int(object_id), field, proposed_value,
            ):
                raise ValueError(f"Такое предложение уже ожидает рассмотрения: #{existing_task['id']}.")
        payload = {
            "rule_code": rule_code, "object_table": table, "object_id": int(object_id),
            "field_name": field, "old_value": old, "proposed_value": proposed_value,
            "evidence": evidence, "dialogue": [],
        }
        timestamp = _now()
        cur = conn.execute(
            """INSERT INTO operator_task_queue(task_type,status,apartment_number,
                   telegram_user_id,title,description,origin,created_by,created_at,updated_at,payload_json)
               VALUES (?,'PENDING',?,?,?,?,?,?,?, ?,?)""",
            (TASK_TYPE, str(apartment), str(telegram_user_id) if telegram_user_id else None,
             f"Уточнить {field}", evidence, ORIGIN, actor, timestamp, timestamp,
             json.dumps(payload, ensure_ascii=False)),
        )
        task_id = int(cur.lastrowid)
        _audit(conn, actor=actor, table="operator_task_queue", row_id=task_id,
               field="status", old="", new="PENDING", action="quality_proposal_created", note=evidence)
        if owns:
            conn.commit()
        return task_id
    except Exception:
        if owns:
            conn.rollback()
        raise
    finally:
        if owns:
            conn.close()


def list_proposals(*, conn: sqlite3.Connection | None = None, status: str | None = None) -> list[dict]:
    owns = conn is None
    conn = conn or get_conn()
    try:
        conn.row_factory = sqlite3.Row
        sql = "SELECT * FROM operator_task_queue WHERE origin=? AND task_type=?"
        params: list[str] = [ORIGIN, TASK_TYPE]
        if status:
            sql += " AND status=?"
            params.append(status)
        sql += " ORDER BY id DESC"
        return [dict(row) for row in conn.execute(sql, params)]
    finally:
        if owns:
            conn.close()


def get_proposal(task_id: int) -> dict | None:
    conn = get_conn()
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM operator_task_queue WHERE id=? AND origin=? AND task_type=?",
            (task_id, ORIGIN, TASK_TYPE),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def request_clarification(*, task_id: int, reviewer_id: int | str,
                          question: str) -> None:
    question = question.strip()
    if not question:
        raise ValueError("Введите вопрос для уточнения.")
    conn = get_conn()
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN IMMEDIATE")
        if not _is_super_admin(reviewer_id, conn):
            raise PermissionError("Только SUPER_ADMIN может запрашивать уточнение.")
        task, payload = _load_task(conn, task_id)
        if task["status"] not in {"PENDING", "IN_PROGRESS"}:
            raise ValueError("Предложение уже ожидает ответа или закрыто.")
        payload.setdefault("dialogue", []).append({"at": _now(), "actor": str(reviewer_id), "kind": "QUESTION", "text": question})
        conn.execute(
            "UPDATE operator_task_queue SET status='NEEDS_CLARIFICATION',updated_at=?,payload_json=? WHERE id=?",
            (_now(), json.dumps(payload, ensure_ascii=False), task_id),
        )
        _audit(conn, actor=str(reviewer_id), table="operator_task_queue", row_id=task_id,
               field="status", old=task["status"], new="NEEDS_CLARIFICATION",
               action="quality_clarification_requested", note=question)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def reply_to_clarification(*, task_id: int, actor: str, reply: str,
                           telegram_user_id: int | str | None = None) -> None:
    reply, actor = reply.strip(), actor.strip()
    if not reply or not actor:
        raise ValueError("Введите ответ и исполнителя.")
    conn = get_conn()
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN IMMEDIATE")
        task, payload = _load_task(conn, task_id)
        if task["status"] != "NEEDS_CLARIFICATION":
            raise ValueError("Это предложение не ожидает уточнения.")
        if task["telegram_user_id"] and str(task["telegram_user_id"]) != str(telegram_user_id):
            raise PermissionError("Ответить может только автор предложения.")
        if not task["telegram_user_id"] and task["created_by"] != actor:
            raise PermissionError("Ответить может только автор предложения.")
        payload.setdefault("dialogue", []).append({"at": _now(), "actor": actor, "kind": "ANSWER", "text": reply})
        conn.execute(
            "UPDATE operator_task_queue SET status='PENDING',updated_at=?,payload_json=? WHERE id=?",
            (_now(), json.dumps(payload, ensure_ascii=False), task_id),
        )
        _audit(conn, actor=actor, table="operator_task_queue", row_id=task_id,
               field="status", old="NEEDS_CLARIFICATION", new="PENDING",
               action="quality_clarification_replied", note=reply)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def approve_proposal(*, task_id: int, reviewer_id: int | str, note: str) -> None:
    """Apply only model/color after super-admin authorization and stale check."""
    note = note.strip()
    if not note:
        raise ValueError("Укажите основание решения.")
    conn = get_conn()
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN IMMEDIATE")
        if not _is_super_admin(reviewer_id, conn):
            raise PermissionError("Только SUPER_ADMIN может принимать исправления.")
        task, payload = _load_task(conn, task_id)
        if task["status"] != "PENDING":
            raise ValueError("Предложение не ожидает решения.")
        if payload["object_table"] != "vehicles" or payload["field_name"] not in DIRECT_FIELDS:
            raise ValueError("Для этого поля нужен специальный сценарий; простое утверждение не меняет БД.")
        field = payload["field_name"]
        vehicle_id = int(payload["object_id"])
        row = conn.execute(f"SELECT {field} FROM vehicles WHERE id=?", (vehicle_id,)).fetchone()
        if not row or str(row[field] or "") != payload["old_value"]:
            raise ValueError("Исходное значение изменилось. Требуется повторная проверка предложения.")
        value = str(payload["proposed_value"])
        normalizer = _utils.normalize_car_model if field == "car_model" else _utils.normalize_color
        conn.execute(
            f"UPDATE vehicles SET {field}=?,{DIRECT_FIELDS[field]}=?,updated_at=?,updated_by=? WHERE id=?",
            (value, normalizer(value), _now(), str(reviewer_id), vehicle_id),
        )
        _audit(conn, actor=str(reviewer_id), table="vehicles", row_id=vehicle_id,
               field=field, old=payload["old_value"], new=value,
               action="quality_proposal_approved", note=note)
        conn.execute(
            """UPDATE operator_task_queue SET status='RESOLVED',updated_at=?,closed_at=?,
                   close_note=? WHERE id=?""",
            (_now(), _now(), note, task_id),
        )
        _audit(conn, actor=str(reviewer_id), table="operator_task_queue", row_id=task_id,
               field="status", old="PENDING", new="RESOLVED",
               action="quality_proposal_resolved", note=note)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def reject_proposal(*, task_id: int, reviewer_id: int | str, note: str) -> None:
    note = note.strip()
    if not note:
        raise ValueError("Укажите причину отказа.")
    conn = get_conn()
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN IMMEDIATE")
        if not _is_super_admin(reviewer_id, conn):
            raise PermissionError("Только SUPER_ADMIN может отклонять предложения.")
        task, _payload = _load_task(conn, task_id)
        if task["status"] not in {"PENDING", "NEEDS_CLARIFICATION"}:
            raise ValueError("Предложение уже закрыто.")
        conn.execute(
            "UPDATE operator_task_queue SET status='REJECTED',updated_at=?,closed_at=?,close_note=? WHERE id=?",
            (_now(), _now(), note, task_id),
        )
        _audit(conn, actor=str(reviewer_id), table="operator_task_queue", row_id=task_id,
               field="status", old=task["status"], new="REJECTED",
               action="quality_proposal_rejected", note=note)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
