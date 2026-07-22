# -*- coding: utf-8 -*-
"""
OSBB resident profile verification core.

Business purpose
----------------
Before a resident can request phone-gate access, the system must have a
confirmed and complete minimal parking profile:

* a linked apartment/unit;
* an explicit resolution of vehicles:
  - every recorded vehicle has a plate and parking_time, OR
  - the resident explicitly confirms that there is no vehicle;
* the resident confirms the currently shown data;
* no unresolved resident correction request remains.

Make/model/color are displayed for verification but are advisory by default.
They are not a phone-access blocker unless a later policy version changes that.

Privacy rule
------------
The telephone number used to open a barrier is a separate non-public access
credential. This module intentionally never reads or compares resident contact
phones, Telegram phones, or any address-book number. It only controls whether
the profile is ready to request access.

The module is additive and sandbox-safe. It creates profile-specific tables;
it does not modify vehicle/contact/order/payment data except when an operator
explicitly approves a PARKING_TIME correction request.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime
from typing import Any


PROFILE_SCHEMA_MIGRATION_CODE = "RESIDENT_PROFILE_VERIFICATION_V1"
PROFILE_POLICY_SET = "RESIDENT_PROFILE_VERIFICATION"

STATUS_NEEDS_REVIEW = "NEEDS_REVIEW"
STATUS_READY = "READY"
STATUS_PENDING_OPERATOR = "PENDING_OPERATOR"

REQUEST_PARKING_TIME = "PARKING_TIME"
REQUEST_GENERAL_CORRECTION = "GENERAL_CORRECTION"

REQUEST_PENDING = "PENDING_OPERATOR"
REQUEST_APPROVED = "APPROVED"
REQUEST_REJECTED = "REJECTED"
REQUEST_ACCEPTED_MANUAL = "ACCEPTED_MANUAL"

MODE_CHECK_PARKING = "CHECK_LINKED_PARKING_ACCOUNT"
MODE_NO_PARKING = "NOT_APPLICABLE_NO_PARKING"
MODE_MANUAL = "MANUAL_REVIEW"

SETTING_REQUIRE_CLIENT_CONFIRMATION = "PROFILE_REQUIRE_CLIENT_CONFIRMATION"
SETTING_REQUIRE_EXPLICIT_NO_VEHICLE = "PROFILE_REQUIRE_EXPLICIT_NO_VEHICLE"
SETTING_CRITICAL_REQUIRE_PLATE = "PROFILE_CRITICAL_REQUIRE_PLATE"
SETTING_CRITICAL_REQUIRE_PARKING_TIME = "PROFILE_CRITICAL_REQUIRE_PARKING_TIME"
SETTING_ALLOW_PRIVATE_ACCESS_PHONE = "PROFILE_ALLOW_PRIVATE_ACCESS_PHONE"

VALID_PARKING_TIMES = {"DAY": "Day", "NIGHT": "Night", "INACTIVE": "Inactive"}


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


def _row_dict(cur: sqlite3.Cursor, row: Any) -> dict | None:
    if row is None:
        return None
    if isinstance(row, sqlite3.Row):
        return dict(row)
    names = [str(item[0]) for item in (cur.description or [])]
    return dict(zip(names, row))


def _fetchone(
    cur: sqlite3.Cursor,
    sql: str,
    params: tuple[Any, ...] = (),
) -> dict | None:
    cur.execute(sql, params)
    return _row_dict(cur, cur.fetchone())


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _parse_json(value: Any, default: Any) -> Any:
    try:
        return json.loads(text(value))
    except Exception:
        return default


def _truth(value: Any) -> bool:
    return text(value).lower() in {"1", "true", "yes", "y", "так", "да"}


def _value(row: dict, candidates: tuple[str, ...]) -> str:
    for name in candidates:
        value = text(row.get(name))
        if value:
            return value
    return ""


def _normalise_parking_time(value: Any) -> str:
    raw = text(value).upper()
    if raw in {"", "NULL", "NONE", "N/A", "UNKNOWN", "?"}:
        return ""
    aliases = {
        "DAY": "Day",
        "ДЕНЬ": "Day",
        "ДЕННИЙ": "Day",
        "NIGHT": "Night",
        "НОЧЬ": "Night",
        "НІЧ": "Night",
        "СУТКИ": "Night",
        "INACTIVE": "Inactive",
        "НЕ ПАРКУЕТСЯ": "Inactive",
        "НЕ ПАРКУЄТЬСЯ": "Inactive",
    }
    return aliases.get(raw, text(value))


def _vehicle_snapshot_row(row: dict) -> dict:
    vehicle_id_raw = row.get("id")
    try:
        vehicle_id = int(vehicle_id_raw) if vehicle_id_raw is not None else None
    except (TypeError, ValueError):
        vehicle_id = None

    plate = _value(
        row,
        (
            "license_plate_normalized",
            "license_plate",
            "plate_number",
            "plate",
            "number",
        ),
    )
    model = _value(
        row,
        (
            "car_model_normalized",
            "car_model",
            "make_model",
            "model",
            "vehicle_model",
        ),
    )
    colour = _value(
        row,
        (
            "car_color_normalized",
            "car_color",
            "color",
            "colour",
            "vehicle_color",
        ),
    )
    parking_time = _normalise_parking_time(row.get("parking_time"))
    return {
        "id": vehicle_id,
        "plate": plate,
        "model": model,
        "color": colour,
        "parking_time": parking_time,
    }


def _list_vehicles(
    cur: sqlite3.Cursor,
    *,
    apartment_id: int | None,
    apartment_number: str,
) -> tuple[list[dict], str | None]:
    """
    Return vehicles for the currently linked unit using whichever supported
    link columns are available. Does not infer anything from contact phones.
    """
    if not table_exists(cur, "vehicles"):
        return [], "VEHICLES_TABLE_UNAVAILABLE"

    cols = table_columns(cur, "vehicles")
    where = ""
    params: tuple[Any, ...] = ()
    if apartment_id is not None and "apartment_id" in cols:
        where = "apartment_id = ?"
        params = (int(apartment_id),)
    elif apartment_number and "apartment_number" in cols:
        where = "CAST(apartment_number AS TEXT) = ?"
        params = (text(apartment_number),)
    else:
        return [], "VEHICLE_LINK_UNAVAILABLE"

    cur.execute(
        f"""
        SELECT *
        FROM vehicles
        WHERE {where}
        ORDER BY id ASC
        """,
        params,
    )
    return [_vehicle_snapshot_row(dict(row)) for row in cur.fetchall()], None


def _snapshot_signature(
    *,
    apartment_id: int | None,
    apartment_number: str,
    vehicles: list[dict],
    no_vehicle_declared: bool,
) -> str:
    normalized = {
        "apartment_id": int(apartment_id) if apartment_id is not None else None,
        "apartment_number": text(apartment_number),
        "no_vehicle_declared": bool(no_vehicle_declared),
        "vehicles": [
            {
                "id": row.get("id"),
                "plate": text(row.get("plate")).upper(),
                "model": text(row.get("model")).upper(),
                "color": text(row.get("color")).upper(),
                "parking_time": text(row.get("parking_time")).upper(),
            }
            for row in vehicles
        ],
    }
    raw = _json(normalized).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _create_schema(cur: sqlite3.Cursor) -> None:
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS resident_profile_schema_migrations (
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
        CREATE TABLE IF NOT EXISTS resident_profile_policy_versions (
            id INTEGER PRIMARY KEY,
            policy_set_code TEXT NOT NULL,
            version_number INTEGER NOT NULL CHECK(version_number > 0),
            policy_status TEXT NOT NULL DEFAULT 'ACTIVE'
                CHECK(policy_status IN ('DRAFT', 'ACTIVE', 'RETIRED', 'ARCHIVED')),
            effective_from TEXT NOT NULL,
            effective_to TEXT,
            approved_by TEXT,
            approval_reference TEXT,
            change_reason TEXT,
            created_by TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(policy_set_code, version_number),
            UNIQUE(policy_set_code, effective_from)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS resident_profile_policy_values (
            id INTEGER PRIMARY KEY,
            policy_version_id INTEGER NOT NULL,
            setting_code TEXT NOT NULL,
            value_type TEXT NOT NULL
                CHECK(value_type IN ('TEXT', 'INTEGER', 'BOOLEAN', 'JSON')),
            value_text TEXT NOT NULL,
            description TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(policy_version_id, setting_code)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS resident_profile_verifications (
            id INTEGER PRIMARY KEY,
            resident_account_id INTEGER NOT NULL UNIQUE,
            apartment_id INTEGER,
            apartment_number TEXT,
            verification_status TEXT NOT NULL DEFAULT 'NEEDS_REVIEW'
                CHECK(verification_status IN (
                    'NEEDS_REVIEW', 'READY', 'PENDING_OPERATOR', 'ARCHIVED'
                )),
            no_vehicle_declared INTEGER NOT NULL DEFAULT 0 CHECK(no_vehicle_declared IN (0, 1)),
            no_vehicle_declared_at TEXT,
            no_vehicle_declared_by TEXT,
            resident_confirmed_at TEXT,
            resident_confirmed_by TEXT,
            verified_snapshot_hash TEXT,
            last_snapshot_hash TEXT,
            parking_debt_check_mode TEXT NOT NULL DEFAULT 'MANUAL_REVIEW'
                CHECK(parking_debt_check_mode IN (
                    'CHECK_LINKED_PARKING_ACCOUNT',
                    'NOT_APPLICABLE_NO_PARKING',
                    'MANUAL_REVIEW'
                )),
            welcome_shown_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_resident_profile_verifications_status
        ON resident_profile_verifications(verification_status, apartment_id, apartment_number)
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS resident_profile_change_requests (
            id INTEGER PRIMARY KEY,
            request_number TEXT NOT NULL UNIQUE,
            resident_account_id INTEGER NOT NULL,
            apartment_id INTEGER,
            apartment_number TEXT,
            vehicle_id INTEGER,
            request_type TEXT NOT NULL
                CHECK(request_type IN ('PARKING_TIME', 'GENERAL_CORRECTION')),
            current_value_json TEXT,
            requested_value_json TEXT NOT NULL,
            resident_note TEXT,
            request_status TEXT NOT NULL DEFAULT 'PENDING_OPERATOR'
                CHECK(request_status IN (
                    'PENDING_OPERATOR', 'APPROVED', 'REJECTED',
                    'ACCEPTED_MANUAL', 'CANCELLED'
                )),
            submitted_at TEXT NOT NULL,
            resolved_at TEXT,
            resolved_by TEXT,
            resolution_note TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_resident_profile_requests_queue
        ON resident_profile_change_requests(request_status, submitted_at, apartment_number)
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS resident_profile_operation_journal (
            id INTEGER PRIMARY KEY,
            event_number TEXT NOT NULL UNIQUE,
            resident_account_id INTEGER,
            apartment_id INTEGER,
            apartment_number TEXT,
            request_id INTEGER,
            event_type TEXT NOT NULL,
            actor_id TEXT,
            payload_json TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_resident_profile_journal_account
        ON resident_profile_operation_journal(resident_account_id, created_at, id)
        """
    )


def _next_number(prefix: str, cur: sqlite3.Cursor, table: str, column: str) -> str:
    # Keep external numbers human-readable; table primary keys remain authoritative.
    today = datetime.now().strftime("%Y%m%d")
    cur.execute(
        f"""
        SELECT {quote(column)}
        FROM {quote(table)}
        WHERE {quote(column)} LIKE ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (f"{prefix}-{today}-%",),
    )
    row = cur.fetchone()
    sequence = 1
    if row and text(row[0]):
        try:
            sequence = int(text(row[0]).rsplit("-", 1)[1]) + 1
        except Exception:
            sequence = 1
    return f"{prefix}-{today}-{sequence:06d}"


def _journal(
    cur: sqlite3.Cursor,
    *,
    resident_account_id: int | None,
    apartment_id: int | None,
    apartment_number: str,
    request_id: int | None,
    event_type: str,
    actor_id: int | str | None,
    payload: dict,
) -> None:
    cur.execute(
        """
        INSERT INTO resident_profile_operation_journal (
            event_number, resident_account_id, apartment_id, apartment_number,
            request_id, event_type, actor_id, payload_json, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            _next_number(
                "RPE",
                cur,
                "resident_profile_operation_journal",
                "event_number",
            ),
            resident_account_id,
            apartment_id,
            text(apartment_number),
            request_id,
            event_type,
            None if actor_id is None else str(actor_id),
            _json(payload),
            now_db(),
        ),
    )


def _ensure_policy(cur: sqlite3.Cursor, *, effective_from: str, actor_id: str) -> int:
    row = _fetchone(
        cur,
        """
        SELECT *
        FROM resident_profile_policy_versions
        WHERE policy_set_code = ? AND effective_from = ?
        """,
        (PROFILE_POLICY_SET, effective_from),
    )
    if row:
        return int(row["id"])

    cur.execute(
        """
        SELECT COALESCE(MAX(version_number), 0) + 1
        FROM resident_profile_policy_versions
        WHERE policy_set_code = ?
        """,
        (PROFILE_POLICY_SET,),
    )
    version = int(cur.fetchone()[0])
    now = now_db()
    cur.execute(
        """
        INSERT INTO resident_profile_policy_versions (
            policy_set_code, version_number, policy_status, effective_from,
            approval_reference, change_reason, created_by, created_at, updated_at
        )
        VALUES (?, ?, 'ACTIVE', ?, 'SANDBOX_INITIAL_SEED',
                'Initial resident profile verification policy for sandbox.',
                ?, ?, ?)
        """,
        (PROFILE_POLICY_SET, version, effective_from, actor_id, now, now),
    )
    return int(cur.lastrowid)


def _ensure_policy_value(
    cur: sqlite3.Cursor,
    *,
    policy_id: int,
    code: str,
    value_type: str,
    value: str,
    description: str,
) -> None:
    existing = _fetchone(
        cur,
        """
        SELECT *
        FROM resident_profile_policy_values
        WHERE policy_version_id = ? AND setting_code = ?
        """,
        (int(policy_id), code),
    )
    if existing:
        expected = (value_type, value)
        actual = (text(existing["value_type"]), text(existing["value_text"]))
        if actual != expected:
            raise RuntimeError(
                "Existing resident-profile policy value differs: " + code
            )
        return
    now = now_db()
    cur.execute(
        """
        INSERT INTO resident_profile_policy_values (
            policy_version_id, setting_code, value_type, value_text,
            description, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (policy_id, code, value_type, value, description, now, now),
    )


def ensure_profile_verification_schema(
    conn: sqlite3.Connection,
    *,
    effective_from: str = "2026-06-27",
    actor_id: str = "profile_verification_migration",
    sandbox_db_path: str = "",
) -> list[str]:
    """
    Create the additive profile-verification tables and seed policy v1.

    The caller owns transaction handling. The function is idempotent.
    """
    cur = conn.cursor()
    existing = {
        str(row[0])
        for row in cur.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    _create_schema(cur)
    expected = {
        "resident_profile_schema_migrations",
        "resident_profile_policy_versions",
        "resident_profile_policy_values",
        "resident_profile_verifications",
        "resident_profile_change_requests",
        "resident_profile_operation_journal",
    }
    changes = [f"created table {name}" for name in sorted(expected - existing)]

    policy_id = _ensure_policy(
        cur, effective_from=text(effective_from), actor_id=text(actor_id)
    )
    values = [
        (
            SETTING_REQUIRE_CLIENT_CONFIRMATION,
            "BOOLEAN",
            "1",
            "Phone access requires a resident confirmation of the shown profile.",
        ),
        (
            SETTING_REQUIRE_EXPLICIT_NO_VEHICLE,
            "BOOLEAN",
            "1",
            "No vehicle must be an explicit declaration, not an inference.",
        ),
        (
            SETTING_CRITICAL_REQUIRE_PLATE,
            "BOOLEAN",
            "1",
            "Vehicle plate is critical for phone-access readiness.",
        ),
        (
            SETTING_CRITICAL_REQUIRE_PARKING_TIME,
            "BOOLEAN",
            "1",
            "Vehicle parking_time is critical for phone-access readiness.",
        ),
        (
            SETTING_ALLOW_PRIVATE_ACCESS_PHONE,
            "BOOLEAN",
            "1",
            "Access phone may differ from resident contact/Telegram phones.",
        ),
    ]
    for code, typ, value, description in values:
        before = _fetchone(
            cur,
            """
            SELECT 1 AS present
            FROM resident_profile_policy_values
            WHERE policy_version_id = ? AND setting_code = ?
            """,
            (policy_id, code),
        )
        _ensure_policy_value(
            cur,
            policy_id=policy_id,
            code=code,
            value_type=typ,
            value=value,
            description=description,
        )
        if not before:
            changes.append(f"seeded policy value {code} = {value}")

    migration = _fetchone(
        cur,
        """
        SELECT migration_code
        FROM resident_profile_schema_migrations
        WHERE migration_code = ?
        """,
        (PROFILE_SCHEMA_MIGRATION_CODE,),
    )
    if not migration:
        cur.execute(
            """
            INSERT INTO resident_profile_schema_migrations (
                migration_code, schema_version, applied_at, applied_by,
                sandbox_db_path, note
            )
            VALUES (?, '1.0', ?, ?, ?, ?)
            """,
            (
                PROFILE_SCHEMA_MIGRATION_CODE,
                now_db(),
                text(actor_id),
                text(sandbox_db_path),
                "Additive resident profile verification schema.",
            ),
        )
        _journal(
            cur,
            resident_account_id=None,
            apartment_id=None,
            apartment_number="",
            request_id=None,
            event_type="SCHEMA_MIGRATION_APPLIED",
            actor_id=actor_id,
            payload={"migration_code": PROFILE_SCHEMA_MIGRATION_CODE},
        )
        changes.append(f"recorded migration {PROFILE_SCHEMA_MIGRATION_CODE}")

    return changes or ["profile-verification schema and policy already present"]


def _active_policy(cur: sqlite3.Cursor) -> dict[str, str]:
    row = _fetchone(
        cur,
        """
        SELECT *
        FROM resident_profile_policy_versions
        WHERE policy_set_code = ?
          AND policy_status = 'ACTIVE'
          AND effective_from <= date('now')
          AND (effective_to IS NULL OR effective_to >= date('now'))
        ORDER BY effective_from DESC, version_number DESC
        LIMIT 1
        """,
        (PROFILE_POLICY_SET,),
    )
    if not row:
        # In synthetic tests / dates before effective date use the latest active
        # policy rather than silently treating the profile as unrestricted.
        row = _fetchone(
            cur,
            """
            SELECT *
            FROM resident_profile_policy_versions
            WHERE policy_set_code = ? AND policy_status = 'ACTIVE'
            ORDER BY effective_from DESC, version_number DESC
            LIMIT 1
            """,
            (PROFILE_POLICY_SET,),
        )
    if not row:
        raise LookupError("Resident-profile policy is not configured.")
    cur.execute(
        """
        SELECT setting_code, value_text
        FROM resident_profile_policy_values
        WHERE policy_version_id = ?
        """,
        (int(row["id"]),),
    )
    return {text(item[0]): text(item[1]) for item in cur.fetchall()}


def _profile_row(cur: sqlite3.Cursor, resident_account_id: int) -> dict | None:
    return _fetchone(
        cur,
        """
        SELECT *
        FROM resident_profile_verifications
        WHERE resident_account_id = ?
        """,
        (int(resident_account_id),),
    )


def _ensure_profile_row(
    cur: sqlite3.Cursor,
    *,
    resident_account_id: int,
    apartment_id: int | None,
    apartment_number: str,
) -> dict:
    row = _profile_row(cur, resident_account_id)
    if row:
        changed_link = (
            (row.get("apartment_id") != apartment_id)
            or (text(row.get("apartment_number")) != text(apartment_number))
        )
        if changed_link:
            now = now_db()
            cur.execute(
                """
                UPDATE resident_profile_verifications
                SET apartment_id = ?, apartment_number = ?,
                    verification_status = ?, resident_confirmed_at = NULL,
                    resident_confirmed_by = NULL, verified_snapshot_hash = NULL,
                    no_vehicle_declared = 0, no_vehicle_declared_at = NULL,
                    no_vehicle_declared_by = NULL,
                    parking_debt_check_mode = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    apartment_id,
                    text(apartment_number),
                    STATUS_NEEDS_REVIEW,
                    MODE_MANUAL,
                    now,
                    int(row["id"]),
                ),
            )
            row = _profile_row(cur, resident_account_id)
        return row

    now = now_db()
    cur.execute(
        """
        INSERT INTO resident_profile_verifications (
            resident_account_id, apartment_id, apartment_number,
            verification_status, parking_debt_check_mode, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(resident_account_id),
            apartment_id,
            text(apartment_number),
            STATUS_NEEDS_REVIEW,
            MODE_MANUAL,
            now,
            now,
        ),
    )
    return _profile_row(cur, resident_account_id) or {}


def _open_request_count(cur: sqlite3.Cursor, resident_account_id: int) -> int:
    cur.execute(
        """
        SELECT COUNT(*)
        FROM resident_profile_change_requests
        WHERE resident_account_id = ?
          AND request_status = ?
        """,
        (int(resident_account_id), REQUEST_PENDING),
    )
    return int(cur.fetchone()[0] or 0)


def _critical_and_advisory(
    *,
    profile: dict,
    vehicles: list[dict],
    vehicle_error: str | None,
    policy: dict[str, str],
) -> tuple[list[dict], list[dict]]:
    critical: list[dict] = []
    advisory: list[dict] = []

    if vehicle_error:
        critical.append(
            {
                "code": vehicle_error,
                "text": "Не вдалося надійно прочитати дані автомобілів.",
            }
        )
        return critical, advisory

    if not vehicles:
        if _truth(profile.get("no_vehicle_declared")):
            pass
        elif _truth(policy.get(SETTING_REQUIRE_EXPLICIT_NO_VEHICLE, "1")):
            critical.append(
                {
                    "code": "CONFIRM_NO_VEHICLE",
                    "text": "Підтвердьте, що автомобіля немає, або додайте/уточніть дані авто.",
                }
            )
        return critical, advisory

    for vehicle in vehicles:
        label = text(vehicle.get("plate")) or f"авто ID {vehicle.get('id') or '—'}"
        if _truth(policy.get(SETTING_CRITICAL_REQUIRE_PLATE, "1")) and not text(
            vehicle.get("plate")
        ):
            critical.append(
                {
                    "code": "VEHICLE_PLATE",
                    "vehicle_id": vehicle.get("id"),
                    "text": f"Для {label} не вказано державний номер.",
                }
            )
        if _truth(
            policy.get(SETTING_CRITICAL_REQUIRE_PARKING_TIME, "1")
        ) and not text(vehicle.get("parking_time")):
            critical.append(
                {
                    "code": "PARKING_TIME",
                    "vehicle_id": vehicle.get("id"),
                    "text": (
                        f"Для {label} не визначено режим паркування. "
                        "Оберіть: Day, Night або «Не користується паркуванням»."
                    ),
                }
            )
        if not text(vehicle.get("model")):
            advisory.append(
                {
                    "code": "VEHICLE_MODEL",
                    "vehicle_id": vehicle.get("id"),
                    "text": f"Для {label} не вказано марку/модель.",
                }
            )
        if not text(vehicle.get("color")):
            advisory.append(
                {
                    "code": "VEHICLE_COLOR",
                    "vehicle_id": vehicle.get("id"),
                    "text": f"Для {label} не вказано колір.",
                }
            )

    return critical, advisory


def profile_snapshot(
    *,
    resident_account_id: int,
    apartment_id: int | None,
    apartment_number: str,
    conn: sqlite3.Connection,
) -> dict:
    """
    Read one resident's verification snapshot.

    This function never reads resident contact phone tables. It only reviews
    apartment and vehicle records.
    """
    ensure_profile_verification_schema(conn)
    cur = conn.cursor()
    policy = _active_policy(cur)
    profile = _ensure_profile_row(
        cur,
        resident_account_id=int(resident_account_id),
        apartment_id=apartment_id,
        apartment_number=text(apartment_number),
    )
    vehicles, vehicle_error = _list_vehicles(
        cur,
        apartment_id=apartment_id,
        apartment_number=text(apartment_number),
    )
    signature = _snapshot_signature(
        apartment_id=apartment_id,
        apartment_number=text(apartment_number),
        vehicles=vehicles,
        no_vehicle_declared=_truth(profile.get("no_vehicle_declared")),
    )
    critical, advisory = _critical_and_advisory(
        profile=profile,
        vehicles=vehicles,
        vehicle_error=vehicle_error,
        policy=policy,
    )
    open_requests = _open_request_count(cur, int(resident_account_id))
    if open_requests:
        critical.append(
            {
                "code": "PENDING_OPERATOR",
                "text": "Є незавершена заявка на зміну даних. Очікується перевірка оператора.",
            }
        )
    confirmed_needed = _truth(
        policy.get(SETTING_REQUIRE_CLIENT_CONFIRMATION, "1")
    )
    is_currently_confirmed = (
        text(profile.get("verification_status")) == STATUS_READY
        and text(profile.get("verified_snapshot_hash")) == signature
    )
    structural_critical = list(critical)
    required_data_complete = not structural_critical
    resident_confirmation_required = (
        confirmed_needed
        and required_data_complete
        and not is_currently_confirmed
    )
    if resident_confirmation_required:
        critical.append(
            {
                "code": "PROFILE_CONFIRMATION",
                "text": (
                    "Обов’язкові дані заповнені. "
                    "Потрібне підтвердження мешканця."
                ),
            }
        )

    profile_ready = required_data_complete and is_currently_confirmed
    has_parking_obligation = any(
        text(row.get("parking_time")) in {"Day", "Night"} for row in vehicles
    )
    if profile_ready:
        debt_mode = MODE_CHECK_PARKING if has_parking_obligation else MODE_NO_PARKING
    else:
        debt_mode = MODE_MANUAL

    now = now_db()
    cur.execute(
        """
        UPDATE resident_profile_verifications
        SET last_snapshot_hash = ?, parking_debt_check_mode = ?, updated_at = ?
        WHERE id = ?
        """,
        (signature, debt_mode, now, int(profile["id"])),
    )
    profile = _profile_row(cur, int(resident_account_id)) or profile

    return {
        "profile": profile,
        "policy": policy,
        "vehicles": vehicles,
        "vehicle_error": vehicle_error,
        "snapshot_hash": signature,
        "critical": critical,
        "structural_critical": structural_critical,
        "advisory": advisory,
        "open_request_count": open_requests,
        "required_data_complete": required_data_complete,
        "resident_confirmation_required": resident_confirmation_required,
        "profile_ready": profile_ready,
        "phone_access_allowed": profile_ready,
        "parking_debt_check_mode": debt_mode,
        "private_access_phone_allowed": _truth(
            policy.get(SETTING_ALLOW_PRIVATE_ACCESS_PHONE, "1")
        ),
    }


def phone_access_eligibility(
    *,
    resident_account_id: int,
    apartment_id: int | None,
    apartment_number: str,
    conn: sqlite3.Connection,
) -> dict:
    """
    Gate phone access by profile completeness, never by matching phone numbers.

    The returned mode is passed to the phone-access subscription:
    * CHECK_LINKED_PARKING_ACCOUNT — at least one Day/Night vehicle;
    * NOT_APPLICABLE_NO_PARKING — confirmed no current parking obligation;
    * MANUAL_REVIEW — only while incomplete; the UI blocks creation in this case.
    """
    snapshot = profile_snapshot(
        resident_account_id=resident_account_id,
        apartment_id=apartment_id,
        apartment_number=apartment_number,
        conn=conn,
    )
    return {
        "allowed": bool(snapshot["phone_access_allowed"]),
        "critical": snapshot["critical"],
        "structural_critical": snapshot.get("structural_critical") or [],
        "required_data_complete": bool(snapshot.get("required_data_complete")),
        "resident_confirmation_required": bool(
            snapshot.get("resident_confirmation_required")
        ),
        "profile_ready": bool(snapshot.get("profile_ready")),
        "parking_debt_check_mode": snapshot["parking_debt_check_mode"],
        "parking_debt_check_note": (
            "Profile verified before phone access; "
            + (
                "linked parking obligation applies."
                if snapshot["parking_debt_check_mode"] == MODE_CHECK_PARKING
                else "no active parking obligation confirmed."
            )
        ),
        "private_access_phone_allowed": snapshot["private_access_phone_allowed"],
        "snapshot": snapshot,
    }


def mark_welcome_shown(
    *,
    resident_account_id: int,
    apartment_id: int | None,
    apartment_number: str,
    conn: sqlite3.Connection,
) -> None:
    snapshot = profile_snapshot(
        resident_account_id=resident_account_id,
        apartment_id=apartment_id,
        apartment_number=apartment_number,
        conn=conn,
    )
    profile = snapshot["profile"]
    if not profile.get("welcome_shown_at"):
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE resident_profile_verifications
            SET welcome_shown_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (now_db(), now_db(), int(profile["id"])),
        )
        _journal(
            cur,
            resident_account_id=resident_account_id,
            apartment_id=apartment_id,
            apartment_number=apartment_number,
            request_id=None,
            event_type="WELCOME_SHOWN",
            actor_id="system",
            payload={},
        )


def confirm_profile(
    *,
    resident_account_id: int,
    apartment_id: int | None,
    apartment_number: str,
    actor_id: int | str | None,
    conn: sqlite3.Connection,
) -> dict:
    snapshot = profile_snapshot(
        resident_account_id=resident_account_id,
        apartment_id=apartment_id,
        apartment_number=apartment_number,
        conn=conn,
    )
    structural = list(snapshot.get("structural_critical") or [])
    if structural:
        raise ValueError(
            "Неможливо підтвердити обов’язкові дані: "
            "є незавершені критичні поля."
        )

    debt_mode = (
        MODE_CHECK_PARKING
        if any(text(row.get("parking_time")) in {"Day", "Night"} for row in snapshot["vehicles"])
        else MODE_NO_PARKING
    )
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE resident_profile_verifications
        SET verification_status = ?, resident_confirmed_at = ?,
            resident_confirmed_by = ?, verified_snapshot_hash = ?,
            last_snapshot_hash = ?, parking_debt_check_mode = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            STATUS_READY,
            now_db(),
            str(actor_id) if actor_id is not None else "resident",
            snapshot["snapshot_hash"],
            snapshot["snapshot_hash"],
            debt_mode,
            now_db(),
            int(snapshot["profile"]["id"]),
        ),
    )
    _journal(
        cur,
        resident_account_id=resident_account_id,
        apartment_id=apartment_id,
        apartment_number=apartment_number,
        request_id=None,
        event_type="PROFILE_CONFIRMED_BY_RESIDENT",
        actor_id=actor_id,
        payload={"parking_debt_check_mode": debt_mode},
    )
    return profile_snapshot(
        resident_account_id=resident_account_id,
        apartment_id=apartment_id,
        apartment_number=apartment_number,
        conn=conn,
    )


def declare_no_vehicle(
    *,
    resident_account_id: int,
    apartment_id: int | None,
    apartment_number: str,
    actor_id: int | str | None,
    conn: sqlite3.Connection,
) -> dict:
    snapshot = profile_snapshot(
        resident_account_id=resident_account_id,
        apartment_id=apartment_id,
        apartment_number=apartment_number,
        conn=conn,
    )
    if snapshot["vehicles"]:
        raise ValueError(
            "У базі є автомобілі. Спочатку повідомте оператору, які записи слід виправити."
        )

    cur = conn.cursor()
    # The no-vehicle declaration itself is the explicit resolved state.
    signature = _snapshot_signature(
        apartment_id=apartment_id,
        apartment_number=apartment_number,
        vehicles=[],
        no_vehicle_declared=True,
    )
    now = now_db()
    cur.execute(
        """
        UPDATE resident_profile_verifications
        SET no_vehicle_declared = 1, no_vehicle_declared_at = ?,
            no_vehicle_declared_by = ?, verification_status = ?,
            resident_confirmed_at = ?, resident_confirmed_by = ?,
            verified_snapshot_hash = ?, last_snapshot_hash = ?,
            parking_debt_check_mode = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            now,
            str(actor_id) if actor_id is not None else "resident",
            STATUS_READY,
            now,
            str(actor_id) if actor_id is not None else "resident",
            signature,
            signature,
            MODE_NO_PARKING,
            now,
            int(snapshot["profile"]["id"]),
        ),
    )
    _journal(
        cur,
        resident_account_id=resident_account_id,
        apartment_id=apartment_id,
        apartment_number=apartment_number,
        request_id=None,
        event_type="NO_VEHICLE_DECLARED_BY_RESIDENT",
        actor_id=actor_id,
        payload={},
    )
    return profile_snapshot(
        resident_account_id=resident_account_id,
        apartment_id=apartment_id,
        apartment_number=apartment_number,
        conn=conn,
    )


def _vehicle_in_snapshot(snapshot: dict, vehicle_id: int) -> dict:
    for row in snapshot["vehicles"]:
        if int(row.get("id") or -1) == int(vehicle_id):
            return row
    raise ValueError("Автомобіль не належить до поточної квартири.")


def create_parking_time_request(
    *,
    resident_account_id: int,
    apartment_id: int | None,
    apartment_number: str,
    vehicle_id: int,
    parking_time: str,
    actor_id: int | str | None,
    conn: sqlite3.Connection,
) -> dict:
    proposed = _normalise_parking_time(parking_time)
    if proposed not in {"Day", "Night", "Inactive"}:
        raise ValueError("Допустимі значення: Day, Night або Не паркується.")

    snapshot = profile_snapshot(
        resident_account_id=resident_account_id,
        apartment_id=apartment_id,
        apartment_number=apartment_number,
        conn=conn,
    )
    vehicle = _vehicle_in_snapshot(snapshot, int(vehicle_id))
    cur = conn.cursor()
    duplicate = _fetchone(
        cur,
        """
        SELECT *
        FROM resident_profile_change_requests
        WHERE resident_account_id = ?
          AND vehicle_id = ?
          AND request_type = ?
          AND request_status = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (
            resident_account_id,
            int(vehicle_id),
            REQUEST_PARKING_TIME,
            REQUEST_PENDING,
        ),
    )
    if duplicate:
        raise ValueError("Для цього автомобіля вже є незавершена заявка.")

    now = now_db()
    request_number = _next_number(
        "PVR",
        cur,
        "resident_profile_change_requests",
        "request_number",
    )
    cur.execute(
        """
        INSERT INTO resident_profile_change_requests (
            request_number, resident_account_id, apartment_id, apartment_number,
            vehicle_id, request_type, current_value_json, requested_value_json,
            request_status, submitted_at, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            request_number,
            resident_account_id,
            apartment_id,
            text(apartment_number),
            int(vehicle_id),
            REQUEST_PARKING_TIME,
            _json({"parking_time": vehicle.get("parking_time"), "plate": vehicle.get("plate")}),
            _json({"parking_time": proposed, "plate": vehicle.get("plate")}),
            REQUEST_PENDING,
            now,
            now,
            now,
        ),
    )
    request_id = int(cur.lastrowid)
    cur.execute(
        """
        UPDATE resident_profile_verifications
        SET verification_status = ?, verified_snapshot_hash = NULL,
            parking_debt_check_mode = ?, updated_at = ?
        WHERE resident_account_id = ?
        """,
        (STATUS_PENDING_OPERATOR, MODE_MANUAL, now, resident_account_id),
    )
    _journal(
        cur,
        resident_account_id=resident_account_id,
        apartment_id=apartment_id,
        apartment_number=apartment_number,
        request_id=request_id,
        event_type="PARKING_TIME_CHANGE_REQUESTED",
        actor_id=actor_id,
        payload={"vehicle_id": int(vehicle_id), "parking_time": proposed},
    )
    return get_profile_request(request_id=request_id, conn=conn) or {}


def create_general_correction_request(
    *,
    resident_account_id: int,
    apartment_id: int | None,
    apartment_number: str,
    note: str,
    actor_id: int | str | None,
    conn: sqlite3.Connection,
) -> dict:
    note = text(note)
    if len(note) < 5:
        raise ValueError("Опишіть зміну щонайменше п’ятьма символами.")

    snapshot = profile_snapshot(
        resident_account_id=resident_account_id,
        apartment_id=apartment_id,
        apartment_number=apartment_number,
        conn=conn,
    )
    cur = conn.cursor()
    now = now_db()
    request_number = _next_number(
        "PVR",
        cur,
        "resident_profile_change_requests",
        "request_number",
    )
    cur.execute(
        """
        INSERT INTO resident_profile_change_requests (
            request_number, resident_account_id, apartment_id, apartment_number,
            request_type, current_value_json, requested_value_json, resident_note,
            request_status, submitted_at, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            request_number,
            resident_account_id,
            apartment_id,
            text(apartment_number),
            REQUEST_GENERAL_CORRECTION,
            _json({"snapshot_hash": snapshot["snapshot_hash"]}),
            _json({"note": note}),
            note,
            REQUEST_PENDING,
            now,
            now,
            now,
        ),
    )
    request_id = int(cur.lastrowid)
    cur.execute(
        """
        UPDATE resident_profile_verifications
        SET verification_status = ?, verified_snapshot_hash = NULL,
            parking_debt_check_mode = ?, updated_at = ?
        WHERE resident_account_id = ?
        """,
        (STATUS_PENDING_OPERATOR, MODE_MANUAL, now, resident_account_id),
    )
    _journal(
        cur,
        resident_account_id=resident_account_id,
        apartment_id=apartment_id,
        apartment_number=apartment_number,
        request_id=request_id,
        event_type="GENERAL_CORRECTION_REQUESTED",
        actor_id=actor_id,
        payload={"note": note},
    )
    return get_profile_request(request_id=request_id, conn=conn) or {}


def get_profile_request(
    *,
    request_id: int,
    conn: sqlite3.Connection,
) -> dict | None:
    cur = conn.cursor()
    row = _fetchone(
        cur,
        "SELECT * FROM resident_profile_change_requests WHERE id = ?",
        (int(request_id),),
    )
    if not row:
        return None
    row["current_value"] = _parse_json(row.get("current_value_json"), {})
    row["requested_value"] = _parse_json(row.get("requested_value_json"), {})
    return row


def list_pending_profile_requests(
    *,
    conn: sqlite3.Connection,
    limit: int = 100,
) -> list[dict]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT *
        FROM resident_profile_change_requests
        WHERE request_status = ?
        ORDER BY submitted_at ASC, id ASC
        LIMIT ?
        """,
        (REQUEST_PENDING, int(limit)),
    )
    result = []
    for row in cur.fetchall():
        item = dict(row)
        item["current_value"] = _parse_json(item.get("current_value_json"), {})
        item["requested_value"] = _parse_json(item.get("requested_value_json"), {})
        result.append(item)
    return result


def _apply_parking_time(
    cur: sqlite3.Cursor,
    *,
    apartment_id: int | None,
    apartment_number: str,
    vehicle_id: int,
    parking_time: str,
) -> None:
    if not table_exists(cur, "vehicles"):
        raise RuntimeError("Таблиця vehicles не знайдена.")
    cols = table_columns(cur, "vehicles")
    if "id" not in cols or "parking_time" not in cols:
        raise RuntimeError("Таблиця vehicles не має полів id та parking_time.")

    row = _fetchone(
        cur,
        f"SELECT * FROM vehicles WHERE id = ?",
        (int(vehicle_id),),
    )
    if not row:
        raise ValueError("Автомобіль не знайдено.")
    if apartment_id is not None and "apartment_id" in cols:
        if row.get("apartment_id") != apartment_id:
            raise ValueError("Автомобіль не належить до цієї квартири.")
    elif apartment_number and "apartment_number" in cols:
        if text(row.get("apartment_number")) != text(apartment_number):
            raise ValueError("Автомобіль не належить до цієї квартири.")

    cur.execute(
        "UPDATE vehicles SET parking_time = ? WHERE id = ?",
        (parking_time, int(vehicle_id)),
    )


def resolve_profile_request(
    *,
    request_id: int,
    approve: bool,
    actor_id: int | str | None,
    resolution_note: str = "",
    conn: sqlite3.Connection,
) -> dict:
    request = get_profile_request(request_id=int(request_id), conn=conn)
    if not request:
        raise ValueError("Заявку не знайдено.")
    if text(request.get("request_status")) != REQUEST_PENDING:
        raise ValueError("Ця заявка вже не очікує рішення.")

    cur = conn.cursor()
    now = now_db()
    request_type = text(request.get("request_type"))
    if approve and request_type == REQUEST_PARKING_TIME:
        requested = request.get("requested_value") or {}
        parking_time = _normalise_parking_time(requested.get("parking_time"))
        _apply_parking_time(
            cur,
            apartment_id=request.get("apartment_id"),
            apartment_number=text(request.get("apartment_number")),
            vehicle_id=int(request.get("vehicle_id")),
            parking_time=parking_time,
        )
        new_status = REQUEST_APPROVED
        event_type = "PARKING_TIME_CHANGE_APPROVED"
    elif approve and request_type == REQUEST_GENERAL_CORRECTION:
        # No free-text field is ever auto-applied. Existing controlled editor
        # remains the only place an operator changes core records.
        new_status = REQUEST_ACCEPTED_MANUAL
        event_type = "GENERAL_CORRECTION_ACCEPTED_MANUAL"
    else:
        new_status = REQUEST_REJECTED
        event_type = "PROFILE_CHANGE_REJECTED"

    cur.execute(
        """
        UPDATE resident_profile_change_requests
        SET request_status = ?, resolved_at = ?, resolved_by = ?,
            resolution_note = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            new_status,
            now,
            str(actor_id) if actor_id is not None else "operator",
            text(resolution_note),
            now,
            int(request_id),
        ),
    )
    cur.execute(
        """
        UPDATE resident_profile_verifications
        SET verification_status = ?, verified_snapshot_hash = NULL,
            parking_debt_check_mode = ?, updated_at = ?
        WHERE resident_account_id = ?
        """,
        (
            STATUS_NEEDS_REVIEW,
            MODE_MANUAL,
            now,
            int(request["resident_account_id"]),
        ),
    )
    _journal(
        cur,
        resident_account_id=int(request["resident_account_id"]),
        apartment_id=request.get("apartment_id"),
        apartment_number=text(request.get("apartment_number")),
        request_id=int(request_id),
        event_type=event_type,
        actor_id=actor_id,
        payload={"resolution_note": text(resolution_note), "new_status": new_status},
    )
    return get_profile_request(request_id=int(request_id), conn=conn) or {}


def profile_summary_for_display(snapshot: dict) -> dict:
    """Small pure-data summary useful in UI/tests."""
    return {
        "allowed": snapshot["phone_access_allowed"],
        "status": text(snapshot["profile"].get("verification_status")),
        "required_data_complete": bool(snapshot.get("required_data_complete")),
        "resident_confirmation_required": bool(
            snapshot.get("resident_confirmation_required")
        ),
        "profile_ready": bool(snapshot.get("profile_ready")),
        "critical_count": len(snapshot["critical"]),
        "structural_critical_count": len(snapshot.get("structural_critical") or []),
        "advisory_count": len(snapshot["advisory"]),
        "parking_debt_check_mode": snapshot["parking_debt_check_mode"],
        "private_access_phone_allowed": snapshot["private_access_phone_allowed"],
    }
