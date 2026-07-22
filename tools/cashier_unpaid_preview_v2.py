from pathlib import Path
import sys
import sqlite3
import argparse
from datetime import datetime

# This script is READ ONLY. It never writes to the database.
# Put it into: G:\Programming\Py\OSBB\tools\cashier_unpaid_preview_v2.py

THIS_FILE = Path(__file__).resolve()
OSBB_ROOT = THIS_FILE.parents[1]
PY_ROOT = OSBB_ROOT.parent
if str(PY_ROOT) not in sys.path:
    sys.path.insert(0, str(PY_ROOT))

from config import paths, USE_TEST_DB

DEFAULT_SERVICES = ["PARKING_DAY", "PARKING_NIGHT", "BARRIER_PHONE"]


def get_db_file():
    return paths.OSBB_TEST_DB_FILE if USE_TEST_DB else paths.OSBB_DB_FILE


def connect():
    conn = sqlite3.connect(get_db_file())
    conn.row_factory = sqlite3.Row
    return conn


def money(v):
    try:
        x = float(v or 0)
    except Exception:
        return v
    return int(x) if x.is_integer() else round(x, 2)


def table_exists(cur, name):
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,))
    return cur.fetchone() is not None


def cols(cur, table):
    cur.execute(f"PRAGMA table_info({table})")
    return {r[1] for r in cur.fetchall()}


def normalize_list(values):
    result = []
    for v in values or []:
        for part in str(v).split(','):
            part = part.strip()
            if part:
                result.append(part)
    return result


def print_header(title):
    print("=" * 100)
    print(title)
    print("=" * 100)


def list_periods(args):
    conn = connect()
    cur = conn.cursor()
    print_header("OSBB cashier periods - READ ONLY")
    print("Generated:", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    print("USE_TEST_DB:", USE_TEST_DB)
    print("DB:", get_db_file())
    print("")

    if not table_exists(cur, "charges"):
        print("ERROR: table charges not found")
        conn.close()
        return

    charge_cols = cols(cur, "charges")
    alloc_cols = cols(cur, "payment_allocations") if table_exists(cur, "payment_allocations") else set()

    service_expr = "COALESCE(NULLIF(base_service_code,''), NULLIF(service_code,''), 'UNCLASSIFIED')"
    item_expr = "COALESCE(NULLIF(service_item_code,''), '-')"
    paid_subquery = "0"
    if table_exists(cur, "payment_allocations") and "charge_id" in alloc_cols and "amount" in alloc_cols:
        paid_subquery = "(SELECT COALESCE(SUM(pa.amount),0) FROM payment_allocations pa WHERE pa.charge_id = c.id)"

    amount_expr = "COALESCE(c.net_amount, c.amount, 0)" if "net_amount" in charge_cols else "COALESCE(c.amount,0)"

    service_filter = normalize_list(args.services)
    where = []
    params = []
    if service_filter:
        placeholders = ",".join("?" for _ in service_filter)
        where.append(f"{service_expr} IN ({placeholders})")
        params.extend(service_filter)
    where_sql = "WHERE " + " AND ".join(where) if where else ""

    cur.execute(f"""
        SELECT
            COALESCE(NULLIF(c.period_code,''), '<EMPTY>') AS period_code,
            {service_expr} AS service_code,
            {item_expr} AS service_item_code,
            COUNT(*) AS rows_count,
            COALESCE(SUM({amount_expr}),0) AS amount_sum,
            COALESCE(SUM({paid_subquery}),0) AS paid_sum,
            COALESCE(SUM(({amount_expr}) - ({paid_subquery})),0) AS rest_sum
        FROM charges c
        {where_sql}
        GROUP BY COALESCE(NULLIF(c.period_code,''), '<EMPTY>'), {service_expr}, {item_expr}
        ORDER BY period_code, service_code, service_item_code
    """, tuple(params))
    rows = [dict(r) for r in cur.fetchall()]

    print("period_code | service | item | rows | amount | paid | rest")
    print("-" * 100)
    for r in rows:
        print(f"{r['period_code']} | {r['service_code']} | {r['service_item_code']} | {r['rows_count']} | {money(r['amount_sum'])} | {money(r['paid_sum'])} | {money(r['rest_sum'])}")
    print("-" * 100)
    print("Periods found:", len({r['period_code'] for r in rows}))
    print("READ ONLY COMPLETED")
    conn.close()


def unpaid_preview(args):
    conn = connect()
    cur = conn.cursor()
    print_header("OSBB cashier unpaid preview - READ ONLY")
    print("Generated:", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    print("USE_TEST_DB:", USE_TEST_DB)
    print("DB:", get_db_file())
    print("Period:", args.period or "<ALL>")
    print("Services:", ", ".join(normalize_list(args.services)) or "<ALL>")
    print("")

    required = ["charges", "apartments"]
    missing = [t for t in required if not table_exists(cur, t)]
    if missing:
        print("ERROR: missing tables:", ", ".join(missing))
        conn.close()
        return

    charge_cols = cols(cur, "charges")
    alloc_cols = cols(cur, "payment_allocations") if table_exists(cur, "payment_allocations") else set()

    amount_expr = "COALESCE(c.net_amount, c.amount, 0)" if "net_amount" in charge_cols else "COALESCE(c.amount,0)"
    service_expr = "COALESCE(NULLIF(c.base_service_code,''), NULLIF(c.service_code,''), 'UNCLASSIFIED')"
    paid_subquery = "0"
    if table_exists(cur, "payment_allocations") and "charge_id" in alloc_cols and "amount" in alloc_cols:
        paid_subquery = "(SELECT COALESCE(SUM(pa.amount),0) FROM payment_allocations pa WHERE pa.charge_id = c.id)"

    where = []
    params = []
    if args.period:
        where.append("c.period_code = ?")
        params.append(args.period)
    services = normalize_list(args.services)
    if services:
        where.append(f"{service_expr} IN ({','.join('?' for _ in services)})")
        params.extend(services)
    if not args.include_paid:
        where.append(f"(({amount_expr}) - ({paid_subquery})) > 0.00001")

    where_sql = "WHERE " + " AND ".join(where) if where else ""

    vehicle_join = "LEFT JOIN vehicles v ON v.id = c.vehicle_id" if table_exists(cur, "vehicles") else ""
    vehicle_cols = cols(cur, "vehicles") if table_exists(cur, "vehicles") else set()
    plate_expr = "COALESCE(v.license_plate_normalized, v.license_plate, '')" if vehicle_cols else "''"
    model_expr = "COALESCE(v.car_model_normalized, v.car_model, '')" if vehicle_cols else "''"
    parking_expr = "COALESCE(v.parking_time, '')" if vehicle_cols else "''"

    cur.execute(f"""
        SELECT
            COALESCE(a.entrance_number, a.entrance, '') AS entrance,
            c.apartment_number,
            c.id AS charge_id,
            {service_expr} AS service_code,
            COALESCE(NULLIF(c.service_item_code,''), '-') AS service_item_code,
            c.period_code,
            c.vehicle_id,
            {plate_expr} AS plate,
            {model_expr} AS model,
            {parking_expr} AS parking_time,
            {amount_expr} AS amount,
            {paid_subquery} AS paid,
            ({amount_expr}) - ({paid_subquery}) AS rest,
            COALESCE(c.status, '') AS charge_status,
            COALESCE(c.comment, '') AS charge_comment
        FROM charges c
        LEFT JOIN apartments a ON a.apartment_number = c.apartment_number
        {vehicle_join}
        {where_sql}
        ORDER BY CAST(COALESCE(a.entrance_number, a.entrance, '999') AS INTEGER), CAST(c.apartment_number AS INTEGER), c.apartment_number, c.id
    """, tuple(params))
    rows = [dict(r) for r in cur.fetchall()]

    print("Unpaid / partial charges:", len(rows))
    print("")

    summary = {}
    for r in rows:
        e = str(r["entrance"] or "-")
        flags = []
        if not str(r.get("parking_time") or "").strip() and str(r.get("service_code") or "").startswith("PARKING"):
            flags.append("NO_PARKING_TIME")
        if float(r.get("paid") or 0) > 0 and float(r.get("rest") or 0) > 0:
            flags.append("PARTIAL")
        if not r.get("vehicle_id") and str(r.get("service_code") or "").startswith("PARKING"):
            flags.append("NO_VEHICLE")
        s = summary.setdefault(e, {"rows": 0, "rest": 0.0, "problems": 0})
        s["rows"] += 1
        s["rest"] += float(r.get("rest") or 0)
        if flags:
            s["problems"] += 1
        r["flags"] = ",".join(flags)

    print("SUMMARY BY ENTRANCE")
    print("-" * 70)
    print("entr | rows | rest_sum | problem_rows")
    print("-" * 70)
    for e in sorted(summary, key=lambda x: (999 if x == '-' else int(x) if str(x).isdigit() else 998, str(x))):
        s = summary[e]
        print(f"{e} | {s['rows']} | {money(s['rest'])} | {s['problems']}")
    print("-" * 70)
    print("")

    print("-" * 150)
    print("entr | apt | charge | period | service | item | plate | model | p_time | amount | paid | rest | flags")
    print("-" * 150)
    for r in rows[:args.limit]:
        print(
            f"{r['entrance'] or '-'} | {r['apartment_number']} | {r['charge_id']} | {r['period_code']} | "
            f"{r['service_code']} | {r['service_item_code']} | {r['plate'] or '-'} | {r['model'] or '-'} | "
            f"{r['parking_time'] or '-'} | {money(r['amount'])} | {money(r['paid'])} | {money(r['rest'])} | {r['flags']}"
        )
    if len(rows) > args.limit:
        print(f"... truncated: shown {args.limit} of {len(rows)} rows")
    print("-" * 150)
    print("READ ONLY COMPLETED")
    conn.close()


def main():
    parser = argparse.ArgumentParser(description="OSBB cashier unpaid preview. READ ONLY.")
    parser.add_argument("--period", default="", help="Exact period_code from charges. Empty means all periods.")
    parser.add_argument("--services", nargs="*", default=DEFAULT_SERVICES, help="Service codes, comma or space separated. Default parking/barrier.")
    parser.add_argument("--list-periods", action="store_true", help="List real period_code values from charges and exit.")
    parser.add_argument("--include-paid", action="store_true", help="Show paid charges too.")
    parser.add_argument("--limit", type=int, default=500)
    args = parser.parse_args()

    if args.list_periods:
        list_periods(args)
    else:
        unpaid_preview(args)


if __name__ == "__main__":
    main()
