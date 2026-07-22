# ==========================================================
# generate_parking_charges.py
#
# Current stable version
# Updated: 2026-06-18
# ==========================================================
from pathlib import Path
import sys
import sqlite3
import argparse
from datetime import datetime

OSBB_ROOT = Path(__file__).resolve().parent
PY_ROOT = OSBB_ROOT.parent

if str(PY_ROOT) not in sys.path:
    sys.path.insert(0, str(PY_ROOT))

from config import paths, USE_TEST_DB


DEFAULT_PERIOD_CODE = "2026-05_2026-06"
DEFAULT_MONTHS = 2
DEFAULT_VALID_ON = "2026-05-01"


def get_db_file():
    return paths.OSBB_TEST_DB_FILE if USE_TEST_DB else paths.OSBB_DB_FILE


def now_ts():
    return datetime.now().strftime("%Y-%m-%d_%H-%M-%S")


def norm_text(value):
    if value is None:
        return ""
    return str(value).strip()


def table_columns(cur, table_name):
    cur.execute(f"PRAGMA table_info({table_name})")
    return [row[1] for row in cur.fetchall()]


def pick_status_column(cur, table_name):
    columns = table_columns(cur, table_name)

    if "charge_status" in columns:
        return "charge_status"

    if "status" in columns:
        return "status"

    return None


def get_active_tariff(cur, service_code, valid_on):
    cur.execute("""
        SELECT
            amount,
            currency,
            valid_from,
            valid_to
        FROM service_tariffs
        WHERE service_code = ?
          AND is_active = 1
          AND valid_from <= ?
          AND (valid_to IS NULL OR valid_to >= ?)
        ORDER BY valid_from DESC, id DESC
        LIMIT 1
    """, (service_code, valid_on, valid_on))

    return cur.fetchone()


def parking_time_to_service_code(parking_time):
    if parking_time == "Day":
        return "PARKING_DAY"

    if parking_time == "Night":
        return "PARKING_NIGHT"

    return None


def vehicle_sort_key(row):
    apt = norm_text(row["apartment_number"])
    if apt.isdigit():
        return (0, int(apt), row["vehicle_id"])
    return (1, apt, row["vehicle_id"])


def load_vehicles(conn):
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("""
        SELECT
            v.id AS vehicle_id,
            a.apartment_number,
            COALESCE(v.license_plate_normalized, v.license_plate) AS plate,
            COALESCE(v.car_model_normalized, v.car_model) AS model,
            v.parking_time
        FROM vehicles v
        JOIN apartments a
            ON a.id = v.apartment_id
        ORDER BY
            CASE
                WHEN a.apartment_number GLOB '[0-9]*'
                THEN CAST(a.apartment_number AS INTEGER)
                ELSE 999999
            END,
            a.apartment_number,
            v.id
    """)

    return [dict(row) for row in cur.fetchall()]


def charge_already_exists(cur, period_code, vehicle_id, service_code):
    status_col = pick_status_column(cur, "charges")

    if status_col:
        cur.execute(f"""
            SELECT id
            FROM charges
            WHERE period_code = ?
              AND vehicle_id = ?
              AND service_code = ?
              AND COALESCE({status_col}, '') <> 'cancelled'
            LIMIT 1
        """, (period_code, vehicle_id, service_code))
    else:
        cur.execute("""
            SELECT id
            FROM charges
            WHERE period_code = ?
              AND vehicle_id = ?
              AND service_code = ?
            LIMIT 1
        """, (period_code, vehicle_id, service_code))

    row = cur.fetchone()
    return row[0] if row else None


def build_charges(conn, period_code, months, valid_on):
    cur = conn.cursor()
    vehicles = load_vehicles(conn)

    charges = []
    skipped = []
    existing = []
    missing_tariffs = []

    tariff_cache = {}

    for vehicle in sorted(vehicles, key=vehicle_sort_key):
        vehicle_id = vehicle["vehicle_id"]
        apt = norm_text(vehicle["apartment_number"])
        plate = norm_text(vehicle["plate"]) or "-"
        model = norm_text(vehicle["model"]) or "-"
        parking_time = norm_text(vehicle["parking_time"])

        if parking_time == "Inactive":
            skipped.append({
                "reason": "Inactive",
                "vehicle_id": vehicle_id,
                "apartment_number": apt,
                "plate": plate,
                "model": model,
                "parking_time": parking_time or "NULL",
            })
            continue

        service_code = parking_time_to_service_code(parking_time)

        if not service_code:
            skipped.append({
                "reason": "parking_time=NULL/unknown",
                "vehicle_id": vehicle_id,
                "apartment_number": apt,
                "plate": plate,
                "model": model,
                "parking_time": parking_time or "NULL",
            })
            continue

        if service_code not in tariff_cache:
            tariff_cache[service_code] = get_active_tariff(cur, service_code, valid_on)

        tariff = tariff_cache[service_code]

        if not tariff:
            missing_tariffs.append({
                "service_code": service_code,
                "vehicle_id": vehicle_id,
                "apartment_number": apt,
                "plate": plate,
                "model": model,
                "parking_time": parking_time,
            })
            continue

        unit_price, currency, valid_from, valid_to = tariff
        amount = float(unit_price) * float(months)

        existing_id = charge_already_exists(cur, period_code, vehicle_id, service_code)

        charge = {
            "period_code": period_code,
            "vehicle_id": vehicle_id,
            "apartment_number": apt,
            "plate": plate,
            "model": model,
            "parking_time": parking_time,
            "service_code": service_code,
            "quantity": float(months),
            "unit": "month",
            "unit_price": float(unit_price),
            "amount": amount,
            "currency": currency,
            "tariff_valid_from": valid_from,
            "tariff_valid_to": valid_to,
            "existing_charge_id": existing_id,
        }

        if existing_id:
            existing.append(charge)
        else:
            charges.append(charge)

    return charges, skipped, existing, missing_tariffs


def format_money(value):
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.2f}"


def write_report(report_file, charges, skipped, existing, missing_tariffs, period_code, months, valid_on, db_file, apply_mode):
    lines = []

    lines.append("=" * 100)
    lines.append("PARKING CHARGES DRY RUN" if not apply_mode else "PARKING CHARGES APPLY REPORT")
    lines.append("=" * 100)
    lines.append(f"Generated : {datetime.now():%Y-%m-%d %H:%M:%S}")
    lines.append(f"DB        : {db_file}")
    lines.append(f"MODE      : {'TEST/WORK' if USE_TEST_DB else 'PROD'}")
    lines.append(f"Period    : {period_code}")
    lines.append(f"Months    : {months}")
    lines.append(f"Tariff on : {valid_on}")
    lines.append("")

    # Сначала SKIPPED, потому что это рабочий список для добивания тарифов.
    lines.append("=" * 100)
    lines.append("SKIPPED — НЕ БУДЕТ НАЧИСЛЕНО")
    lines.append("=" * 100)
    lines.append("id | apt | plate | model | parking_time | reason")
    lines.append("-" * 100)

    if skipped:
        for item in skipped:
            lines.append(
                f"{item['vehicle_id']} | "
                f"{item['apartment_number']} | "
                f"{item['plate']} | "
                f"{item['model']} | "
                f"{item['parking_time']} | "
                f"{item['reason']}"
            )
    else:
        lines.append("нет пропущенных авто")
    lines.append("")

    lines.append("=" * 100)
    lines.append("CHARGES TO CREATE")
    lines.append("=" * 100)
    lines.append("apt | plate | model | parking_time | service | qty | unit_price | amount")
    lines.append("-" * 100)

    if charges:
        for item in charges:
            lines.append(
                f"{item['apartment_number']} | "
                f"{item['plate']} | "
                f"{item['model']} | "
                f"{item['parking_time']} | "
                f"{item['service_code']} | "
                f"{format_money(item['quantity'])} | "
                f"{format_money(item['unit_price'])} | "
                f"{format_money(item['amount'])}"
            )
    else:
        lines.append("нет новых начислений")
    lines.append("")

    if existing:
        lines.append("=" * 100)
        lines.append("EXISTING CHARGES — УЖЕ БЫЛИ СОЗДАНЫ РАНЕЕ")
        lines.append("=" * 100)
        lines.append("charge_id | apt | plate | service | amount")
        lines.append("-" * 100)

        for item in existing:
            lines.append(
                f"{item['existing_charge_id']} | "
                f"{item['apartment_number']} | "
                f"{item['plate']} | "
                f"{item['service_code']} | "
                f"{format_money(item['amount'])}"
            )
        lines.append("")

    if missing_tariffs:
        lines.append("=" * 100)
        lines.append("MISSING SERVICE TARIFFS")
        lines.append("=" * 100)
        for item in missing_tariffs:
            lines.append(
                f"{item['service_code']} | "
                f"id={item['vehicle_id']} | "
                f"apt={item['apartment_number']} | "
                f"plate={item['plate']}"
            )
        lines.append("")

    day_count = sum(1 for x in charges if x["service_code"] == "PARKING_DAY")
    night_count = sum(1 for x in charges if x["service_code"] == "PARKING_NIGHT")
    day_total = sum(x["amount"] for x in charges if x["service_code"] == "PARKING_DAY")
    night_total = sum(x["amount"] for x in charges if x["service_code"] == "PARKING_NIGHT")
    total = day_total + night_total

    lines.append("=" * 100)
    lines.append("SUMMARY")
    lines.append("=" * 100)
    lines.append(f"New charges count       : {len(charges)}")
    lines.append(f"Existing charges count  : {len(existing)}")
    lines.append(f"Skipped vehicles        : {len(skipped)}")
    lines.append(f"Missing tariffs         : {len(missing_tariffs)}")
    lines.append("")
    lines.append(f"PARKING_DAY   count     : {day_count}")
    lines.append(f"PARKING_DAY   total     : {format_money(day_total)}")
    lines.append(f"PARKING_NIGHT count     : {night_count}")
    lines.append(f"PARKING_NIGHT total     : {format_money(night_total)}")
    lines.append("")
    lines.append(f"TOTAL TO CREATE         : {format_money(total)}")

    report_file.write_text("\n".join(lines), encoding="utf-8")


def apply_charges(conn, charges, period_code):
    cur = conn.cursor()
    columns = table_columns(cur, "charges")
    status_col = pick_status_column(cur, "charges")

    inserted = 0

    for item in charges:
        values = {
            "period_code": period_code,
            "apartment_number": item["apartment_number"],
            "vehicle_id": item["vehicle_id"],
            "service_code": item["service_code"],
            "quantity": item["quantity"],
            "unit": item["unit"],
            "unit_price": item["unit_price"],
            "amount": item["amount"],
            "source": "system",
            "source_ref": f"{period_code}:{item['vehicle_id']}:{item['service_code']}",
            "created_by": "generate_parking_charges.py",
            "comment": f"Generated from vehicles.parking_time={item['parking_time']}",
        }

        if status_col:
            values[status_col] = "unpaid"

        insert_cols = [
            col for col in [
                "period_code",
                "apartment_number",
                "vehicle_id",
                "service_code",
                "quantity",
                "unit",
                "unit_price",
                "amount",
                status_col,
                "source",
                "source_ref",
                "created_by",
                "comment",
            ]
            if col and col in columns
        ]

        placeholders = ",".join("?" for _ in insert_cols)

        cur.execute(f"""
            INSERT INTO charges (
                {", ".join(insert_cols)}
            )
            VALUES ({placeholders})
        """, tuple(values[col] for col in insert_cols))

        inserted += cur.rowcount

    conn.commit()
    return inserted


def main():
    parser = argparse.ArgumentParser(
        description="Generate parking charges from vehicles.parking_time."
    )

    parser.add_argument("--period", default=DEFAULT_PERIOD_CODE)
    parser.add_argument("--months", type=float, default=DEFAULT_MONTHS)
    parser.add_argument("--valid-on", default=DEFAULT_VALID_ON)
    parser.add_argument("--apply", action="store_true")

    args = parser.parse_args()

    db_file = get_db_file()

    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row

    charges, skipped, existing, missing_tariffs = build_charges(
        conn,
        period_code=args.period,
        months=args.months,
        valid_on=args.valid_on,
    )

    inserted = 0
    if args.apply:
        inserted = apply_charges(conn, charges, args.period)

    report_dir = paths.OSBB_EXPORTS_DIR / "billing"
    report_dir.mkdir(parents=True, exist_ok=True)

    mode = "apply" if args.apply else "dry_run"
    report_file = report_dir / f"parking_charges_{args.period}_{mode}_{now_ts()}.txt"

    write_report(
        report_file=report_file,
        charges=charges,
        skipped=skipped,
        existing=existing,
        missing_tariffs=missing_tariffs,
        period_code=args.period,
        months=args.months,
        valid_on=args.valid_on,
        db_file=db_file,
        apply_mode=args.apply,
    )

    conn.close()

    print("=" * 70)
    print("PARKING CHARGES")
    print("=" * 70)
    print("DB:", db_file)
    print("MODE:", "TEST/WORK" if USE_TEST_DB else "PROD")
    print("Period:", args.period)
    print("Months:", args.months)
    print("Apply:", args.apply)
    print("")
    print("New charges:", len(charges))
    print("Existing charges:", len(existing))
    print("Skipped vehicles:", len(skipped))
    print("Missing tariffs:", len(missing_tariffs))

    if args.apply:
        print("Inserted:", inserted)
    else:
        print("")
        print("DRY RUN ONLY. Database was not changed.")
        print("To apply, run with --apply")

    print("")
    print("Report:")
    print(report_file)
    print("=" * 70)


if __name__ == "__main__":
    main()
