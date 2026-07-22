"""
Единый resolver единиц дома.

Модуль только читает БД.

Назначение:
- отдельная квартира/помещение: 203, 4A, 4T1;
- составная группа: 31, 32, 31.32, 31_32, 31-32, 31/32
  должны привести к группе 31_32 и её физическим участникам.

Группа unit_groups является общей карточкой поиска и ведения.
Она не означает юридического объединения физических квартир.

Примеры запуска:
  g:/Programming/Py/venv/Scripts/python.exe G:/Programming/Py/OSBB/unit_resolver.py 31 31.32 4A
  g:/Programming/Py/venv/Scripts/python.exe G:/Programming/Py/OSBB/unit_resolver.py --self-test
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
import argparse
import json
import re
import sqlite3
import sys
from typing import Any, Literal

ROOT = Path(__file__).resolve().parent
for folder in (ROOT, ROOT.parent):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))

from config import paths, USE_TEST_DB

ResolutionKind = Literal["GROUP", "UNIT", "AMBIGUOUS", "NOT_FOUND"]


@dataclass(frozen=True)
class UnitMember:
    """Физическая единица в apartments."""

    apartment_id: int
    apartment_number: str
    unit_code: str | None = None
    unit_type: str | None = None
    entrance_number: int | None = None
    display_name: str | None = None
    official_number: str | None = None
    record_status: str | None = None


@dataclass
class UnitResolution:
    """
    Итог поиска.

    GROUP     — логическая составная группа.
    UNIT      — одна отдельная физическая единица.
    AMBIGUOUS — требуется ручной выбор.
    NOT_FOUND — ничего не найдено.
    """

    query: str
    normalized_query: str
    kind: ResolutionKind
    matched_by: str | None = None
    group_id: int | None = None
    group_code: str | None = None
    group_type: str | None = None
    group_display_name: str | None = None
    legal_status: str | None = None
    group_record_status: str | None = None
    members: list[UnitMember] = field(default_factory=list)
    message: str | None = None

    @property
    def found(self) -> bool:
        return self.kind in {"GROUP", "UNIT"}

    @property
    def is_group(self) -> bool:
        return self.kind == "GROUP"

    @property
    def is_unit(self) -> bool:
        return self.kind == "UNIT"

    @property
    def member_apartment_ids(self) -> list[int]:
        return [member.apartment_id for member in self.members]

    @property
    def member_apartment_numbers(self) -> list[str]:
        return [member.apartment_number for member in self.members]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def get_db_file() -> Path:
    return paths.OSBB_TEST_DB_FILE if USE_TEST_DB else paths.OSBB_DB_FILE


def q(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    cur = conn.cursor()
    cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    )
    return cur.fetchone() is not None


def table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    cur = conn.cursor()
    cur.execute(f"PRAGMA table_info({q(table_name)})")
    return {row[1] for row in cur.fetchall()}


def normalize_unit_reference(value: Any) -> str:
    """
    Нормализация для сопоставления с unit_group_aliases.alias_normalized.

    31.32 / 31_32 / 31-32 / 31/32 -> 31_32.
    Одиночные коды вроде 4A и 4T1 сохраняются.
    """
    raw = text(value).upper().replace(" ", "")
    raw = raw.replace("-", "_").replace("/", "_").replace(",", "_").replace(".", "_")

    # Позволяет ввести 4А кириллицей вместо 4A латиницей.
    raw = raw.translate(str.maketrans({
        "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M",
        "Н": "H", "О": "O", "Р": "P", "С": "C", "Т": "T",
        "Х": "X", "І": "I",
    }))

    raw = re.sub(r"_+", "_", raw)
    return raw.strip("_")


def _apartment_select(columns: set[str]) -> list[str]:
    def field(name: str, alias: str) -> str:
        if name in columns:
            return f'a.{q(name)} AS {q(alias)}'
        return f'NULL AS {q(alias)}'

    return [
        'a."id" AS "apartment_id"',
        'a."apartment_number" AS "apartment_number"',
        field("unit_code", "unit_code"),
        field("unit_type", "unit_type"),
        field("entrance_number", "entrance_number"),
        field("display_name", "display_name"),
        field("official_number", "official_number"),
        field("record_status", "record_status"),
    ]


def _member_from_row(row: sqlite3.Row) -> UnitMember:
    entrance = row["entrance_number"]
    return UnitMember(
        apartment_id=int(row["apartment_id"]),
        apartment_number=text(row["apartment_number"]),
        unit_code=text(row["unit_code"]) or None,
        unit_type=text(row["unit_type"]) or None,
        entrance_number=int(entrance) if entrance not in (None, "") else None,
        display_name=text(row["display_name"]) or None,
        official_number=text(row["official_number"]) or None,
        record_status=text(row["record_status"]) or None,
    )


def _load_group_members(conn: sqlite3.Connection, group_id: int) -> list[UnitMember]:
    columns = table_columns(conn, "apartments")
    cur = conn.cursor()
    cur.execute(f"""
        SELECT {", ".join(_apartment_select(columns))}
        FROM unit_group_members gm
        JOIN apartments a ON a.id = gm.apartment_id
        WHERE gm.group_id = ?
        ORDER BY gm.member_order, a.id
    """, (group_id,))
    return [_member_from_row(row) for row in cur.fetchall()]


def _resolve_group_alias(
    conn: sqlite3.Connection,
    query: str,
    normalized: str,
) -> UnitResolution | None:
    """
    Групповые алиасы имеют приоритет над прямой квартирой:
      31 -> группа 31_32, а не только physical apartment 31.
    """
    needed = {"unit_groups", "unit_group_aliases", "unit_group_members"}
    if not all(table_exists(conn, table) for table in needed):
        return None

    cur = conn.cursor()
    cur.execute("""
        SELECT
            g.id AS group_id,
            g.group_code,
            g.group_type,
            g.display_name,
            g.legal_status,
            g.record_status,
            uga.alias_kind
        FROM unit_group_aliases uga
        JOIN unit_groups g ON g.id = uga.group_id
        WHERE uga.alias_normalized = ?
          AND uga.is_active = 1
        ORDER BY g.id
    """, (normalized,))
    rows = cur.fetchall()

    if not rows:
        return None

    group_ids = {int(row["group_id"]) for row in rows}
    if len(group_ids) > 1:
        return UnitResolution(
            query=query,
            normalized_query=normalized,
            kind="AMBIGUOUS",
            matched_by="group_alias_conflict",
            message=(
                "Один поисковый код связан с несколькими логическими группами. "
                "Автоматический выбор запрещён."
            ),
        )

    row = rows[0]
    group_id = int(row["group_id"])
    return UnitResolution(
        query=query,
        normalized_query=normalized,
        kind="GROUP",
        matched_by=f'group_alias:{text(row["alias_kind"]) or "UNKNOWN"}',
        group_id=group_id,
        group_code=text(row["group_code"]) or None,
        group_type=text(row["group_type"]) or None,
        group_display_name=text(row["display_name"]) or None,
        legal_status=text(row["legal_status"]) or None,
        group_record_status=text(row["record_status"]) or None,
        members=_load_group_members(conn, group_id),
        message=(
            "Найдена логическая составная группа. "
            "Физические квартиры/помещения остаются отдельными записями."
        ),
    )


def _resolve_direct_unit(
    conn: sqlite3.Connection,
    query: str,
    normalized: str,
) -> list[UnitMember]:
    """Поиск отдельной physical unit, когда alias группы не найден."""
    if not table_exists(conn, "apartments"):
        return []

    columns = table_columns(conn, "apartments")
    where = [
        "TRIM(CAST(a.apartment_number AS TEXT)) = ?",
        "UPPER(TRIM(CAST(a.apartment_number AS TEXT))) = ?",
    ]
    params: list[str] = [query, normalized]

    if "unit_code" in columns:
        where.extend([
            "TRIM(CAST(a.unit_code AS TEXT)) = ?",
            "UPPER(TRIM(CAST(a.unit_code AS TEXT))) = ?",
        ])
        params.extend([query, normalized])

    cur = conn.cursor()
    cur.execute(f"""
        SELECT {", ".join(_apartment_select(columns))}
        FROM apartments a
        WHERE {" OR ".join(where)}
        ORDER BY a.id
    """, params)

    # Одна строка может соответствовать нескольким where-условиям.
    unique: dict[int, UnitMember] = {}
    for row in cur.fetchall():
        member = _member_from_row(row)
        unique[member.apartment_id] = member
    return list(unique.values())


def resolve_unit_ref(
    conn: sqlite3.Connection,
    unit_ref: Any,
) -> UnitResolution:
    """
    Главная функция для всех обработчиков бота и служебных модулей.

    Возвращает только безопасный результат. При неоднозначности — AMBIGUOUS.
    """
    query = text(unit_ref)
    normalized = normalize_unit_reference(query)

    if not query or not normalized:
        return UnitResolution(
            query=query,
            normalized_query=normalized,
            kind="NOT_FOUND",
            message="Пустое обозначение единицы.",
        )

    group = _resolve_group_alias(conn, query, normalized)
    if group is not None:
        return group

    members = _resolve_direct_unit(conn, query, normalized)
    if not members:
        return UnitResolution(
            query=query,
            normalized_query=normalized,
            kind="NOT_FOUND",
            message="Единица с таким обозначением не найдена.",
        )

    if len(members) > 1:
        return UnitResolution(
            query=query,
            normalized_query=normalized,
            kind="AMBIGUOUS",
            matched_by="direct_unit_multiple",
            members=members,
            message=(
                "Найдено несколько физических единиц с одинаковым обозначением. "
                "Оператор должен выбрать запись явно."
            ),
        )

    return UnitResolution(
        query=query,
        normalized_query=normalized,
        kind="UNIT",
        matched_by="direct_unit",
        members=members,
        message="Найдена отдельная физическая единица.",
    )


def resolve_unit_ids(conn: sqlite3.Connection, unit_ref: Any) -> list[int]:
    """
    Для группы возвращает все physical apartment id.
    Для обычной единицы — один id.
    Для ошибки/неоднозначности — пустой список.
    """
    result = resolve_unit_ref(conn, unit_ref)
    return result.member_apartment_ids if result.found else []


def resolve_unit_numbers(conn: sqlite3.Connection, unit_ref: Any) -> list[str]:
    result = resolve_unit_ref(conn, unit_ref)
    return result.member_apartment_numbers if result.found else []


def resolve_unit_ref_from_db(
    unit_ref: Any,
    db_path: str | Path | None = None,
) -> UnitResolution:
    """Обёртка для CLI и небольших утилит."""
    db = Path(db_path) if db_path else get_db_file()
    if not db.exists():
        return UnitResolution(
            query=text(unit_ref),
            normalized_query=normalize_unit_reference(unit_ref),
            kind="NOT_FOUND",
            message=f"Не найдена БД: {db}",
        )

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        return resolve_unit_ref(conn, unit_ref)
    finally:
        conn.close()


def format_resolution(result: UnitResolution) -> str:
    """Удобный текстовый вывод для консоли и будущего Telegram UI."""
    lines = [
        f"Запрос: {result.query!r}",
        f"Нормализовано: {result.normalized_query!r}",
        f"Статус: {result.kind}",
    ]

    if result.group_code:
        lines.extend([
            f"Группа: {result.group_code}",
            f"Юридический статус: {result.legal_status or '-'}",
            "Группа используется для совместного поиска и карточки, "
            "но не подтверждает юридическое объединение.",
        ])

    if result.matched_by:
        lines.append(f"Найдено через: {result.matched_by}")

    if result.members:
        lines.append("Физические единицы:")
        for member in result.members:
            details = []
            if member.unit_type:
                details.append(member.unit_type)
            if member.entrance_number is not None:
                details.append(f"подъезд {member.entrance_number}")
            name = member.apartment_number
            if member.display_name:
                name += f" — {member.display_name}"
            lines.append(
                f"  - id={member.apartment_id} | {name}"
                + (f" | {', '.join(details)}" if details else "")
            )

    if result.message:
        lines.append(f"Сообщение: {result.message}")

    return "\n".join(lines)


def run_self_test(db_path: str | Path | None = None) -> int:
    """
    Проверяет все действующие group aliases:
      alias -> нужная группа -> минимум два physical member.
    """
    db = Path(db_path) if db_path else get_db_file()
    if not db.exists():
        print("ERROR: DB not found:", db)
        return 2

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        if not (
            table_exists(conn, "unit_groups")
            and table_exists(conn, "unit_group_aliases")
        ):
            print("Группы ещё не созданы. Сначала примените миграцию составных групп.")
            return 1

        cur = conn.cursor()
        cur.execute("""
            SELECT uga.alias_text, ug.group_code
            FROM unit_group_aliases uga
            JOIN unit_groups ug ON ug.id = uga.group_id
            WHERE uga.is_active = 1
            ORDER BY ug.group_code, uga.alias_text
        """)
        expected = cur.fetchall()

        print("=" * 96)
        print("UNIT RESOLVER SELF TEST")
        print("=" * 96)
        print("DB:", db)
        print("Active aliases:", len(expected))
        print()

        passed = 0
        failed = 0
        for row in expected:
            alias_text = text(row["alias_text"])
            expected_group = text(row["group_code"])
            result = resolve_unit_ref(conn, alias_text)

            ok = (
                result.kind == "GROUP"
                and result.group_code == expected_group
                and len(result.members) >= 2
            )
            state = "PASS" if ok else "FAIL"
            print(
                f"{state}: {alias_text!r} -> {result.group_code or '-'} "
                f"| physical: {', '.join(result.member_apartment_numbers) or '-'}"
            )
            if ok:
                passed += 1
            else:
                failed += 1

        print()
        print("Passed:", passed)
        print("Failed:", failed)
        print("=" * 96)
        return 0 if failed == 0 else 1
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Единый resolver квартир, помещений и составных групп."
    )
    parser.add_argument(
        "references",
        nargs="*",
        help="Примеры: 31 31.32 31_32 4A",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="Путь к БД. По умолчанию TEST/PROD из config.py.",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Проверить все активные алиасы групп.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Показать результат в JSON.",
    )
    args = parser.parse_args()

    if args.self_test:
        raise SystemExit(run_self_test(args.db))

    if not args.references:
        parser.print_help()
        raise SystemExit(0)

    for index, ref in enumerate(args.references):
        result = resolve_unit_ref_from_db(ref, args.db)
        if args.json:
            print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        else:
            print(format_resolution(result))

        if index < len(args.references) - 1:
            print("-" * 96)


if __name__ == "__main__":
    main()
