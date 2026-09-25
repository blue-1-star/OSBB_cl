"""Structured destinations named in unverified cash-handover claims.

KAS1/KAS2 and later KAS slots are named collectors, not cashier ledger codes.
Assignments are effective-dated so a historical claim resolves to its custodian.
"""

from __future__ import annotations

from datetime import date, timedelta
import sqlite3

from audit_logger import audit_log
from service_orders_core import get_conn


POINTS = (
    ("K1", "Консьерж 1-го подъезда", "CASHBOX"),
    ("K2", "Консьерж 2-го подъезда", "CASHBOX"),
    ("K3", "Консьерж 3-го подъезда", "CASHBOX"),
    ("K4", "Консьерж 4-го подъезда", "CASHBOX"),
    ("K5", "Консьерж 5-го подъезда", "CASHBOX"),
    ("K6", "Консьерж 6-го подъезда", "CASHBOX"),
    ("O", "Пост охраны", "CASHBOX"),
    ("KAS1", "Кассир 1", "COLLECTOR_SLOT"),
    ("KAS2", "Кассир 2", "COLLECTOR_SLOT"),
)


def ensure_claim_points_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS cash_claim_points (
            point_code TEXT PRIMARY KEY,
            point_name TEXT NOT NULL,
            point_kind TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS cash_claim_custodians (
            id INTEGER PRIMARY KEY,
            point_code TEXT NOT NULL,
            person_name TEXT NOT NULL,
            valid_from TEXT NOT NULL,
            valid_to TEXT,
            assigned_by TEXT NOT NULL,
            note TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(point_code) REFERENCES cash_claim_points(point_code),
            CHECK(valid_to IS NULL OR valid_to >= valid_from)
        )"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_cash_claim_custodians_point_dates "
        "ON cash_claim_custodians(point_code,valid_from,valid_to)"
    )
    if not any(row[1] == "telegram_user_id" for row in conn.execute("PRAGMA table_info(cash_claim_custodians)")):
        conn.execute("ALTER TABLE cash_claim_custodians ADD COLUMN telegram_user_id TEXT")
    for code, name, kind in POINTS:
        conn.execute(
            "INSERT OR IGNORE INTO cash_claim_points(point_code,point_name,point_kind) VALUES (?,?,?)",
            (code, name, kind),
        )


def create_collector_slot(*, point_name: str, created_by: str,
                          conn: sqlite3.Connection | None = None) -> dict:
    """Create another named collector slot without imposing a two-person limit."""
    point_name, created_by = point_name.strip(), created_by.strip()
    if not point_name or not created_by:
        raise ValueError("Нужны название роли/точки и имя создавшего оператора.")
    owns = conn is None
    conn = conn or get_conn()
    try:
        ensure_claim_points_schema(conn)
        conn.row_factory = sqlite3.Row
        used = {row[0] for row in conn.execute(
            "SELECT point_code FROM cash_claim_points WHERE point_kind='COLLECTOR_SLOT'"
        )}
        number = 1
        while f"KAS{number}" in used:
            number += 1
        code = f"KAS{number}"
        conn.execute(
            "INSERT INTO cash_claim_points(point_code,point_name,point_kind) VALUES (?,?,'COLLECTOR_SLOT')",
            (code, point_name),
        )
        audit_log(
            conn=conn, operator_id=created_by, user_id=created_by,
            actor_type="operator", action_type="cash_claim_collector_slot_created",
            table_name="cash_claim_points", row_id=code, field_name="point_name",
            old_value="", new_value=point_name, source_context="cash_claim_points_core",
            comment="Создана дополнительная точка уполномоченного сборщика; это не касса.",
            commit=False,
        )
        if owns:
            conn.commit()
        return {"point_code": code, "point_name": point_name}
    except Exception:
        if owns:
            conn.rollback()
        raise
    finally:
        if owns:
            conn.close()


def custodian_at(conn: sqlite3.Connection, point_code: str, as_of: str) -> dict | None:
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        """SELECT * FROM cash_claim_custodians WHERE point_code=?
           AND valid_from<=? AND (valid_to IS NULL OR valid_to>=?)
           ORDER BY valid_from DESC,id DESC LIMIT 1""",
        (point_code.upper(), as_of[:10], as_of[:10]),
    ).fetchone()
    return dict(row) if row else None


def bind_collector_telegram(*, point_code: str, telegram_user_id: str,
                            actor: str, conn: sqlite3.Connection | None = None) -> dict:
    """Bind the current effective-dated collector assignment to a Telegram identity."""
    point_code, telegram_user_id, actor = point_code.strip().upper(), telegram_user_id.strip(), actor.strip()
    if not actor or (telegram_user_id and not telegram_user_id.isdecimal()):
        raise ValueError("Нужны исполнитель и числовой Telegram ID сборщика (или пустое поле для снятия привязки).")
    owns = conn is None
    conn = conn or get_conn()
    try:
        if owns:
            conn.execute("BEGIN IMMEDIATE")
        ensure_claim_points_schema(conn)
        current = custodian_at(conn, point_code, date.today().isoformat())
        if not current:
            raise ValueError("Нет действующего назначения сборщика.")
        if telegram_user_id and conn.execute(
            """SELECT 1 FROM cash_claim_custodians WHERE telegram_user_id=? AND id<>?
               AND valid_from<=? AND (valid_to IS NULL OR valid_to>=?) LIMIT 1""",
            (telegram_user_id, current["id"], date.today().isoformat(), date.today().isoformat()),
        ).fetchone():
            raise ValueError("Этот Telegram ID уже привязан к другому действующему сборщику.")
        conn.execute("UPDATE cash_claim_custodians SET telegram_user_id=? WHERE id=?",
                     (telegram_user_id or None, current["id"]))
        audit_log(
            conn=conn, operator_id=actor, user_id=actor, actor_type="operator",
            action_type="cash_claim_collector_telegram_bound",
            table_name="cash_claim_custodians", row_id=current["id"],
            field_name="telegram_user_id", old_value=current.get("telegram_user_id") or "",
            new_value=telegram_user_id, source_context="cash_claim_points_core",
            comment="Привязка Telegram к назначению сборщика.", commit=False,
        )
        if owns:
            conn.commit()
        return {"point_code": point_code, "person_name": current["person_name"],
                "telegram_user_id": telegram_user_id}
    except Exception:
        if owns:
            conn.rollback()
        raise
    finally:
        if owns:
            conn.close()


def active_collector_for_telegram(telegram_user_id: int | str,
                                  conn: sqlite3.Connection | None = None) -> dict | None:
    owns = conn is None
    conn = conn or get_conn()
    try:
        conn.row_factory = sqlite3.Row
        if not any(row[1] == "telegram_user_id" for row in conn.execute("PRAGMA table_info(cash_claim_custodians)")):
            return None
        today = date.today().isoformat()
        row = conn.execute(
            """SELECT a.*,p.point_name FROM cash_claim_custodians a
               JOIN cash_claim_points p ON p.point_code=a.point_code AND p.is_active=1
               WHERE a.telegram_user_id=? AND a.valid_from<=?
                 AND (a.valid_to IS NULL OR a.valid_to>=?)
               ORDER BY a.id DESC LIMIT 1""", (str(telegram_user_id), today, today),
        ).fetchone()
        return dict(row) if row else None
    finally:
        if owns:
            conn.close()


def list_claim_points(as_of: str | None = None, *, conn: sqlite3.Connection | None = None,
                      include_historical_collectors: bool = False) -> list[dict]:
    owns = conn is None
    conn = conn or get_conn()
    try:
        conn.row_factory = sqlite3.Row
        day = (as_of or date.today().isoformat())[:10]
        result = []
        for row in conn.execute(
            "SELECT point_code,point_name,point_kind FROM cash_claim_points "
            "WHERE is_active=1 ORDER BY point_kind,point_code"
        ):
            point = dict(row)
            code = point["point_code"]
            if point["point_kind"] == "CASHBOX":
                cashbox = conn.execute(
                    "SELECT is_active FROM cashboxes WHERE cashbox_code=?", (code,)
                ).fetchone()
                if not cashbox or int(cashbox[0]) != 1:
                    continue
                point["custodian"] = None
            else:
                assignment = custodian_at(conn, code, day)
                if not assignment:
                    if not include_historical_collectors or not conn.execute(
                        "SELECT 1 FROM cash_claim_custodians WHERE point_code=? LIMIT 1", (code,)
                    ).fetchone():
                        continue
                    point["custodian"] = "ФИО определяется по дате сообщения"
                else:
                    point["custodian"] = assignment["person_name"]
            result.append(point)
        return result
    finally:
        if owns:
            conn.close()


def assign_collector(
    *, point_code: str, person_name: str, valid_from: str,
    assigned_by: str, note: str = "", conn: sqlite3.Connection | None = None,
) -> dict:
    point_code = point_code.strip().upper()
    person_name = person_name.strip()
    assigned_by = assigned_by.strip()
    start = date.fromisoformat(valid_from[:10])
    if not person_name or not assigned_by:
        raise ValueError("Нужны ФИО уполномоченного и имя назначившего оператора.")
    owns = conn is None
    conn = conn or get_conn()
    try:
        ensure_claim_points_schema(conn)
        conn.row_factory = sqlite3.Row
        point = conn.execute(
            "SELECT point_kind FROM cash_claim_points WHERE point_code=? AND is_active=1",
            (point_code,),
        ).fetchone()
        if not point or point["point_kind"] != "COLLECTOR_SLOT":
            raise ValueError("Выберите действующую ячейку уполномоченного инкассатора.")
        last = conn.execute(
            "SELECT * FROM cash_claim_custodians WHERE point_code=? ORDER BY valid_from DESC,id DESC LIMIT 1",
            (point_code,),
        ).fetchone()
        if last:
            prior_start = date.fromisoformat(last["valid_from"])
            if start <= prior_start:
                raise ValueError("Новая дата должна быть позже начала последнего назначения.")
            if last["valid_to"] is None:
                conn.execute(
                    "UPDATE cash_claim_custodians SET valid_to=? WHERE id=?",
                    ((start - timedelta(days=1)).isoformat(), int(last["id"])),
                )
            elif start <= date.fromisoformat(last["valid_to"]):
                raise ValueError("Дата пересекается с действующим историческим назначением.")
        cur = conn.execute(
            """INSERT INTO cash_claim_custodians
               (point_code,person_name,valid_from,assigned_by,note)
               VALUES (?,?,?,?,?)""",
            (point_code, person_name, start.isoformat(), assigned_by, note.strip() or None),
        )
        audit_log(
            conn=conn, operator_id=assigned_by, user_id=assigned_by,
            actor_type="operator", action_type="cash_claim_collector_assigned",
            table_name="cash_claim_custodians", row_id=cur.lastrowid,
            field_name="person_name,valid_from", old_value="",
            new_value=f"{point_code}:{person_name}:{start.isoformat()}",
            source_context="cash_claim_points_core",
            comment="Назначение уполномоченного для ячейки заявления о передаче денег; не кассовая проводка.",
            commit=False,
        )
        result = dict(conn.execute(
            "SELECT * FROM cash_claim_custodians WHERE id=?", (int(cur.lastrowid),)
        ).fetchone())
        if owns:
            conn.commit()
        return result
    except Exception:
        if owns:
            conn.rollback()
        raise
    finally:
        if owns:
            conn.close()


def end_collector_assignment(
    *, point_code: str, last_day: str, ended_by: str,
    conn: sqlite3.Connection | None = None,
) -> dict:
    """Leave a collector slot vacant after the specified last authorized day."""
    point_code = point_code.strip().upper()
    ended_by = ended_by.strip()
    end = date.fromisoformat(last_day[:10])
    if not ended_by:
        raise ValueError("Укажите, кто прекратил полномочия.")
    owns = conn is None
    conn = conn or get_conn()
    try:
        conn.row_factory = sqlite3.Row
        current = conn.execute(
            """SELECT * FROM cash_claim_custodians
               WHERE point_code=? AND valid_to IS NULL
               ORDER BY valid_from DESC,id DESC LIMIT 1""",
            (point_code,),
        ).fetchone()
        if current is None:
            raise ValueError("В этой ячейке нет открытого назначения.")
        if end < date.fromisoformat(current["valid_from"]):
            raise ValueError("Последний день не может быть раньше начала полномочий.")
        conn.execute(
            "UPDATE cash_claim_custodians SET valid_to=? WHERE id=? AND valid_to IS NULL",
            (end.isoformat(), int(current["id"])),
        )
        audit_log(
            conn=conn, operator_id=ended_by, user_id=ended_by,
            actor_type="operator", action_type="cash_claim_collector_ended",
            table_name="cash_claim_custodians", row_id=int(current["id"]),
            field_name="valid_to", old_value="", new_value=end.isoformat(),
            source_context="cash_claim_points_core",
            comment=f"Полномочия {current['person_name']} в {point_code} прекращены после указанной даты.",
            commit=False,
        )
        result = dict(conn.execute(
            "SELECT * FROM cash_claim_custodians WHERE id=?", (int(current["id"]),)
        ).fetchone())
        if owns:
            conn.commit()
        return result
    except Exception:
        if owns:
            conn.rollback()
        raise
    finally:
        if owns:
            conn.close()
