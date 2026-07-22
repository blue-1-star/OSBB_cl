from pathlib import Path
import sys
import sqlite3
from datetime import datetime

# cashier_unpaid_preview.py
# READ ONLY: показывает неоплаченные/частично оплаченные начисления по TEST DB
# По умолчанию: период 2026-07, сервисы PARKING_DAY/PARKING_NIGHT/BARRIER_PHONE

OSBB_ROOT = Path(__file__).resolve().parents[1] if Path(__file__).resolve().parent.name.lower() == "tools" else Path(__file__).resolve().parent
PY_ROOT = OSBB_ROOT.parent

if str(PY_ROOT) not in sys.path:
    sys.path.insert(0, str(PY_ROOT))

from config import paths, USE_TEST_DB  # noqa: E402

PERIOD_CODE = "2026-07"
SERVICE_CODES = ("PARKING_DAY", "PARKING_NIGHT", "BARRIER_PHONE")
LIMIT = 500


def db_file():
    return paths.OSBB_TEST_DB_FILE if USE_TEST_DB else paths.OSBB_DB_FILE


def money(value):
    value = float(value or 0)
    return str(int(value)) if value.is_integer() else f"{value:.2f}"


def norm(value):
    return "" if value is None else str(value).strip()


def connect():
    conn = sqlite3.connect(db_file())
    conn.row_factory = sqlite3.Row
    return conn


def table_exists(cur, table):
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
    return cur.fetchone() is not None


def columns(cur, table):
    cur.execute(f"PRAGMA table_info({table})")
    return {r[1] for r in cur.fetchall()}


def require_schema(cur):
    required = {
        "charges": {"id", "period_code", "apartment_number", "amount", "status"},
        "payment_allocations": {"charge_id", "amount"},
        "apartments": {"id", "apartment_number"},
        "vehicles": {"id", "apartment_id", "license_plate", "car_model", "parking_time"},
    }
    problems = []
    for table, req_cols in required.items():
        if not table_exists(cur, table):
            problems.append(f"missing table: {table}")
            continue
        missing = sorted(req_cols - columns(cur, table))
        if missing:
            problems.append(f"{table}: missing columns: {', '.join(missing)}")
    return problems


def load_unpaid(cur):
    charge_cols = columns(cur, "charges")
    amount_expr = "COALESCE(c.net_amount, c.amount)" if "net_amount" in charge_cols else "c.amount"
    service_expr = "COALESCE(NULLIF(c.base_service_code,''), NULLIF(c.service_code,''), '')"
    item_expr = "COALESCE(NULLIF(c.service_item_code,''), NULLIF(c.service_code,''), '')"

    sql = f"""
        WITH alloc AS (
            SELECT charge_id, COALESCE(SUM(amount), 0) AS paid_amount
            FROM payment_allocations
            GROUP BY charge_id
        )
        SELECT
            c.id AS charge_id,
            c.period_code,
            c.apartment_number,
            a.id AS apartment_id,
            COALESCE(a.entrance_number, a.entrance, '') AS entrance,
            c.vehicle_id,
            COALESCE(v.license_plate_normalized, v.license_plate, '') AS plate,
            COALESCE(v.car_model_normalized, v.car_model, '') AS model,
            COALESCE(v.parking_time, '') AS parking_time,
            {service_expr} AS base_service_code,
            {item_expr} AS service_item_code,
            COALESCE(c.status, '') AS charge_status,
            {amount_expr} AS charge_amount,
            COALESCE(alloc.paid_amount, 0) AS paid_amount,
            ({amount_expr} - COALESCE(alloc.paid_amount, 0)) AS rest_amount
        FROM charges c
        LEFT JOIN alloc ON alloc.charge_id = c.id
        LEFT JOIN apartments a ON a.apartment_number = c.apartment_number
        LEFT JOIN vehicles v ON v.id = c.vehicle_id
        WHERE c.period_code = ?
          AND {service_expr} IN ({','.join('?' for _ in SERVICE_CODES)})
          AND ({amount_expr} - COALESCE(alloc.paid_amount, 0)) > 0.00001
          AND LOWER(COALESCE(c.status, '')) NOT IN ('cancelled', 'void', 'deleted')
        ORDER BY
            CAST(COALESCE(a.entrance_number, a.entrance, '999') AS INTEGER),
            CAST(c.apartment_number AS INTEGER),
            c.apartment_number,
            c.id
        LIMIT ?
    """
    cur.execute(sql, (PERIOD_CODE, *SERVICE_CODES, LIMIT))
    return [dict(r) for r in cur.fetchall()]


def flags(row):
    result = []
    service = norm(row.get("base_service_code"))
    if service.startswith("PARKING") and not norm(row.get("parking_time")):
        result.append("NO_PARKING_TIME")
    if service.startswith("PARKING") and not norm(row.get("plate")):
        result.append("NO_VEHICLE")
    if float(row.get("paid_amount") or 0) > 0:
        result.append("PARTIAL")
    return ",".join(result) if result else ""


def print_rows(rows):
    print("-" * 140)
    print("entr | apt | charge | service | item | plate | model | p_time | amount | paid | rest | flags")
    print("-" * 140)
    for r in rows:
        print(
            f"{norm(r['entrance']) or '-':>4} | "
            f"{norm(r['apartment_number']):>3} | "
            f"{r['charge_id']:>6} | "
            f"{norm(r['base_service_code'])[:14]:14} | "
            f"{norm(r['service_item_code'])[:14]:14} | "
            f"{norm(r['plate'])[:10]:10} | "
            f"{norm(r['model'])[:14]:14} | "
            f"{norm(r['parking_time']) or '-':7} | "
            f"{money(r['charge_amount']):>6} | "
            f"{money(r['paid_amount']):>5} | "
            f"{money(r['rest_amount']):>6} | "
            f"{flags(r)}"
        )
    print("-" * 140)


def print_group_summary(rows):
    by_entrance = {}
    for r in rows:
        e = norm(r.get("entrance")) or "?"
        by_entrance.setdefault(e, {"count": 0, "sum": 0.0, "problems": 0})
        by_entrance[e]["count"] += 1
        by_entrance[e]["sum"] += float(r.get("rest_amount") or 0)
        if flags(r):
            by_entrance[e]["problems"] += 1

    print("SUMMARY BY ENTRANCE")
    print("-" * 70)
    print("entr | rows | rest_sum | problem_rows")
    print("-" * 70)
    for e in sorted(by_entrance, key=lambda x: (999 if x == '?' else int(x) if x.isdigit() else 998, x)):
        item = by_entrance[e]
        print(f"{e:>4} | {item['count']:>4} | {money(item['sum']):>8} | {item['problems']:>12}")
    print("-" * 70)


def main():
    print("OSBB cashier unpaid preview - READ ONLY")
    print("Generated:", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    print("USE_TEST_DB:", USE_TEST_DB)
    print("DB:", db_file())
    print("Period:", PERIOD_CODE)
    print("Services:", ", ".join(SERVICE_CODES))
    print("")

    conn = connect()
    cur = conn.cursor()
    problems = require_schema(cur)
    if problems:
        print("SCHEMA PROBLEMS:")
        for p in problems:
            print(" -", p)
        conn.close()
        return

    rows = load_unpaid(cur)
    conn.close()

    print("Unpaid / partial charges:", len(rows))
    print("")
    print_group_summary(rows)
    print("")
    print_rows(rows)
    print("READ ONLY COMPLETED")


if __name__ == "__main__":
    main()
