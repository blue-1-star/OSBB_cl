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


def table_exists(cur, table_name):
    cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    )
    return cur.fetchone() is not None


def table_columns(cur, table_name):
    cur.execute(f"PRAGMA table_info({table_name})")
    return [row[1] for row in cur.fetchall()]


def pick_table(cur, *names):
    for name in names:
        if table_exists(cur, name):
            return name
    return None


def money(value):
    value = float(value or 0)
    if value.is_integer():
        return str(int(value))
    return f"{value:.2f}"


def apt_sort_key(apt):
    s = "" if apt is None else str(apt)
    return (0, int(s)) if s.isdigit() else (1, s)


def load_charge_rows(cur, charges_table, period_code):
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
            c.quantity,
            c.unit_price,
            COALESCE(v.license_plate_normalized, v.license_plate, '') AS plate,
            COALESCE(v.car_model_normalized, v.car_model, '') AS model
        FROM {charges_table} c
        LEFT JOIN vehicles v
            ON v.id = c.vehicle_id
        WHERE c.period_code = ?
    """

    if status_col:
        sql += f" AND COALESCE(c.{status_col}, '') <> 'cancelled'"

    sql += " ORDER BY c.apartment_number, c.vehicle_id, c.id"

    cur.execute(sql, (period_code,))
    return [dict(row) for row in cur.fetchall()]


def load_payment_rows(cur, payments_table, period_code):
    if not payments_table:
        return []

    columns = table_columns(cur, payments_table)

    service_expr = "p.service_code" if "service_code" in columns else "NULL"
    payment_date_expr = "p.payment_date" if "payment_date" in columns else "NULL"
    apartment_expr = "p.apartment_number" if "apartment_number" in columns else "NULL"
    vehicle_expr = "p.vehicle_id" if "vehicle_id" in columns else "NULL"
    method_expr = "p.payment_method" if "payment_method" in columns else "NULL"
    source_expr = "p.source" if "source" in columns else "NULL"
    source_ref_expr = "p.source_ref" if "source_ref" in columns else "NULL"
    comment_expr = "p.comment" if "comment" in columns else "NULL"

    order_parts = []
    if "apartment_number" in columns:
        order_parts.append("p.apartment_number")
    if "payment_date" in columns:
        order_parts.append("p.payment_date")
    order_parts.append("p.id")
    order_sql = ", ".join(order_parts)

    cur.execute(f"""
        SELECT
            p.id AS payment_id,
            {payment_date_expr} AS payment_date,
            p.period_code,
            {apartment_expr} AS apartment_number,
            {vehicle_expr} AS vehicle_id,
            {service_expr} AS service_code,
            p.amount,
            {method_expr} AS payment_method,
            {source_expr} AS source,
            {source_ref_expr} AS source_ref,
            {comment_expr} AS comment,
            COALESCE(v.license_plate_normalized, v.license_plate, '') AS plate,
            COALESCE(v.car_model_normalized, v.car_model, '') AS model
        FROM {payments_table} p
        LEFT JOIN vehicles v
            ON v.id = {vehicle_expr}
        WHERE p.period_code = ?
        ORDER BY {order_sql}
    """, (period_code,))

    return [dict(row) for row in cur.fetchall()]


def load_allocations(cur, allocations_table):
    if not allocations_table:
        return []

    cur.execute(f"""
        SELECT
            payment_id,
            charge_id,
            amount
        FROM {allocations_table}
    """)

    return [dict(row) for row in cur.fetchall()]


def load_apartments(cur):
    cur.execute("""
        SELECT
            id,
            apartment_number
        FROM apartments
    """)

    return {str(row["apartment_number"]): dict(row) for row in cur.fetchall()}


def build_statement(conn, period_code):
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    charges_table = pick_table(cur, "charges", "service_charges")
    payments_table = pick_table(cur, "payments", "service_payments")
    allocations_table = pick_table(cur, "payment_allocations", "service_payment_allocations")

    charges = load_charge_rows(cur, charges_table, period_code)
    payments = load_payment_rows(cur, payments_table, period_code)
    allocations = load_allocations(cur, allocations_table)
    apartments = load_apartments(cur)

    charge_by_id = {row["charge_id"]: row for row in charges}
    payment_by_id = {row["payment_id"]: row for row in payments}

    allocated_by_charge = {}
    allocated_by_payment = {}

    for row in allocations:
        charge_id = row["charge_id"]
        payment_id = row["payment_id"]
        amount = float(row["amount"] or 0)

        allocated_by_charge[charge_id] = allocated_by_charge.get(charge_id, 0) + amount
        allocated_by_payment[payment_id] = allocated_by_payment.get(payment_id, 0) + amount

    statement = {}

    def ensure_apt(apt):
        apt = "" if apt is None else str(apt)
        if apt not in statement:
            statement[apt] = {
                "apartment_number": apt,
                "charges_total": 0.0,
                "payments_total": 0.0,
                "allocated_total": 0.0,
                "unallocated_payments": 0.0,
                "balance": 0.0,
                "charges": [],
                "payments": [],
            }
        return statement[apt]

    for charge in charges:
        apt = str(charge["apartment_number"] or "")
        item = ensure_apt(apt)
        amount = float(charge["amount"] or 0)
        allocated = float(allocated_by_charge.get(charge["charge_id"], 0))

        charge = dict(charge)
        charge["allocated_amount"] = allocated
        charge["charge_balance"] = amount - allocated

        item["charges_total"] += amount
        item["allocated_total"] += allocated
        item["charges"].append(charge)

    for payment in payments:
        apt = str(payment["apartment_number"] or "")
        item = ensure_apt(apt)
        amount = float(payment["amount"] or 0)
        allocated = float(allocated_by_payment.get(payment["payment_id"], 0))

        payment = dict(payment)
        payment["allocated_amount"] = allocated
        payment["unallocated_amount"] = amount - allocated

        item["payments_total"] += amount
        item["unallocated_payments"] += amount - allocated
        item["payments"].append(payment)

    for apt, item in statement.items():
        item["balance"] = item["charges_total"] - item["payments_total"]

    return {
        "charges_table": charges_table,
        "payments_table": payments_table,
        "allocations_table": allocations_table,
        "statement": statement,
        "charges": charges,
        "payments": payments,
        "allocations": allocations,
        "apartments": apartments,
    }


def service_label(code):
    labels = {
        "PARKING_DAY": "Парковка Day",
        "PARKING_NIGHT": "Парковка Night",
        "BARRIER_CALL": "Шлагбаум",
        "IMPROVEMENT": "Благоустройство",
    }
    return labels.get(code or "", code or "-")


def write_report(report_file, data, db_file, period_code):
    statement = data["statement"]

    rows = sorted(statement.values(), key=lambda x: apt_sort_key(x["apartment_number"]))

    lines = []
    lines.append("=" * 120)
    lines.append("PARKING BILLING STATEMENT")
    lines.append("=" * 120)
    lines.append(f"Generated : {datetime.now():%Y-%m-%d %H:%M:%S}")
    lines.append(f"DB        : {db_file}")
    lines.append(f"MODE      : {'TEST/WORK' if USE_TEST_DB else 'PROD'}")
    lines.append(f"Period    : {period_code}")
    lines.append(f"Charges   : {data['charges_table']}")
    lines.append(f"Payments  : {data['payments_table']}")
    lines.append(f"Allocations: {data['allocations_table']}")
    lines.append("")

    total_charges = sum(x["charges_total"] for x in rows)
    total_payments = sum(x["payments_total"] for x in rows)
    total_allocated = sum(x["allocated_total"] for x in rows)
    total_unallocated = sum(x["unallocated_payments"] for x in rows)
    total_balance = sum(x["balance"] for x in rows)

    debt_rows = [x for x in rows if x["balance"] > 0.01]
    overpay_rows = [x for x in rows if x["balance"] < -0.01]
    zero_rows = [x for x in rows if abs(x["balance"]) <= 0.01]

    lines.append("=" * 120)
    lines.append("SUMMARY")
    lines.append("=" * 120)
    lines.append(f"Apartments in statement : {len(rows)}")
    lines.append(f"Charges total           : {money(total_charges)}")
    lines.append(f"Payments total          : {money(total_payments)}")
    lines.append(f"Allocated payments      : {money(total_allocated)}")
    lines.append(f"Unallocated payments    : {money(total_unallocated)}")
    lines.append(f"Balance total           : {money(total_balance)}")
    lines.append(f"Debtors                 : {len(debt_rows)}")
    lines.append(f"Overpayments            : {len(overpay_rows)}")
    lines.append(f"Zero balance            : {len(zero_rows)}")
    lines.append("")

    lines.append("=" * 120)
    lines.append("MAIN STATEMENT")
    lines.append("=" * 120)
    lines.append("apt | charged | paid | allocated | unallocated | balance | status")
    lines.append("-" * 120)

    for item in rows:
        balance = item["balance"]
        if balance > 0.01:
            status = "DEBT"
        elif balance < -0.01:
            status = "OVERPAY"
        else:
            status = "OK"

        lines.append(
            f"{item['apartment_number']} | "
            f"{money(item['charges_total'])} | "
            f"{money(item['payments_total'])} | "
            f"{money(item['allocated_total'])} | "
            f"{money(item['unallocated_payments'])} | "
            f"{money(balance)} | "
            f"{status}"
        )

    lines.append("")
    lines.append("=" * 120)
    lines.append("DEBTORS")
    lines.append("=" * 120)
    lines.append("apt | debt | charged | paid")
    lines.append("-" * 120)

    if not debt_rows:
        lines.append("нет должников")
    else:
        for item in sorted(debt_rows, key=lambda x: (-x["balance"], apt_sort_key(x["apartment_number"]))):
            lines.append(
                f"{item['apartment_number']} | "
                f"{money(item['balance'])} | "
                f"{money(item['charges_total'])} | "
                f"{money(item['payments_total'])}"
            )

    lines.append("")
    lines.append("=" * 120)
    lines.append("OVERPAYMENTS / UNALLOCATED PAYMENTS")
    lines.append("=" * 120)
    lines.append("apt | overpay/balance | paid | charged | unallocated")
    lines.append("-" * 120)

    if not overpay_rows and total_unallocated <= 0.01:
        lines.append("нет переплат")
    else:
        for item in sorted(overpay_rows, key=lambda x: (x["balance"], apt_sort_key(x["apartment_number"]))):
            lines.append(
                f"{item['apartment_number']} | "
                f"{money(-item['balance'])} | "
                f"{money(item['payments_total'])} | "
                f"{money(item['charges_total'])} | "
                f"{money(item['unallocated_payments'])}"
            )

        for item in rows:
            if item["unallocated_payments"] > 0.01 and item not in overpay_rows:
                lines.append(
                    f"{item['apartment_number']} | "
                    f"0 | "
                    f"{money(item['payments_total'])} | "
                    f"{money(item['charges_total'])} | "
                    f"{money(item['unallocated_payments'])}"
                )

    lines.append("")
    lines.append("=" * 120)
    lines.append("DETAILS BY APARTMENT")
    lines.append("=" * 120)

    for item in rows:
        lines.append("-" * 120)
        lines.append(
            f"Apartment {item['apartment_number']} | "
            f"charged={money(item['charges_total'])} | "
            f"paid={money(item['payments_total'])} | "
            f"balance={money(item['balance'])}"
        )

        if item["charges"]:
            lines.append("  Charges:")
            for ch in item["charges"]:
                plate = ch.get("plate") or "-"
                model = ch.get("model") or "-"
                lines.append(
                    f"    charge_id={ch['charge_id']} | "
                    f"{service_label(ch['service_code'])} | "
                    f"{plate} | {model} | "
                    f"amount={money(ch['amount'])} | "
                    f"allocated={money(ch.get('allocated_amount', 0))} | "
                    f"balance={money(ch.get('charge_balance', 0))}"
                )

        if item["payments"]:
            lines.append("  Payments:")
            for p in item["payments"]:
                plate = p.get("plate") or "-"
                service = service_label(p.get("service_code"))
                lines.append(
                    f"    payment_id={p['payment_id']} | "
                    f"{p.get('payment_date') or '-'} | "
                    f"{service} | {plate} | "
                    f"amount={money(p['amount'])} | "
                    f"allocated={money(p.get('allocated_amount', 0))} | "
                    f"unallocated={money(p.get('unallocated_amount', 0))} | "
                    f"source={p.get('source') or '-'}"
                )

    report_file.write_text("\n".join(lines), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(
        description="Create parking billing statement: charges, payments, balance."
    )

    parser.add_argument("--period", default=DEFAULT_PERIOD_CODE)

    args = parser.parse_args()

    db_file = get_db_file()
    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row

    data = build_statement(conn, args.period)
    conn.close()

    report_dir = paths.OSBB_EXPORTS_DIR / "billing"
    report_dir.mkdir(parents=True, exist_ok=True)

    report_file = report_dir / f"parking_billing_statement_{args.period}_{now_ts()}.txt"

    write_report(
        report_file=report_file,
        data=data,
        db_file=db_file,
        period_code=args.period,
    )

    statement = data["statement"]
    total_charges = sum(x["charges_total"] for x in statement.values())
    total_payments = sum(x["payments_total"] for x in statement.values())
    total_balance = sum(x["balance"] for x in statement.values())

    print("=" * 70)
    print("PARKING BILLING STATEMENT")
    print("=" * 70)
    print("DB:", db_file)
    print("MODE:", "TEST/WORK" if USE_TEST_DB else "PROD")
    print("Period:", args.period)
    print("Apartments:", len(statement))
    print("Charges total:", money(total_charges))
    print("Payments total:", money(total_payments))
    print("Balance total:", money(total_balance))
    print("")
    print("Report:")
    print(report_file)
    print("=" * 70)


if __name__ == "__main__":
    main()
