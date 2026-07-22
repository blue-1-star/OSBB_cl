"""
Миграция универсального реестра единиц дома и логических составных групп.

Почему есть ДВЕ сущности:
- apartments — физические единицы дома; 105 и 106 остаются двумя строками.
- unit_groups — логическая карточка для совместного обслуживания/поиска:
  105, 106, 105.106, 105_106 -> группа 105_106.

Группа НЕ утверждает, что квартиры юридически объединены. Её legal_status
по умолчанию UNKNOWN. Она лишь сохраняет уже действовавшую логику старого
бота, где составная запись Telegram-базы раскрывалась на несколько квартир.

Миграция:
- расширяет apartments полями unit_type и описанием единицы;
- все УЖЕ существующие apartments задаёт как RESIDENTIAL по происхождению;
- создаёт unit_groups, unit_group_members, unit_group_aliases;
- переносит только безопасные составные группы из quarantine tbot_parking_import,
  если все части существуют как отдельные apartments и нет пересечений;
- ничего не объединяет, не удаляет и не меняет payments/vehicles/contacts.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from pathlib import Path
import argparse
import re
import sqlite3
import sys

ROOT = Path(__file__).resolve().parent
PY_ROOT = ROOT.parent
for folder in (ROOT, PY_ROOT):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))

from config import paths, USE_TEST_DB

try:
    from audit_logger import audit_log
except Exception:
    audit_log = None


COMPOSITE_RE = re.compile(r"^\s*\d+\s*[._/,]\s*\d+(?:\s*[._/,]\s*\d+)*\s*$")

# Уже использовавшиеся в прежнем resolver исторические исправления.
SPECIAL_CASES = {
    "89.9": ("89_90", ("89", "90")),
    "89.90": ("89_90", ("89", "90")),
}


def now_db() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_main_db() -> Path:
    return paths.OSBB_TEST_DB_FILE if USE_TEST_DB else paths.OSBB_DB_FILE


def get_quarantine_db() -> Path:
    return paths.OSBB_QUARANTINE_DB_FILE


def quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    cur = conn.cursor()
    cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    )
    return cur.fetchone() is not None


def columns(cur: sqlite3.Cursor, table: str) -> list[str]:
    cur.execute(f"PRAGMA table_info({quote(table)})")
    return [row[1] for row in cur.fetchall()]


def add_column_if_missing(
    cur: sqlite3.Cursor,
    column_name: str,
    definition: str,
) -> bool:
    if column_name in columns(cur, "apartments"):
        return False
    cur.execute(f"ALTER TABLE apartments ADD COLUMN {quote(column_name)} {definition}")
    return True


def text(value) -> str:
    return "" if value is None else str(value).strip()


def is_composite(value) -> bool:
    raw = text(value)
    return raw in SPECIAL_CASES or bool(COMPOSITE_RE.fullmatch(raw))


def composite_info(value) -> tuple[str, tuple[str, ...]]:
    raw = text(value)
    if raw in SPECIAL_CASES:
        return SPECIAL_CASES[raw]

    parts = tuple(
        part.strip()
        for part in re.split(r"[._/,]", raw)
        if part.strip()
    )
    return "_".join(parts), parts


def normalize_alias(value) -> str:
    """Для поиска: 105.106 / 105-106 / 105_106 -> 105_106."""
    raw = text(value).upper().replace(" ", "")
    raw = raw.replace("-", "_").replace("/", "_").replace(",", "_").replace(".", "_")
    raw = re.sub(r"_+", "_", raw)
    return raw.strip("_")


def aliases_for_group(canonical: str, parts: tuple[str, ...], observed_forms: set[str]) -> list[tuple[str, str]]:
    """
    (alias_text, alias_kind).
    COMPONENT — ввод одного номера части должен вести в группу.
    COMPOSITE — все формы полной составной записи.
    """
    result: dict[str, str] = {}

    for part in parts:
        result[part] = "COMPONENT"

    for form in observed_forms:
        result[form] = "COMPOSITE"

    if len(parts) >= 2:
        for separator in ("_", ".", "-", "/"):
            result[separator.join(parts)] = "COMPOSITE"

    result[canonical] = "COMPOSITE"
    return sorted(result.items(), key=lambda pair: (normalize_alias(pair[0]), pair[0]))


def ensure_registry_columns(cur: sqlite3.Cursor) -> list[str]:
    """
    Важно: не определяем жилое помещение по внешнему виду apartment_number.
    Все существующие записи — это baseline жилого фонда.
    """
    definitions = {
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
    for column_name, definition in definitions.items():
        if add_column_if_missing(cur, column_name, definition):
            added.append(column_name)
    return added


def ensure_group_tables(cur: sqlite3.Cursor) -> None:
    cur.execute("""
        CREATE TABLE IF NOT EXISTS unit_groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_code TEXT NOT NULL UNIQUE,
            group_type TEXT NOT NULL DEFAULT 'COMPOSITE_LOOKUP',
            display_name TEXT,
            legal_status TEXT NOT NULL DEFAULT 'UNKNOWN',
            record_status TEXT NOT NULL DEFAULT 'LEGACY_LOOKUP',
            source_note TEXT,
            internal_note TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS unit_group_members (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id INTEGER NOT NULL,
            apartment_id INTEGER NOT NULL,
            member_order INTEGER NOT NULL DEFAULT 1,
            member_role TEXT NOT NULL DEFAULT 'MEMBER',
            source_note TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT,
            UNIQUE(group_id, apartment_id),
            FOREIGN KEY(group_id) REFERENCES unit_groups(id),
            FOREIGN KEY(apartment_id) REFERENCES apartments(id)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS unit_group_aliases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id INTEGER NOT NULL,
            alias_text TEXT NOT NULL,
            alias_normalized TEXT NOT NULL,
            alias_kind TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1,
            source_note TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT,
            UNIQUE(alias_normalized, is_active),
            FOREIGN KEY(group_id) REFERENCES unit_groups(id)
        )
    """)

    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_unit_groups_status
        ON unit_groups(record_status, legal_status)
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_unit_group_members_apartment
        ON unit_group_members(apartment_id)
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_unit_group_aliases_group
        ON unit_group_aliases(group_id)
    """)


def make_registry_views(cur: sqlite3.Cursor) -> None:
    cur.execute("DROP VIEW IF EXISTS v_unit_group_registry")
    cur.execute("""
        CREATE VIEW v_unit_group_registry AS
        SELECT
            g.id AS group_id,
            g.group_code,
            g.group_type,
            g.display_name,
            g.legal_status,
            g.record_status,
            (
                SELECT GROUP_CONCAT(a.apartment_number, ' + ')
                FROM unit_group_members gm
                JOIN apartments a ON a.id = gm.apartment_id
                WHERE gm.group_id = g.id
                ORDER BY gm.member_order
            ) AS members,
            (
                SELECT GROUP_CONCAT(ga.alias_text, ' | ')
                FROM unit_group_aliases ga
                WHERE ga.group_id = g.id
                  AND ga.is_active = 1
            ) AS aliases
        FROM unit_groups g
    """)


def seed_existing_residential(cur: sqlite3.Cursor) -> tuple[int, int]:
    """
    Все существующие records считаем жилыми baseline.
    Исключение — если оператор когда-либо уже вручную отметил COMMERCIAL/TECHNICAL.
    """
    cur.execute("""
        SELECT id, apartment_number, unit_type, unit_code, record_status
        FROM apartments
        ORDER BY id
    """)
    rows = cur.fetchall()

    residential_set = 0
    unit_codes_filled = 0

    for unit_id, apartment_number, unit_type, unit_code, record_status in rows:
        updates: dict[str, object] = {}

        if text(unit_type) not in {"COMMERCIAL", "TECHNICAL"}:
            if text(unit_type) != "RESIDENTIAL":
                updates["unit_type"] = "RESIDENTIAL"
                residential_set += 1

        if not text(unit_code):
            updates["unit_code"] = text(apartment_number)
            unit_codes_filled += 1

        if not text(record_status):
            updates["record_status"] = "LEGACY"

        if updates:
            updates["unit_updated_at"] = now_db()
            sets = ", ".join(f"{quote(name)} = ?" for name in updates)
            cur.execute(
                f"UPDATE apartments SET {sets} WHERE id = ?",
                (*updates.values(), unit_id),
            )

    cur.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_apartments_unit_code_unique
        ON apartments(unit_code)
        WHERE unit_code IS NOT NULL
          AND TRIM(unit_code) <> ''
    """)

    return residential_set, unit_codes_filled


def load_quarantine_composites(quarantine_db: Path) -> dict[str, dict]:
    """
    Возвращает candidate groups, обнаруженные в tbot_parking_import.
    """
    if not quarantine_db.exists():
        return {}

    conn = sqlite3.connect(quarantine_db)
    try:
        if not table_exists(conn, "tbot_parking_import"):
            return {}

        cur = conn.cursor()
        table_cols = columns(cur, "tbot_parking_import")
        apartment_col = next(
            (
                name for name in (
                    "apartment_number",
                    "Номер квартири",
                    "Номер квартиры",
                    "Квартира",
                )
                if name in table_cols
            ),
            None,
        )
        if not apartment_col:
            return {}

        id_col = "id" if "id" in table_cols else "rowid"
        cur.execute(f"""
            SELECT {quote(id_col)} AS source_id,
                   {quote(apartment_col)} AS apartment_ref
            FROM tbot_parking_import
            WHERE {quote(apartment_col)} IS NOT NULL
              AND TRIM(CAST({quote(apartment_col)} AS TEXT)) <> ''
        """)

        result: dict[str, dict] = {}
        for source_id, raw_value in cur.fetchall():
            raw = text(raw_value)
            if not is_composite(raw):
                continue

            canonical, parts = composite_info(raw)
            item = result.setdefault(canonical, {
                "canonical": canonical,
                "parts": parts,
                "raw_forms": set(),
                "source_ids": [],
            })
            item["raw_forms"].add(raw)
            item["source_ids"].append(int(source_id))

        return result
    finally:
        conn.close()


def load_main_apartment_map(cur: sqlite3.Cursor) -> dict[str, list[int]]:
    cur.execute("""
        SELECT id, apartment_number
        FROM apartments
        WHERE apartment_number IS NOT NULL
          AND TRIM(apartment_number) <> ''
    """)
    result: dict[str, list[int]] = defaultdict(list)
    for apartment_id, apartment_number in cur.fetchall():
        result[text(apartment_number)].append(int(apartment_id))
    return result


def assess_candidates(
    candidates: dict[str, dict],
    main_apartment_map: dict[str, list[int]],
) -> tuple[list[dict], list[dict]]:
    """
    approved — безопасно создать lookup-группу:
      каждая часть существует ровно один раз,
      часть не входит в другую candidate group.

    review — не угадываем.
    """
    part_to_groups: dict[str, set[str]] = defaultdict(set)
    for canonical, item in candidates.items():
        for part in item["parts"]:
            part_to_groups[part].add(canonical)

    approved: list[dict] = []
    review: list[dict] = []

    for canonical, item in sorted(candidates.items()):
        parts = item["parts"]
        missing = [part for part in parts if not main_apartment_map.get(part)]
        duplicates = [
            part for part in parts
            if len(main_apartment_map.get(part, [])) != 1
        ]
        overlap_parts = [
            part for part in parts
            if len(part_to_groups[part]) > 1
        ]

        record = {
            **item,
            "missing_parts": missing,
            "duplicate_parts": duplicates,
            "overlap_parts": overlap_parts,
            "apartment_ids": [
                main_apartment_map[part][0]
                for part in parts
                if len(main_apartment_map.get(part, [])) == 1
            ],
        }

        if missing or duplicates or overlap_parts:
            review.append(record)
        else:
            approved.append(record)

    return approved, review


def get_or_create_group(cur: sqlite3.Cursor, candidate: dict) -> tuple[int, bool]:
    canonical = candidate["canonical"]

    cur.execute(
        "SELECT id FROM unit_groups WHERE group_code = ?",
        (canonical,),
    )
    row = cur.fetchone()
    if row:
        group_id = int(row[0])
        cur.execute("""
            UPDATE unit_groups
            SET group_type = 'COMPOSITE_LOOKUP',
                display_name = ?,
                source_note = ?,
                updated_at = ?
            WHERE id = ?
        """, (
            f"Составная группа {canonical}",
            (
                "Создано из tbot_parking_import. "
                f"Исходные формы: {', '.join(sorted(candidate['raw_forms']))}. "
                "Группа предназначена для общего поиска/карточки и не подтверждает юридическое объединение."
            ),
            now_db(),
            group_id,
        ))
        return group_id, False

    cur.execute("""
        INSERT INTO unit_groups (
            group_code, group_type, display_name,
            legal_status, record_status,
            source_note, created_at, updated_at
        )
        VALUES (?, 'COMPOSITE_LOOKUP', ?, 'UNKNOWN', 'LEGACY_LOOKUP', ?, ?, ?)
    """, (
        canonical,
        f"Составная группа {canonical}",
        (
            "Создано из tbot_parking_import. "
            f"Исходные формы: {', '.join(sorted(candidate['raw_forms']))}. "
            "Группа предназначена для общего поиска/карточки и не подтверждает юридическое объединение."
        ),
        now_db(),
        now_db(),
    ))
    return int(cur.lastrowid), True


def ensure_member(
    cur: sqlite3.Cursor,
    group_id: int,
    apartment_id: int,
    member_order: int,
) -> bool:
    cur.execute("""
        SELECT 1 FROM unit_group_members
        WHERE group_id = ? AND apartment_id = ?
    """, (group_id, apartment_id))
    if cur.fetchone():
        return False

    cur.execute("""
        INSERT INTO unit_group_members (
            group_id, apartment_id, member_order,
            member_role, source_note, created_at, updated_at
        )
        VALUES (?, ?, ?, 'MEMBER', ?, ?, ?)
    """, (
        group_id,
        apartment_id,
        member_order,
        "Участник составной группы, полученной из tbot_parking_import.",
        now_db(),
        now_db(),
    ))
    return True


def ensure_group_alias(
    cur: sqlite3.Cursor,
    group_id: int,
    alias_text: str,
    alias_kind: str,
) -> tuple[bool, str]:
    normalized = normalize_alias(alias_text)
    if not normalized:
        return False, "empty"

    cur.execute("""
        SELECT group_id, alias_text
        FROM unit_group_aliases
        WHERE alias_normalized = ? AND is_active = 1
    """, (normalized,))
    row = cur.fetchone()

    if row:
        if int(row[0]) == group_id:
            return False, "exists"
        return False, f"conflict with group_id={row[0]} alias={row[1]!r}"

    cur.execute("""
        INSERT INTO unit_group_aliases (
            group_id, alias_text, alias_normalized,
            alias_kind, is_active, source_note, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, 1, ?, ?, ?)
    """, (
        group_id,
        alias_text,
        normalized,
        alias_kind,
        "Автоматически создано из подтверждённой legacy composite group.",
        now_db(),
        now_db(),
    ))
    return True, "inserted"


def write_report(
    report_file: Path,
    *,
    main_db: Path,
    quarantine_db: Path,
    apply: bool,
    columns_added: list[str],
    residential_set: int,
    unit_codes_filled: int,
    approved: list[dict],
    review: list[dict],
    results: dict | None,
) -> None:
    lines = [
        "=" * 120,
        "МИГРАЦИЯ РЕЕСТРА ЕДИНИЦ И ЛОГИЧЕСКИХ СОСТАВНЫХ ГРУПП",
        "=" * 120,
        f"Main DB: {main_db}",
        f"Quarantine DB: {quarantine_db}",
        f"MODE: {'TEST/WORK' if USE_TEST_DB else 'PROD'}",
        f"Apply: {apply}",
        "",
        "Ключевое правило:",
        "  physical apartments остаются отдельными строками.",
        "  unit_group — общая логическая карточка/поиск; это НЕ юридическое объединение.",
        "",
        "Расширение apartments:",
        f"  Добавленные поля: {', '.join(columns_added) if columns_added else 'нет'}",
        f"  RESIDENTIAL задано/подтверждено: {residential_set}",
        f"  unit_code заполнено: {unit_codes_filled}",
        "",
        "ГРУППЫ, ГОТОВЫЕ К СОЗДАНИЮ:",
    ]

    for item in approved:
        lines.append(
            f"  {item['canonical']} -> {' + '.join(item['parts'])}; "
            f"исходные формы: {', '.join(sorted(item['raw_forms']))}; "
            f"source ids: {', '.join(map(str, item['source_ids']))}"
        )

    if not approved:
        lines.append("  Нет.")

    lines.extend(["", "ГРУППЫ, ТРЕБУЮЩИЕ РУЧНОГО РЕШЕНИЯ:"])
    for item in review:
        details = []
        if item["missing_parts"]:
            details.append("нет частей: " + ", ".join(item["missing_parts"]))
        if item["duplicate_parts"]:
            details.append("дубли частей: " + ", ".join(item["duplicate_parts"]))
        if item["overlap_parts"]:
            details.append("пересечение частей: " + ", ".join(item["overlap_parts"]))
        lines.append(
            f"  {item['canonical']} -> {' + '.join(item['parts'])}; "
            + "; ".join(details)
        )

    if not review:
        lines.append("  Нет.")

    if results:
        lines.extend([
            "",
            "ПРИМЕНЕНО:",
            f"  групп создано: {results['groups_created']}",
            f"  групп обновлено: {results['groups_updated']}",
            f"  участников добавлено: {results['members_added']}",
            f"  алиасов добавлено: {results['aliases_added']}",
            f"  конфликтов алиасов: {len(results['alias_conflicts'])}",
        ])
        for conflict in results["alias_conflicts"]:
            lines.append("    - " + conflict)

    lines.extend([
        "",
        "Следующий шаг: подключить единый resolver к боту.",
        "Он должен возвращать unit_group и список физических member apartments,",
        "а не «первую попавшуюся квартиру» как старый compatibility-код.",
        "",
        "MIGRATION COMPLETED" if apply else "DRY RUN COMPLETED - NO CHANGES SAVED",
    ])

    report_file.parent.mkdir(parents=True, exist_ok=True)
    report_file.write_text("\n".join(lines), encoding="utf-8")


def migrate(apply: bool) -> None:
    main_db = get_main_db()
    quarantine_db = get_quarantine_db()

    if not main_db.exists():
        raise RuntimeError(f"Не найдена основная БД: {main_db}")

    conn = sqlite3.connect(main_db)
    cur = conn.cursor()

    if not table_exists(conn, "apartments"):
        raise RuntimeError("В основной БД отсутствует apartments.")

    print("=" * 120)
    print("UNIT REGISTRY + COMPOSITE GROUPS MIGRATION")
    print("=" * 120)
    print("DB:", main_db)
    print("MODE:", "TEST/WORK" if USE_TEST_DB else "PROD")
    print("Apply:", apply)
    print()

    columns_added = ensure_registry_columns(cur)
    ensure_group_tables(cur)
    residential_set, unit_codes_filled = seed_existing_residential(cur)
    make_registry_views(cur)

    candidates = load_quarantine_composites(quarantine_db)
    main_map = load_main_apartment_map(cur)
    approved, review = assess_candidates(candidates, main_map)

    report_file = (
        paths.OSBB_EXPORTS_DIR / "units" /
        f"unit_registry_composite_groups_{now_db().replace(':', '-').replace(' ', '_')}.txt"
    )

    results = None

    if apply:
        results = {
            "groups_created": 0,
            "groups_updated": 0,
            "members_added": 0,
            "aliases_added": 0,
            "alias_conflicts": [],
        }

        for candidate in approved:
            group_id, created = get_or_create_group(cur, candidate)
            if created:
                results["groups_created"] += 1
            else:
                results["groups_updated"] += 1

            for index, apartment_id in enumerate(candidate["apartment_ids"], start=1):
                if ensure_member(cur, group_id, apartment_id, index):
                    results["members_added"] += 1

            for alias_text, alias_kind in aliases_for_group(
                candidate["canonical"],
                candidate["parts"],
                candidate["raw_forms"],
            ):
                inserted, state = ensure_group_alias(
                    cur,
                    group_id,
                    alias_text,
                    alias_kind,
                )
                if inserted:
                    results["aliases_added"] += 1
                elif state.startswith("conflict"):
                    results["alias_conflicts"].append(
                        f"{candidate['canonical']}: {alias_text!r} -> {state}"
                    )

        if audit_log is not None:
            try:
                audit_log(
                    conn=conn,
                    actor_type="system",
                    operator_id="system",
                    user_id="system",
                    action_type="unit_registry_composite_groups_migration",
                    table_name="unit_groups",
                    row_id="",
                    field_name="group_code",
                    old_value="",
                    new_value="legacy composite lookup groups",
                    source_context="migrate_unit_registry_composite_groups.py",
                    comment=(
                        "Созданы логические группы составных обозначений. "
                        "Физические apartments не объединялись."
                    ),
                    extra={
                        "columns_added": columns_added,
                        "approved_groups": [item["canonical"] for item in approved],
                        "review_groups": [item["canonical"] for item in review],
                        **results,
                    },
                    commit=False,
                )
            except Exception as exc:
                print("WARNING: audit log not written:", exc)

        conn.commit()
    else:
        conn.rollback()

    write_report(
        report_file=report_file,
        main_db=main_db,
        quarantine_db=quarantine_db,
        apply=apply,
        columns_added=columns_added,
        residential_set=residential_set,
        unit_codes_filled=unit_codes_filled,
        approved=approved,
        review=review,
        results=results,
    )

    conn.close()

    print("Composite candidates:", len(candidates))
    print("Ready groups:", len(approved))
    print("Review groups:", len(review))
    print("Columns added:", ", ".join(columns_added) if columns_added else "none")
    print("RESIDENTIAL set/confirmed:", residential_set)
    print("unit_code filled:", unit_codes_filled)
    print("Report:", report_file)
    print()
    print("MIGRATION COMPLETED" if apply else "DRY RUN COMPLETED - NO CHANGES SAVED")
    print("=" * 120)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Create a universal unit registry and legacy composite lookup groups."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Применить. Без аргумента выполняется dry-run.",
    )
    args = parser.parse_args()
    migrate(apply=args.apply)
