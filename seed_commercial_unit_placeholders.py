"""
Создание стартовых черновиков коммерческих помещений.

По умолчанию создаются:
    1_A, 3_A, 4_A, 5_A, 6_A

Помещение 2_A намеренно НЕ создаётся: по подъезду 2 пока нет подтверждённого
сведения о коммерческом помещении. При необходимости можно добавить его явно:
    --include-entrance-2

Скрипт:
- создаёт только недостающие черновики;
- не меняет существующие записи;
- помечает записи COMMERCIAL / DRAFT;
- создаёт таблицу unit_contacts для будущих контактов оператора;
- пишет аудит при --apply;
- без --apply выполняет dry-run и откатывает все изменения.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import argparse
import sqlite3
import sys

ROOT = Path(__file__).resolve().parent
for folder in (ROOT, ROOT.parent):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))

from config import paths, USE_TEST_DB
from audit_logger import audit_log


DEFAULT_ENTRANCES = (1, 3, 4, 5, 6)


def now_db() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_db_file() -> Path:
    return paths.OSBB_TEST_DB_FILE if USE_TEST_DB else paths.OSBB_DB_FILE


def q(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    cur = conn.cursor()
    cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    )
    return cur.fetchone() is not None


def table_info(cur: sqlite3.Cursor, table_name: str) -> list[sqlite3.Row | tuple]:
    cur.execute(f"PRAGMA table_info({q(table_name)})")
    return cur.fetchall()


def column_names(cur: sqlite3.Cursor, table_name: str) -> set[str]:
    return {row[1] for row in table_info(cur, table_name)}


def add_column_if_missing(
    cur: sqlite3.Cursor,
    table_name: str,
    column_name: str,
    definition: str,
) -> bool:
    if column_name in column_names(cur, table_name):
        return False
    cur.execute(f"ALTER TABLE {q(table_name)} ADD COLUMN {q(column_name)} {definition}")
    return True


def ensure_unit_fields(cur: sqlite3.Cursor) -> list[str]:
    fields = {
        "unit_type": "TEXT DEFAULT 'RESIDENTIAL'",
        "unit_code": "TEXT",
        "entrance_number": "INTEGER",
        "official_number": "TEXT",
        "display_name": "TEXT",
        "area_sqm": "REAL",
        "record_status": "TEXT DEFAULT 'LEGACY'",
        "source_note": "TEXT",
        "internal_note": "TEXT",
        "unit_updated_at": "TEXT",
    }
    added = []
    for field, definition in fields.items():
        if add_column_if_missing(cur, "apartments", field, definition):
            added.append(field)
    return added


def ensure_unit_contacts(cur: sqlite3.Cursor) -> None:
    cur.execute("""
        CREATE TABLE IF NOT EXISTS unit_contacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            apartment_id INTEGER NOT NULL,
            contact_name TEXT,
            contact_phone TEXT,
            contact_role TEXT NOT NULL DEFAULT 'PRIMARY',
            is_primary INTEGER NOT NULL DEFAULT 1,
            record_status TEXT NOT NULL DEFAULT 'DRAFT',
            note TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT,
            FOREIGN KEY(apartment_id) REFERENCES apartments(id)
        )
    """)
    cur.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_unit_contacts_primary
        ON unit_contacts(apartment_id)
        WHERE is_primary = 1
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_unit_contacts_apartment
        ON unit_contacts(apartment_id)
    """)


def find_existing_unit(cur: sqlite3.Cursor, unit_code: str):
    cols = column_names(cur, "apartments")
    where = ["apartment_number = ?"]
    params = [unit_code]
    if "unit_code" in cols:
        where.append("unit_code = ?")
        params.append(unit_code)

    cur.execute(f"""
        SELECT id, apartment_number,
               {"unit_code" if "unit_code" in cols else "NULL AS unit_code"},
               {"unit_type" if "unit_type" in cols else "NULL AS unit_type"},
               {"entrance_number" if "entrance_number" in cols else "NULL AS entrance_number"},
               {"record_status" if "record_status" in cols else "NULL AS record_status"}
        FROM apartments
        WHERE {" OR ".join(where)}
        ORDER BY id
    """, tuple(params))
    return cur.fetchall()


def check_required_columns(cur: sqlite3.Cursor, values: dict[str, object]) -> list[str]:
    unsupported = []
    for row in table_info(cur, "apartments"):
        # cid, name, type, notnull, default, pk
        _cid, name, _typ, notnull, default_value, pk = row[:6]
        if pk:
            continue
        if int(notnull or 0) and default_value is None and name not in values:
            unsupported.append(name)
    return unsupported


def build_insert_values(cur: sqlite3.Cursor, entrance: int, code: str) -> dict[str, object]:
    cols = column_names(cur, "apartments")
    timestamp = now_db()
    values: dict[str, object] = {
        "apartment_number": code,
    }

    optional = {
        "unit_code": code,
        "unit_type": "COMMERCIAL",
        "entrance_number": entrance,
        # Старое поле сохраняем для совместимости существующих запросов.
        "entrance": entrance,
        "display_name": None,
        "official_number": None,
        "area_sqm": None,
        "record_status": "DRAFT",
        "source_note": (
            "Автоматическая заготовка коммерческого помещения. "
            "Требуется обход и заполнение оператором."
        ),
        "internal_note": (
            "Создано программно как черновик. "
            "Уточнить фактическое назначение, название, контакты, номер и площадь."
        ),
        "unit_updated_at": timestamp,
        "created_at": timestamp,
        "updated_at": timestamp,
    }

    for key, value in optional.items():
        if key in cols:
            values[key] = value

    return values


def insert_unit(cur: sqlite3.Cursor, values: dict[str, object]) -> int:
    missing = check_required_columns(cur, values)
    if missing:
        raise RuntimeError(
            "В apartments есть обязательные поля без значения: "
            + ", ".join(missing)
        )

    fields = list(values)
    placeholders = ",".join("?" for _ in fields)
    cur.execute(
        f"INSERT INTO apartments ({', '.join(q(name) for name in fields)}) "
        f"VALUES ({placeholders})",
        tuple(values[name] for name in fields),
    )
    return int(cur.lastrowid)


def write_report(
    path: Path,
    *,
    db_file: Path,
    apply: bool,
    added_fields: list[str],
    requested: list[tuple[int, str]],
    created: list[tuple[int, str, int]],
    existing: list[tuple[int, str, list]],
    conflicts: list[str],
) -> None:
    lines = [
        "=" * 104,
        "СОЗДАНИЕ ЧЕРНОВИКОВ КОММЕРЧЕСКИХ ПОМЕЩЕНИЙ",
        "=" * 104,
        f"DB: {db_file}",
        f"MODE: {'TEST/WORK' if USE_TEST_DB else 'PROD'}",
        f"Apply: {apply}",
        "",
        "Запрошенные заготовки:",
        "  " + ", ".join(code for _entrance, code in requested),
        "",
        "Не создаётся по умолчанию: 2_A (нет подтверждённых сведений по подъезду 2).",
        "",
        "Правила:",
        "  unit_type = COMMERCIAL",
        "  record_status = DRAFT",
        "  название, контакты, официальный номер и площадь пока не заданы.",
        "  запись создаётся в apartments для совместимости всей текущей системы.",
        "",
        "Добавленные поля apartments: " + (", ".join(added_fields) if added_fields else "нет"),
        "",
        "СОЗДАНО:",
    ]

    if created:
        for entrance, code, unit_id in created:
            lines.append(f"  {code} | подъезд {entrance} | unit_id={unit_id}")
    else:
        lines.append("  Нет.")

    lines.extend(["", "УЖЕ СУЩЕСТВУЕТ:"])
    if existing:
        for entrance, code, rows in existing:
            detail = "; ".join(
                f"id={row[0]}, apt={row[1]!r}, unit_code={row[2]!r}, "
                f"type={row[3]!r}, entrance={row[4]!r}, status={row[5]!r}"
                for row in rows
            )
            lines.append(f"  {code} | подъезд {entrance}: {detail}")
    else:
        lines.append("  Нет.")

    lines.extend(["", "КОНФЛИКТЫ / ОШИБКИ:"])
    lines.extend([f"  {item}" for item in conflicts] or ["  Нет."])
    lines.append("")
    lines.append("APPLIED" if apply else "DRY RUN COMPLETED - NO CHANGES SAVED")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Создать черновики коммерческих помещений 1_A, 3_A, 4_A, 5_A, 6_A."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Применить изменения. Без ключа — только dry-run.",
    )
    parser.add_argument(
        "--include-entrance-2",
        action="store_true",
        help="Дополнительно создать 2_A. По умолчанию 2_A не создаётся.",
    )
    args = parser.parse_args()

    db_file = get_db_file()
    if not db_file.exists():
        raise SystemExit(f"Не найдена БД: {db_file}")

    entrances = list(DEFAULT_ENTRANCES)
    if args.include_entrance_2:
        entrances.insert(1, 2)

    requested = [(entrance, f"{entrance}_A") for entrance in entrances]

    conn = sqlite3.connect(db_file)
    cur = conn.cursor()

    if not table_exists(conn, "apartments"):
        raise SystemExit("Таблица apartments не найдена.")

    added_fields = ensure_unit_fields(cur)
    ensure_unit_contacts(cur)

    created: list[tuple[int, str, int]] = []
    existing: list[tuple[int, str, list]] = []
    conflicts: list[str] = []

    for entrance, code in requested:
        rows = find_existing_unit(cur, code)
        if rows:
            existing.append((entrance, code, rows))
            continue

        try:
            values = build_insert_values(cur, entrance, code)
            unit_id = insert_unit(cur, values)
            created.append((entrance, code, unit_id))

            audit_log(
                conn=conn,
                operator_id="system",
                user_id="system",
                actor_type="system",
                action_type="commercial_unit_placeholder_created",
                table_name="apartments",
                row_id=unit_id,
                field_name="unit_code,unit_type,record_status",
                old_value="",
                new_value=f"{code}, COMMERCIAL, DRAFT",
                source_context="seed_commercial_unit_placeholders.py",
                comment=(
                    "Создана автоматическая заготовка коммерческого помещения "
                    "для дальнейшего заполнения оператором."
                ),
                extra={"entrance_number": entrance, "unit_code": code},
                commit=False,
            )
        except Exception as exc:
            conflicts.append(f"{code}: {exc}")

    report = (
        paths.OSBB_EXPORTS_DIR / "units" /
        f"commercial_unit_placeholders_{now_db().replace(':', '-').replace(' ', '_')}.txt"
    )

    if args.apply and not conflicts:
        conn.commit()
    else:
        conn.rollback()

    write_report(
        report,
        db_file=db_file,
        apply=bool(args.apply and not conflicts),
        added_fields=added_fields,
        requested=requested,
        created=created,
        existing=existing,
        conflicts=conflicts,
    )
    conn.close()

    print("=" * 104)
    print("COMMERCIAL UNIT PLACEHOLDERS")
    print("=" * 104)
    print("DB:", db_file)
    print("MODE:", "TEST/WORK" if USE_TEST_DB else "PROD")
    print("Apply:", args.apply)
    print("Requested:", ", ".join(code for _entrance, code in requested))
    print("Created:", len(created))
    print("Already existing:", len(existing))
    print("Conflicts:", len(conflicts))
    print("Report:", report)
    print()
    if conflicts:
        print("NOT APPLIED — имеются конфликты/ошибки.")
    elif args.apply:
        print("APPLIED")
    else:
        print("DRY RUN COMPLETED - NO CHANGES SAVED")


if __name__ == "__main__":
    main()
