# -*- coding: utf-8 -*-
"""
Read-only preflight for an operator-only resident-profile verification test.

Default target:
    apartment 40
    live-services sandbox database only

Safety:
- opens SQLite using URI mode=ro and PRAGMA query_only=ON;
- never imports profile_verification_core, because that module can create
  profile rows as part of normal resident flow;
- never sends Telegram messages;
- never creates a resident profile, greeting, request, order, payment,
  subscription or audit event.

The output answers only:
1. Does apartment 40 exist in the sandbox?
2. Which vehicle records are linked to it?
3. Which required parking fields are missing?
4. Is it suitable for the isolated operator sandbox test?
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
DEFAULT_DB = (
    ROOT / "Data" / "db" / "sandbox"
    / "osbb_test_live_services_2026-06-26_20-13-26.db"
)

UNIT_TABLE_PRIORITY = ("apartments", "units", "residential_units")
UNIT_NUMBER_COLUMNS = (
    "apartment_number",
    "unit_number",
    "number",
    "display_number",
    "legacy_number",
)
VEHICLE_PLATE_COLUMNS = (
    "license_plate_normalized",
    "license_plate",
    "plate_number",
    "plate",
    "number",
)
VEHICLE_MODEL_COLUMNS = (
    "car_model_normalized",
    "car_model",
    "make_model",
    "model",
    "vehicle_model",
)
VEHICLE_COLOR_COLUMNS = (
    "car_color_normalized",
    "car_color",
    "color",
    "colour",
    "vehicle_color",
)


def text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def quote(identifier: str) -> str:
    return '"' + str(identifier).replace('"', '""') + '"'


def tables(conn: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }


def columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {
        str(row[1])
        for row in conn.execute(f"PRAGMA table_info({quote(table)})").fetchall()
    }


def first_present(candidates: tuple[str, ...], available: set[str]) -> str | None:
    return next((name for name in candidates if name in available), None)


def normalise_apartment(value: str) -> str:
    value = text(value)
    if value.endswith(".0") and value[:-2].isdigit():
        return value[:-2]
    return value


def connect_read_only(db_path: Path) -> sqlite3.Connection:
    if not db_path.is_file():
        raise FileNotFoundError(f"Sandbox database not found:\n{db_path}")

    # Path.as_uri() yields a correct file:///G:/... URI on Windows.
    uri = db_path.resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    return conn


def find_unit_candidates(
    conn: sqlite3.Connection,
    apartment_number: str,
) -> list[dict]:
    result: list[dict] = []
    existing_tables = tables(conn)
    ordered_tables = [
        table for table in UNIT_TABLE_PRIORITY if table in existing_tables
    ]
    ordered_tables += sorted(
        table for table in existing_tables
        if table not in ordered_tables
        and ("apartment" in table.lower() or table.lower() == "units")
    )

    wanted = normalise_apartment(apartment_number)
    for table in ordered_tables:
        available = columns(conn, table)
        number_column = first_present(UNIT_NUMBER_COLUMNS, available)
        if not number_column:
            continue

        rows = conn.execute(
            f"""
            SELECT *
            FROM {quote(table)}
            WHERE CAST({quote(number_column)} AS TEXT) = ?
               OR CAST({quote(number_column)} AS TEXT) = ?
            """,
            (wanted, wanted + ".0"),
        ).fetchall()
        for row in rows:
            data = dict(row)
            result.append(
                {
                    "table": table,
                    "number_column": number_column,
                    "id": data.get("id"),
                    "apartment_number": normalise_apartment(
                        text(data.get(number_column))
                    ),
                    "row": data,
                }
            )
    return result


def choose_primary_unit(candidates: list[dict]) -> dict | None:
    if not candidates:
        return None
    # Prefer the table that is used by existing vehicle linkage.
    for preferred in UNIT_TABLE_PRIORITY:
        for candidate in candidates:
            if candidate["table"] == preferred and candidate.get("id") is not None:
                return candidate
    return candidates[0]


def vehicle_value(row: dict, names: tuple[str, ...]) -> str:
    for name in names:
        value = text(row.get(name))
        if value:
            return value
    return ""


def find_vehicles(
    conn: sqlite3.Connection,
    unit: dict,
) -> tuple[list[dict], list[str]]:
    warnings: list[str] = []
    if "vehicles" not in tables(conn):
        return [], ["Таблиця vehicles відсутня."]

    available = columns(conn, "vehicles")
    clauses: list[str] = []
    params: list[Any] = []

    if unit.get("id") is not None and "apartment_id" in available:
        clauses.append(f"{quote('apartment_id')} = ?")
        params.append(unit["id"])

    apartment_number = text(unit.get("apartment_number"))
    if apartment_number and "apartment_number" in available:
        clauses.append(f"CAST({quote('apartment_number')} AS TEXT) IN (?, ?)")
        params.extend([apartment_number, apartment_number + ".0"])

    if not clauses:
        return [], [
            "Не найдено поле связи vehicles.apartment_id или vehicles.apartment_number."
        ]

    rows = conn.execute(
        f"""
        SELECT *
        FROM {quote('vehicles')}
        WHERE {' OR '.join(clauses)}
        ORDER BY id ASC
        """,
        tuple(params),
    ).fetchall()

    seen: set[Any] = set()
    result: list[dict] = []
    for raw in rows:
        row = dict(raw)
        key = row.get("id")
        if key in seen:
            continue
        seen.add(key)
        result.append(
            {
                "id": row.get("id"),
                "plate": vehicle_value(row, VEHICLE_PLATE_COLUMNS),
                "model": vehicle_value(row, VEHICLE_MODEL_COLUMNS),
                "color": vehicle_value(row, VEHICLE_COLOR_COLUMNS),
                "parking_time": text(row.get("parking_time")),
            }
        )
    return result, warnings


def status_for(vehicles: list[dict]) -> tuple[str, int]:
    if not vehicles:
        return "NOT_SUITABLE_NO_VEHICLES", 2
    missing = [
        row for row in vehicles
        if not text(row.get("parking_time"))
    ]
    if missing:
        return "SUITABLE_MISSING_PARKING_TIME", 0
    return "NOT_SUITABLE_NO_MISSING_PARKING_TIME", 2


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read-only profile-verification test preflight."
    )
    parser.add_argument(
        "--apartment",
        default="40",
        help="Apartment number to inspect. Default: 40.",
    )
    parser.add_argument(
        "--db",
        default=str(DEFAULT_DB),
        help="Sandbox SQLite path. Default: designated live-services sandbox.",
    )
    args = parser.parse_args()

    target = normalise_apartment(args.apartment)
    db_path = Path(args.db).expanduser()

    print("OSBB profile-verification test preflight")
    print("Mode: READ ONLY — no data can be changed")
    print("Database:", db_path)
    print("Target apartment:", target)
    print()

    conn = connect_read_only(db_path)
    try:
        candidates = find_unit_candidates(conn, target)
        if not candidates:
            print("RESULT: APARTMENT NOT FOUND")
            print(
                "No resident/client flow was opened and no profile/audit record was created."
            )
            return 2

        print("Matching unit records:")
        for item in candidates:
            print(
                f" - {item['table']} | id={item.get('id')} | "
                f"{item['number_column']}={item['apartment_number']}"
            )

        unit = choose_primary_unit(candidates)
        assert unit is not None
        print()
        print(
            "Selected strictly for read-only test preview: "
            f"{unit['table']} id={unit.get('id')} apartment={unit['apartment_number']}"
        )

        vehicles, warnings = find_vehicles(conn, unit)
        print()
        print("Linked vehicles:")
        if not vehicles:
            print(" - none")
        for row in vehicles:
            parking_display = row["parking_time"] or "⛔ EMPTY"
            print(
                f" - id={row.get('id')} | plate={row['plate'] or '⛔ EMPTY'} | "
                f"model={row['model'] or '—'} | color={row['color'] or '—'} | "
                f"parking_time={parking_display}"
            )

        if warnings:
            print()
            print("Warnings:")
            for warning in warnings:
                print(" -", warning)

        status, exit_code = status_for(vehicles)
        print()
        print("Test suitability:", status)
        if status == "SUITABLE_MISSING_PARKING_TIME":
            print(
                "Apartment 40 is suitable for the next isolated operator sandbox test."
            )
            print(
                "That next test must use a TEST-only session; it must not create "
                "a resident profile, welcome event, resident request, or change "
                "vehicles.parking_time."
            )
        elif status == "NOT_SUITABLE_NO_VEHICLES":
            print(
                "Apartment 40 has no linked vehicle, so it cannot test missing "
                "parking_time. Choose another apartment with a vehicle."
            )
        else:
            print(
                "All linked vehicles already have parking_time, so this apartment "
                "cannot test the missing-parking-time branch."
            )

        print()
        print("READ-ONLY CONFIRMATION: no SQL write was attempted.")
        return exit_code
    finally:
        conn.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print("PREFLIGHT FAILED:", type(exc).__name__ + ":", exc)
        raise SystemExit(1)
