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


def get_db_file():
    return paths.OSBB_TEST_DB_FILE if USE_TEST_DB else paths.OSBB_DB_FILE


def now_ts():
    return datetime.now().strftime("%Y-%m-%d_%H-%M-%S")


def money(value):
    value = float(value or 0)
    return str(int(value)) if value.is_integer() else f"{value:.2f}"


def apt_sort_key(apt):
    s = "" if apt is None else str(apt)
    return (0, int(s)) if s.isdigit() else (1, s)


def table_exists(cur, table_name):
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table_name,))
    return cur.fetchone() is not None


def table_columns(cur, table_name):
    cur.execute(f"PRAGMA table_info({table_name})")
    return [row[1] for row in cur.fetchall()]


def pick_table(cur, *names):
    for name in names:
        if table_exists(cur, name):
            return name
    return None


def service_label(code):
    return {
        "PARKING_DAY": "Парковка Day",
        "PARKING_NIGHT": "Парковка Night",
        "BARRIER_CALL": "Шлагбаум",
        "IMPROVEMENT": "Благоустройство",
    }.get(code or "", code or "-")


def load_charges(cur, charges_table, period_code):
    if not charges_table:
        return []

    columns = table_columns(cur, charges_table)
    status_col = "charge_status" if "charge_status" in columns else ("status" if "status" in columns else None)

    sql = f"""
        SELECT
            c.id AS charge_id,
            c.period_code,
            c.apartment_number,
            c.vehicle_id,
            c.service_code,
            c.amount,
            COALESCE(v.license_plate_normalized, v.license_plate, '') AS plate,
            COALESCE(v.car_model_normalized, v.car_model, '') AS model
        FROM {charges_table} c
        LEFT JOIN vehicles v ON v.id = c.vehicle_id
        WHERE c.period_code = ?
    """

    if status_col:
        sql += f" AND COALESCE(c.{status_col}, '') <> 'cancelled'"

    sql += " ORDER BY c.apartment_number, c.vehicle_id, c.id"

    cur.execute(sql, (period_code,))
    return [dict(row) for row in cur.fetchall()]


def load_payments(cur, payments_table, period_code):
    if not payments_table:
        return []

    columns = table_columns(cur, payments_table)

    payment_date_expr = "p.payment_date" if "payment_date" in columns else "NULL"
    apartment_expr = "p.apartment_number" if "apartment_number" in columns else "NULL"
    vehicle_expr = "p.vehicle_id" if "vehicle_id" in columns else "NULL"
    service_expr = "p.service_code" if "service_code" in columns else "NULL"
    source_expr = "p.source" if "source" in columns else "NULL"
    source_ref_expr = "p.source_ref" if "source_ref" in columns else "NULL"
    comment_expr = "p.comment" if "comment" in columns else "NULL"

    order_sql = ", ".join([x for x in [
        "p.apartment_number" if "apartment_number" in columns else None,
        "p.payment_date" if "payment_date" in columns else None,
        "p.id",
    ] if x])

    cur.execute(f"""
        SELECT
            p.id AS payment_id,
            {payment_date_expr} AS payment_date,
            p.period_code,
            {apartment_expr} AS apartment_number,
            {vehicle_expr} AS vehicle_id,
            {service_expr} AS service_code,
            p.amount,
            {source_expr} AS source,
            {source_ref_expr} AS source_ref,
            {comment_expr} AS comment,
            COALESCE(v.license_plate_normalized, v.license_plate, '') AS plate,
            COALESCE(v.car_model_normalized, v.car_model, '') AS model
        FROM {payments_table} p
        LEFT JOIN vehicles v ON v.id = {vehicle_expr}
        WHERE p.period_code = ?
        ORDER BY {order_sql}
    """, (period_code,))

    return [dict(row) for row in cur.fetchall()]


def load_allocations(cur, allocations_table):
    if not allocations_table:
        return []

    cur.execute(f"""
        SELECT
            id AS allocation_id,
            payment_id,
            charge_id,
            amount
        FROM {allocations_table}
        ORDER BY id
    """)
    return [dict(row) for row in cur.fetchall()]


def build_reconciliation(conn, period_code):
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    charges_table = pick_table(cur, "charges", "service_charges")
    payments_table = pick_table(cur, "payments", "service_payments")
    allocations_table = pick_table(cur, "payment_allocations", "service_payment_allocations")

    charges = load_charges(cur, charges_table, period_code)
    payments = load_payments(cur, payments_table, period_code)
    allocations = load_allocations(cur, allocations_table)

    allocated_by_charge = {}
    allocated_by_payment = {}

    for a in allocations:
        charge_id = a["charge_id"]
        payment_id = a["payment_id"]
        amount = float(a["amount"] or 0)
        allocated_by_charge[charge_id] = allocated_by_charge.get(charge_id, 0) + amount
        allocated_by_payment[payment_id] = allocated_by_payment.get(payment_id, 0) + amount

    fully_matched = []
    partially_paid = []
    unpaid_charges = []
    overallocated_charges = []

    payments_without_charge = []
    unknown_payments = []
    payments_to_empty_apartment = []
    apartment_level_payments = []

    for c in charges:
        amount = float(c["amount"] or 0)
        allocated = float(allocated_by_charge.get(c["charge_id"], 0))
        balance = amount - allocated
        item = dict(c)
        item["allocated"] = allocated
        item["balance"] = balance

        if abs(balance) <= 0.01 and allocated > 0:
            fully_matched.append(item)
        elif allocated > amount + 0.01:
            overallocated_charges.append(item)
        elif allocated > 0.01:
            partially_paid.append(item)
        else:
            unpaid_charges.append(item)

    for p in payments:
        amount = float(p["amount"] or 0)
        allocated = float(allocated_by_payment.get(p["payment_id"], 0))
        unallocated = amount - allocated

        if abs(unallocated) <= 0.01:
            continue

        item = dict(p)
        item["allocated"] = allocated
        item["unallocated"] = unallocated

        apt = str(p.get("apartment_number") or "").strip()
        vehicle_id = p.get("vehicle_id")

        if not apt and not vehicle_id:
            unknown_payments.append(item)
        elif not apt:
            payments_to_empty_apartment.append(item)
        elif not vehicle_id:
            apartment_level_payments.append(item)
        else:
            payments_without_charge.append(item)

    return {
        "charges_table": charges_table,
        "payments_table": payments_table,
        "allocations_table": allocations_table,
        "charges": charges,
        "payments": payments,
        "allocations": allocations,
        "fully_matched": fully_matched,
        "partially_paid": partially_paid,
        "unpaid_charges": unpaid_charges,
        "overallocated_charges": overallocated_charges,
        "payments_without_charge": payments_without_charge,
        "unknown_payments": unknown_payments,
        "payments_to_empty_apartment": payments_to_empty_apartment,
        "apartment_level_payments": apartment_level_payments,
    }


def format_charge_line(c):
    return (
        f"charge_id={c['charge_id']} | apt={c.get('apartment_number') or '-'} | "
        f"{service_label(c.get('service_code'))} | plate={c.get('plate') or '-'} | "
        f"model={c.get('model') or '-'} | amount={money(c.get('amount'))} | "
        f"allocated={money(c.get('allocated'))} | balance={money(c.get('balance'))}"
    )


def format_payment_line(p):
    return (
        f"payment_id={p['payment_id']} | date={p.get('payment_date') or '-'} | "
        f"apt={p.get('apartment_number') or '-'} | vehicle_id={p.get('vehicle_id') or '-'} | "
        f"service={service_label(p.get('service_code'))} | plate={p.get('plate') or '-'} | "
        f"amount={money(p.get('amount'))} | allocated={money(p.get('allocated'))} | "
        f"unallocated={money(p.get('unallocated'))} | source={p.get('source') or '-'} | "
        f"ref={p.get('source_ref') or '-'}"
    )


def write_section(lines, title, items, formatter):
    lines.append("=" * 120)
    lines.append(title)
    lines.append("=" * 120)

    if not items:
        lines.append("нет записей")
        lines.append("")
        return

    def sort_item(x):
        return (
            apt_sort_key(x.get("apartment_number")),
            x.get("vehicle_id") or 0,
            x.get("charge_id") or x.get("payment_id") or 0,
        )

    for item in sorted(items, key=sort_item):
        lines.append(formatter(item))
    lines.append("")


def write_report(report_file, data, db_file, period_code):
    lines = []

    total_charges = sum(float(c["amount"] or 0) for c in data["charges"])
    total_payments = sum(float(p["amount"] or 0) for p in data["payments"])
    total_allocations = sum(float(a["amount"] or 0) for a in data["allocations"])

    total_unallocated = sum(
        float(p.get("unallocated") or 0)
        for group in [
            data["payments_without_charge"],
            data["unknown_payments"],
            data["payments_to_empty_apartment"],
            data["apartment_level_payments"],
        ]
        for p in group
    )

    lines.append("=" * 120)
    lines.append("BILLING RECONCILIATION REPORT")
    lines.append("=" * 120)
    lines.append(f"Generated  : {datetime.now():%Y-%m-%d %H:%M:%S}")
    lines.append(f"DB         : {db_file}")
    lines.append(f"MODE       : {'TEST/WORK' if USE_TEST_DB else 'PROD'}")
    lines.append(f"Period     : {period_code}")
    lines.append(f"Charges    : {data['charges_table']}")
    lines.append(f"Payments   : {data['payments_table']}")
    lines.append(f"Allocations: {data['allocations_table']}")
    lines.append("")

    lines.append("=" * 120)
    lines.append("SUMMARY")
    lines.append("=" * 120)
    lines.append(f"Charges count              : {len(data['charges'])}")
    lines.append(f"Payments count             : {len(data['payments'])}")
    lines.append(f"Allocations count          : {len(data['allocations'])}")
    lines.append(f"Charges total              : {money(total_charges)}")
    lines.append(f"Payments total             : {money(total_payments)}")
    lines.append(f"Allocated total            : {money(total_allocations)}")
    lines.append(f"Unallocated total          : {money(total_unallocated)}")
    lines.append("")
    lines.append(f"Fully matched charges      : {len(data['fully_matched'])}")
    lines.append(f"Partially paid charges     : {len(data['partially_paid'])}")
    lines.append(f"Unpaid charges             : {len(data['unpaid_charges'])}")
    lines.append(f"Overallocated charges      : {len(data['overallocated_charges'])}")
    lines.append(f"Payments without charge    : {len(data['payments_without_charge'])}")
    lines.append(f"Apartment-level payments   : {len(data['apartment_level_payments'])}")
    lines.append(f"Unknown payments           : {len(data['unknown_payments'])}")
    lines.append(f"Empty apartment payments   : {len(data['payments_to_empty_apartment'])}")
    lines.append("")

    write_section(lines, "1. FULLY_MATCHED_CHARGES — начисление полностью закрыто оплатой", data["fully_matched"], format_charge_line)
    write_section(lines, "2. PARTIALLY_PAID_CHARGES — частично оплачено", data["partially_paid"], format_charge_line)
    write_section(lines, "3. UNPAID_CHARGES — начисления без оплаты", data["unpaid_charges"], format_charge_line)
    write_section(lines, "4. OVERALLOCATED_CHARGES — к начислению привязано больше, чем начислено", data["overallocated_charges"], format_charge_line)
    write_section(lines, "5. PAYMENTS_WITHOUT_CHARGE — есть авто/квартира, но нет подходящего начисления", data["payments_without_charge"], format_payment_line)
    write_section(lines, "6. APARTMENT_LEVEL_PAYMENTS — оплата на квартиру без конкретного авто", data["apartment_level_payments"], format_payment_line)
    write_section(lines, "7. UNKNOWN_PAYMENTS — платежи без квартиры и без найденного авто", data["unknown_payments"], format_payment_line)
    write_section(lines, "8. EMPTY_APARTMENT_PAYMENTS — платежи с пустой квартирой, но с vehicle_id", data["payments_to_empty_apartment"], format_payment_line)

    lines.append("=" * 120)
    lines.append("NEXT ACTION HINTS")
    lines.append("=" * 120)
    lines.append("UNKNOWN_PAYMENTS: искать номер/плательщика и привязывать к квартире/авто.")
    lines.append("APARTMENT_LEVEL_PAYMENTS: проверить, можно ли распределить на начисления квартиры.")
    lines.append("PAYMENTS_WITHOUT_CHARGE: вероятно, создать недостающее начисление или сменить тип услуги.")
    lines.append("PARTIALLY_PAID_CHARGES: это реальные частичные оплаты или старые тарифы.")
    lines.append("OVERALLOCATED_CHARGES: проверить переплату или ошибочное распределение.")

    report_file.write_text("\n".join(lines), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description="Supervisor reconciliation report for billing data quality.")
    parser.add_argument("--period", default=DEFAULT_PERIOD_CODE)
    args = parser.parse_args()

    db_file = get_db_file()
    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row

    data = build_reconciliation(conn, args.period)
    conn.close()

    report_dir = paths.OSBB_EXPORTS_DIR / "billing"
    report_dir.mkdir(parents=True, exist_ok=True)

    report_file = report_dir / f"billing_reconciliation_report_{args.period}_{now_ts()}.txt"

    write_report(report_file, data, db_file, args.period)

    print("=" * 70)
    print("BILLING RECONCILIATION REPORT")
    print("=" * 70)
    print("DB:", db_file)
    print("MODE:", "TEST/WORK" if USE_TEST_DB else "PROD")
    print("Period:", args.period)
    print("")
    print("Fully matched:", len(data["fully_matched"]))
    print("Partially paid:", len(data["partially_paid"]))
    print("Unpaid charges:", len(data["unpaid_charges"]))
    print("Payments without charge:", len(data["payments_without_charge"]))
    print("Apartment-level payments:", len(data["apartment_level_payments"]))
    print("Unknown payments:", len(data["unknown_payments"]))
    print("")
    print("Report:")
    print(report_file)
    print("=" * 70)


if __name__ == "__main__":
    main()
