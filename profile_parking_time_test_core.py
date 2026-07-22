# -*- coding: utf-8 -*-
"""
Isolated operator TEST session for a missing parking_time value.

This module is deliberately separate from resident profile verification.

It may read actual apartment/vehicle data to create a frozen test snapshot,
but it never:
- creates/updates resident_profile_verifications;
- creates a resident welcome, resident request, order, payment or subscription;
- sends a resident message;
- writes vehicles.parking_time;
- changes any contact or access credential.

All writes are confined to these TEST-only tables:
- profile_parking_time_test_schema_migrations
- profile_parking_time_test_sessions
- profile_parking_time_test_events
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any


TEST_SCHEMA_MIGRATION_CODE = "PROFILE_PARKING_TIME_TEST_V1"
TEST_TARGET_APARTMENT = "40"

STATUS_OPEN = "OPEN"
STATUS_PENDING_OPERATOR = "PENDING_OPERATOR"
STATUS_APPROVED_NO_WRITE = "APPROVED_TEST_NO_WRITE"
STATUS_REJECTED_NO_WRITE = "REJECTED_TEST_NO_WRITE"
STATUS_CLOSED_NO_WRITE = "CLOSED_TEST_NO_WRITE"

FINAL_STATUSES = {
    STATUS_APPROVED_NO_WRITE,
    STATUS_REJECTED_NO_WRITE,
    STATUS_CLOSED_NO_WRITE,
}

PARKING_TIME_LABELS = {
    "Day": "☀️ Day",
    "Night": "🌙 Night",
    "Inactive": "🚫 Не користується паркуванням",
}


def now_db() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def quote(identifier: str) -> str:
    return '"' + str(identifier).replace('"', '""') + '"'


def table_exists(cur: sqlite3.Cursor, table: str) -> bool:
    cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?",
        (table,),
    )
    return cur.fetchone() is not None


def table_columns(cur: sqlite3.Cursor, table: str) -> set[str]:
    if not table_exists(cur, table):
        return set()
    cur.execute(f"PRAGMA table_info({quote(table)})")
    return {str(row[1]) for row in cur.fetchall()}


def _fetchone(
    cur: sqlite3.Cursor,
    sql: str,
    params: tuple[Any, ...] = (),
) -> dict | None:
    cur.execute(sql, params)
    row = cur.fetchone()
    if row is None:
        return None
    if isinstance(row, sqlite3.Row):
        return dict(row)
    names = [str(col[0]) for col in (cur.description or [])]
    return dict(zip(names, row))


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _decode(value: Any, default: Any) -> Any:
    try:
        return json.loads(text(value))
    except Exception:
        return default


def required_test_tables() -> set[str]:
    return {
        "profile_parking_time_test_schema_migrations",
        "profile_parking_time_test_sessions",
        "profile_parking_time_test_events",
    }


def _create_schema(cur: sqlite3.Cursor) -> None:
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS profile_parking_time_test_schema_migrations (
            migration_code TEXT PRIMARY KEY,
            schema_version TEXT NOT NULL,
            applied_at TEXT NOT NULL,
            applied_by TEXT,
            sandbox_db_path TEXT,
            note TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS profile_parking_time_test_sessions (
            id INTEGER PRIMARY KEY,
            session_number TEXT NOT NULL UNIQUE,
            test_scope TEXT NOT NULL DEFAULT 'PARKING_TIME_NO_WRITE',
            target_apartment_id INTEGER,
            target_apartment_number TEXT NOT NULL,
            target_vehicle_id INTEGER NOT NULL,
            plate_snapshot TEXT,
            model_snapshot TEXT,
            color_snapshot TEXT,
            original_parking_time_snapshot TEXT,
            proposed_parking_time TEXT,
            test_status TEXT NOT NULL
                CHECK(test_status IN (
                    'OPEN',
                    'PENDING_OPERATOR',
                    'APPROVED_TEST_NO_WRITE',
                    'REJECTED_TEST_NO_WRITE',
                    'CLOSED_TEST_NO_WRITE'
                )),
            opened_by TEXT NOT NULL,
            opened_at TEXT NOT NULL,
            proposed_by TEXT,
            proposed_at TEXT,
            reviewed_by TEXT,
            reviewed_at TEXT,
            review_note TEXT,
            close_reason TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_profile_parking_time_test_sessions_queue
        ON profile_parking_time_test_sessions(test_status, opened_at, id)
        """
    )
    cur.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ux_profile_parking_time_test_active_vehicle
        ON profile_parking_time_test_sessions(target_vehicle_id)
        WHERE test_status IN ('OPEN', 'PENDING_OPERATOR')
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS profile_parking_time_test_events (
            id INTEGER PRIMARY KEY,
            event_number TEXT NOT NULL UNIQUE,
            session_id INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            actor_id TEXT,
            payload_json TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_profile_parking_time_test_events_session
        ON profile_parking_time_test_events(session_id, created_at, id)
        """
    )


def _next_number(prefix: str, cur: sqlite3.Cursor, table: str, column: str) -> str:
    day = datetime.now().strftime("%Y%m%d")
    cur.execute(
        f"""
        SELECT {quote(column)}
        FROM {quote(table)}
        WHERE {quote(column)} LIKE ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (f"{prefix}-{day}-%",),
    )
    row = cur.fetchone()
    number = 1
    if row and text(row[0]):
        try:
            number = int(text(row[0]).rsplit("-", 1)[1]) + 1
        except Exception:
            number = 1
    return f"{prefix}-{day}-{number:06d}"


def _event(
    cur: sqlite3.Cursor,
    *,
    session_id: int,
    event_type: str,
    actor_id: int | str | None,
    payload: dict,
) -> None:
    cur.execute(
        """
        INSERT INTO profile_parking_time_test_events (
            event_number, session_id, event_type, actor_id, payload_json, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            _next_number(
                "PTTE",
                cur,
                "profile_parking_time_test_events",
                "event_number",
            ),
            int(session_id),
            text(event_type),
            None if actor_id is None else str(actor_id),
            _json(payload),
            now_db(),
        ),
    )


def ensure_test_schema(
    conn: sqlite3.Connection,
    *,
    actor_id: str = "parking_time_test_migration",
    sandbox_db_path: str = "",
) -> list[str]:
    """Create only TEST tables; caller owns commit/rollback."""
    cur = conn.cursor()
    before = {
        str(row[0])
        for row in cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    _create_schema(cur)
    created = sorted(required_test_tables() - before)
    changes = [f"created table {name}" for name in created]

    row = _fetchone(
        cur,
        """
        SELECT migration_code
        FROM profile_parking_time_test_schema_migrations
        WHERE migration_code = ?
        """,
        (TEST_SCHEMA_MIGRATION_CODE,),
    )
    if not row:
        cur.execute(
            """
            INSERT INTO profile_parking_time_test_schema_migrations (
                migration_code, schema_version, applied_at, applied_by,
                sandbox_db_path, note
            )
            VALUES (?, '1.0', ?, ?, ?, ?)
            """,
            (
                TEST_SCHEMA_MIGRATION_CODE,
                now_db(),
                text(actor_id),
                text(sandbox_db_path),
                "Isolated no-write test sessions for missing parking_time.",
            ),
        )
        changes.append(f"recorded migration {TEST_SCHEMA_MIGRATION_CODE}")
    return changes or ["TEST schema already present"]


def _first_value(row: dict, names: tuple[str, ...]) -> str:
    for name in names:
        value = text(row.get(name))
        if value:
            return value
    return ""


def _apartment_by_number(cur: sqlite3.Cursor, apartment_number: str) -> dict:
    if not table_exists(cur, "apartments"):
        raise RuntimeError("Не найдена основная таблица apartments.")
    cols = table_columns(cur, "apartments")
    number_col = next(
        (
            name for name in (
                "apartment_number",
                "unit_number",
                "number",
                "display_number",
            )
            if name in cols
        ),
        None,
    )
    if not number_col:
        raise RuntimeError("В apartments не найдено поле номера квартиры.")

    target = text(apartment_number)
    row = _fetchone(
        cur,
        f"""
        SELECT *
        FROM {quote('apartments')}
        WHERE CAST({quote(number_col)} AS TEXT) IN (?, ?)
        ORDER BY id ASC
        LIMIT 1
        """,
        (target, target + ".0"),
    )
    if not row:
        raise ValueError(f"Квартира {target} не найдена.")
    row["_apartment_number"] = text(row.get(number_col))
    return row


def _missing_parking_time_vehicles(
    cur: sqlite3.Cursor,
    *,
    apartment_id: int | None,
    apartment_number: str,
) -> list[dict]:
    if not table_exists(cur, "vehicles"):
        raise RuntimeError("Не найдена таблица vehicles.")
    cols = table_columns(cur, "vehicles")
    if "id" not in cols or "parking_time" not in cols:
        raise RuntimeError("В vehicles отсутствуют id или parking_time.")

    clauses: list[str] = []
    params: list[Any] = []
    if apartment_id is not None and "apartment_id" in cols:
        clauses.append(f"{quote('apartment_id')} = ?")
        params.append(int(apartment_id))
    if apartment_number and "apartment_number" in cols:
        clauses.append(
            f"CAST({quote('apartment_number')} AS TEXT) IN (?, ?)"
        )
        params.extend([text(apartment_number), text(apartment_number) + ".0"])
    if not clauses:
        raise RuntimeError("Не найдено поле связи автомобиля с квартирой.")

    cur.execute(
        f"""
        SELECT *
        FROM {quote('vehicles')}
        WHERE ({' OR '.join(clauses)})
          AND (
              {quote('parking_time')} IS NULL
              OR trim(CAST({quote('parking_time')} AS TEXT)) = ''
          )
        ORDER BY id ASC
        """,
        tuple(params),
    )
    result: list[dict] = []
    for raw in cur.fetchall():
        row = dict(raw)
        result.append(
            {
                "id": int(row["id"]),
                "plate": _first_value(
                    row,
                    (
                        "license_plate_normalized",
                        "license_plate",
                        "plate_number",
                        "plate",
                        "number",
                    ),
                ),
                "model": _first_value(
                    row,
                    (
                        "car_model_normalized",
                        "car_model",
                        "make_model",
                        "model",
                        "vehicle_model",
                    ),
                ),
                "color": _first_value(
                    row,
                    (
                        "car_color_normalized",
                        "car_color",
                        "color",
                        "colour",
                        "vehicle_color",
                    ),
                ),
                "parking_time": text(row.get("parking_time")),
            }
        )
    return result


def test_candidate(
    *,
    apartment_number: str = TEST_TARGET_APARTMENT,
    conn: sqlite3.Connection,
) -> dict:
    """
    Read source data only. Does not create a resident profile or write an event.
    """
    cur = conn.cursor()
    apartment = _apartment_by_number(cur, text(apartment_number))
    vehicles = _missing_parking_time_vehicles(
        cur,
        apartment_id=apartment.get("id"),
        apartment_number=text(apartment.get("_apartment_number")),
    )
    return {
        "apartment_id": apartment.get("id"),
        "apartment_number": text(apartment.get("_apartment_number")),
        "vehicles": vehicles,
        "suitable": bool(vehicles),
    }


def _require_test_tables(cur: sqlite3.Cursor) -> None:
    missing = sorted(required_test_tables() - {
        str(row[0])
        for row in cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    })
    if missing:
        raise RuntimeError(
            "TEST-схема не установлена. Не хватает таблиц: " + ", ".join(missing)
        )


def get_test_session(
    *,
    session_id: int,
    conn: sqlite3.Connection,
) -> dict | None:
    cur = conn.cursor()
    _require_test_tables(cur)
    row = _fetchone(
        cur,
        """
        SELECT *
        FROM profile_parking_time_test_sessions
        WHERE id = ?
        """,
        (int(session_id),),
    )
    if row:
        row["events"] = list_test_events(session_id=int(session_id), conn=conn)
    return row


def list_test_events(
    *,
    session_id: int,
    conn: sqlite3.Connection,
) -> list[dict]:
    cur = conn.cursor()
    _require_test_tables(cur)
    cur.execute(
        """
        SELECT *
        FROM profile_parking_time_test_events
        WHERE session_id = ?
        ORDER BY id ASC
        """,
        (int(session_id),),
    )
    rows = []
    for raw in cur.fetchall():
        item = dict(raw)
        item["payload"] = _decode(item.get("payload_json"), {})
        rows.append(item)
    return rows


def list_test_sessions(
    *,
    conn: sqlite3.Connection,
    include_final: bool = False,
    limit: int = 100,
) -> list[dict]:
    cur = conn.cursor()
    _require_test_tables(cur)
    where = "" if include_final else (
        "WHERE test_status IN ('OPEN', 'PENDING_OPERATOR')"
    )
    cur.execute(
        f"""
        SELECT *
        FROM profile_parking_time_test_sessions
        {where}
        ORDER BY opened_at ASC, id ASC
        LIMIT ?
        """,
        (int(limit),),
    )
    return [dict(row) for row in cur.fetchall()]


def _session_simulation(session: dict) -> dict:
    status = text(session.get("test_status"))
    proposed = text(session.get("proposed_parking_time"))
    original = text(session.get("original_parking_time_snapshot"))
    if original:
        return {
            "state": "NOT_APPLICABLE",
            "text": "Джерельний parking_time уже не порожній; тест неактуальний.",
        }
    if status == STATUS_OPEN:
        return {
            "state": "BLOCKED_MISSING_PARKING_TIME",
            "text": (
                "🧪 Симуляція: телефонний доступ заблокований — "
                "режим паркування не визначено."
            ),
        }
    if status == STATUS_PENDING_OPERATOR:
        return {
            "state": "BLOCKED_PENDING_OPERATOR",
            "text": (
                "🧪 Симуляція: телефонний доступ заблокований — "
                "очікується рішення оператора щодо TEST-заявки."
            ),
        }
    if status == STATUS_APPROVED_NO_WRITE:
        return {
            "state": "SIMULATED_NEXT_STEP_RESIDENT_CONFIRMATION",
            "text": (
                "🧪 TEST підтверджено без зміни даних. Якби оператор застосував "
                f"{PARKING_TIME_LABELS.get(proposed, proposed)} в реальній процедурі, "
                "далі знадобилося б підтвердження мешканця. Джерельна база не змінена; "
                "реальний телефонний доступ усе ще заблокований."
            ),
        }
    if status == STATUS_REJECTED_NO_WRITE:
        return {
            "state": "BLOCKED_REJECTED",
            "text": (
                "🧪 TEST відхилено без зміни даних. Джерельний parking_time лишився порожнім."
            ),
        }
    return {
        "state": "CLOSED_NO_WRITE",
        "text": (
            "🧪 TEST закрито без зміни даних. Джерельний parking_time лишився порожнім."
        ),
    }


def open_test_session(
    *,
    apartment_number: str = TEST_TARGET_APARTMENT,
    vehicle_id: int | None = None,
    actor_id: int | str | None,
    conn: sqlite3.Connection,
) -> dict:
    """
    Create a TEST-only snapshot. Writes solely to profile_parking_time_test_*.
    """
    cur = conn.cursor()
    _require_test_tables(cur)
    candidate = test_candidate(apartment_number=text(apartment_number), conn=conn)
    if not candidate["suitable"]:
        raise ValueError(
            f"У квартирі {apartment_number} немає автомобіля з порожнім parking_time."
        )

    vehicles = candidate["vehicles"]
    selected = None
    if vehicle_id is not None:
        selected = next(
            (row for row in vehicles if int(row["id"]) == int(vehicle_id)),
            None,
        )
        if not selected:
            raise ValueError("Обраний автомобіль не має порожнього parking_time.")
    elif len(vehicles) == 1:
        selected = vehicles[0]
    else:
        raise ValueError("Для тесту потрібно окремо обрати автомобіль.")

    active = _fetchone(
        cur,
        """
        SELECT *
        FROM profile_parking_time_test_sessions
        WHERE target_vehicle_id = ?
          AND test_status IN ('OPEN', 'PENDING_OPERATOR')
        ORDER BY id DESC
        LIMIT 1
        """,
        (int(selected["id"]),),
    )
    if active:
        active["events"] = list_test_events(session_id=int(active["id"]), conn=conn)
        return active

    now = now_db()
    session_number = _next_number(
        "PTT",
        cur,
        "profile_parking_time_test_sessions",
        "session_number",
    )
    cur.execute(
        """
        INSERT INTO profile_parking_time_test_sessions (
            session_number, target_apartment_id, target_apartment_number,
            target_vehicle_id, plate_snapshot, model_snapshot, color_snapshot,
            original_parking_time_snapshot, test_status, opened_by, opened_at,
            created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            session_number,
            candidate.get("apartment_id"),
            candidate["apartment_number"],
            int(selected["id"]),
            selected.get("plate"),
            selected.get("model"),
            selected.get("color"),
            selected.get("parking_time"),
            STATUS_OPEN,
            str(actor_id) if actor_id is not None else "operator",
            now,
            now,
            now,
        ),
    )
    session_id = int(cur.lastrowid)
    _event(
        cur,
        session_id=session_id,
        event_type="TEST_SESSION_OPENED",
        actor_id=actor_id,
        payload={
            "scope": "PARKING_TIME_NO_WRITE",
            "source_vehicle_id": int(selected["id"]),
            "source_parking_time": selected.get("parking_time"),
            "guarantee": "No resident profile or source vehicle row is changed.",
        },
    )
    return get_test_session(session_id=session_id, conn=conn) or {}


def propose_parking_time(
    *,
    session_id: int,
    parking_time: str,
    actor_id: int | str | None,
    conn: sqlite3.Connection,
) -> dict:
    if parking_time not in PARKING_TIME_LABELS:
        raise ValueError("Допустимі значення: Day, Night або Inactive.")

    cur = conn.cursor()
    _require_test_tables(cur)
    session = get_test_session(session_id=int(session_id), conn=conn)
    if not session:
        raise ValueError("TEST-сесію не знайдено.")
    if text(session.get("test_status")) != STATUS_OPEN:
        raise ValueError("Вибір уже зафіксовано або TEST-сесію закрито.")

    now = now_db()
    cur.execute(
        """
        UPDATE profile_parking_time_test_sessions
        SET proposed_parking_time = ?, test_status = ?, proposed_by = ?,
            proposed_at = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            parking_time,
            STATUS_PENDING_OPERATOR,
            str(actor_id) if actor_id is not None else "operator",
            now,
            now,
            int(session_id),
        ),
    )
    _event(
        cur,
        session_id=int(session_id),
        event_type="TEST_PARKING_TIME_PROPOSED",
        actor_id=actor_id,
        payload={
            "proposed_parking_time": parking_time,
            "label": PARKING_TIME_LABELS[parking_time],
            "guarantee": "Source vehicles.parking_time remains unchanged.",
        },
    )
    return get_test_session(session_id=int(session_id), conn=conn) or {}


def decide_test_session(
    *,
    session_id: int,
    approve: bool,
    actor_id: int | str | None,
    note: str = "",
    conn: sqlite3.Connection,
) -> dict:
    """
    Approve/reject a TEST outcome without applying any real correction.
    """
    cur = conn.cursor()
    _require_test_tables(cur)
    session = get_test_session(session_id=int(session_id), conn=conn)
    if not session:
        raise ValueError("TEST-сесію не знайдено.")
    if text(session.get("test_status")) != STATUS_PENDING_OPERATOR:
        raise ValueError("Для рішення оператора TEST-сесія має бути в черзі.")

    status = STATUS_APPROVED_NO_WRITE if approve else STATUS_REJECTED_NO_WRITE
    event_type = (
        "TEST_APPROVED_NO_SOURCE_WRITE"
        if approve
        else "TEST_REJECTED_NO_SOURCE_WRITE"
    )
    now = now_db()
    cur.execute(
        """
        UPDATE profile_parking_time_test_sessions
        SET test_status = ?, reviewed_by = ?, reviewed_at = ?,
            review_note = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            status,
            str(actor_id) if actor_id is not None else "operator",
            now,
            text(note),
            now,
            int(session_id),
        ),
    )
    _event(
        cur,
        session_id=int(session_id),
        event_type=event_type,
        actor_id=actor_id,
        payload={
            "decision": "approve" if approve else "reject",
            "note": text(note),
            "guarantee": "No source vehicle/profile/resident data were changed.",
        },
    )
    return get_test_session(session_id=int(session_id), conn=conn) or {}


def close_test_session(
    *,
    session_id: int,
    actor_id: int | str | None,
    reason: str = "TEST_CLOSED",
    conn: sqlite3.Connection,
) -> dict:
    cur = conn.cursor()
    _require_test_tables(cur)
    session = get_test_session(session_id=int(session_id), conn=conn)
    if not session:
        raise ValueError("TEST-сесію не знайдено.")
    if text(session.get("test_status")) in FINAL_STATUSES:
        return session

    now = now_db()
    cur.execute(
        """
        UPDATE profile_parking_time_test_sessions
        SET test_status = ?, reviewed_by = ?, reviewed_at = ?,
            close_reason = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            STATUS_CLOSED_NO_WRITE,
            str(actor_id) if actor_id is not None else "operator",
            now,
            text(reason),
            now,
            int(session_id),
        ),
    )
    _event(
        cur,
        session_id=int(session_id),
        event_type="TEST_CLOSED_NO_SOURCE_WRITE",
        actor_id=actor_id,
        payload={"reason": text(reason)},
    )
    return get_test_session(session_id=int(session_id), conn=conn) or {}


def session_for_display(session: dict) -> dict:
    result = dict(session)
    proposed = text(result.get("proposed_parking_time"))
    result["proposed_label"] = PARKING_TIME_LABELS.get(proposed, "—")
    result["source_parking_display"] = (
        text(result.get("original_parking_time_snapshot")) or "⛔ порожньо"
    )
    result["simulation"] = _session_simulation(result)
    return result
