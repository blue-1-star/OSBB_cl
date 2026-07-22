from pathlib import Path
import sys
import sqlite3
import argparse
import re
from datetime import datetime
from difflib import SequenceMatcher

OSBB_ROOT = Path(__file__).resolve().parent
PY_ROOT = OSBB_ROOT.parent
if str(PY_ROOT) not in sys.path:
    sys.path.insert(0, str(PY_ROOT))

from config import paths, USE_TEST_DB

DEFAULT_PERIOD_CODE = "2026-05_2026-06"

CYR_TO_LAT = str.maketrans({
    "А": "A", "В": "B", "Е": "E", "І": "I", "К": "K",
    "М": "M", "Н": "H", "О": "O", "Р": "P", "С": "C",
    "Т": "T", "Х": "X", "У": "Y",
})


def get_db_file():
    return paths.OSBB_TEST_DB_FILE if USE_TEST_DB else paths.OSBB_DB_FILE


def now_ts():
    return datetime.now().strftime("%Y-%m-%d_%H-%M-%S")


def norm_text(value):
    return "" if value is None else str(value).strip()


def normalize_plate(value):
    text = norm_text(value).upper()
    if not text:
        return ""
    text = text.replace("О", "O")
    if re.fullmatch(r"O\d{3}", text):
        text = "0" + text[1:]
    text = text.translate(CYR_TO_LAT)
    text = re.sub(r"[^A-Z0-9]", "", text)
    if re.fullmatch(r"O\d{3}", text):
        text = "0" + text[1:]
    return text


def digits_only(value):
    return re.sub(r"\D", "", norm_text(value))


def plate_similarity(a, b):
    a = normalize_plate(a)
    b = normalize_plate(b)
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


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


def safe_column_expr(columns, alias, column_name, default="NULL"):
    return f"{alias}.{column_name}" if column_name in columns else default


def load_vehicles(conn):
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("""
        SELECT
            v.id AS vehicle_id,
            a.apartment_number,
            v.license_plate AS license_plate_raw,
            v.license_plate_normalized,
            v.car_model,
            v.car_model_normalized,
            v.parking_time
        FROM vehicles v
        JOIN apartments a ON a.id = v.apartment_id
        ORDER BY a.apartment_number, v.id
    """)
    rows = []
    for row in cur.fetchall():
        plate = normalize_plate(row["license_plate_normalized"] or row["license_plate_raw"])
        rows.append({
            "vehicle_id": row["vehicle_id"],
            "apartment_number": str(row["apartment_number"] or ""),
            "plate": plate,
            "plate_raw": row["license_plate_raw"] or "",
            "model": row["car_model_normalized"] or row["car_model"] or "",
            "parking_time": row["parking_time"] or "",
        })
    return rows


def extract_plate_raw_from_comment(comment):
    m = re.search(r"plate_raw=([^;]*)", comment or "")
    return m.group(1).strip() if m else ""


def load_payments(conn, period_code):
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    payments_table = pick_table(cur, "payments", "service_payments")
    if not payments_table:
        return []
    columns = table_columns(cur, payments_table)
    payment_date_expr = safe_column_expr(columns, "p", "payment_date")
    apartment_expr = safe_column_expr(columns, "p", "apartment_number")
    vehicle_expr = safe_column_expr(columns, "p", "vehicle_id")
    service_expr = safe_column_expr(columns, "p", "service_code")
    source_expr = safe_column_expr(columns, "p", "source")
    source_ref_expr = safe_column_expr(columns, "p", "source_ref")
    comment_expr = safe_column_expr(columns, "p", "comment")
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
            {comment_expr} AS comment
        FROM {payments_table} p
        WHERE p.period_code = ?
        ORDER BY p.id
    """, (period_code,))
    rows = []
    for row in cur.fetchall():
        comment = row["comment"] or ""
        plate_raw = extract_plate_raw_from_comment(comment)
        rows.append({
            "payment_id": row["payment_id"],
            "payment_date": row["payment_date"] or "",
            "period_code": row["period_code"] or "",
            "apartment_number": str(row["apartment_number"] or ""),
            "vehicle_id": row["vehicle_id"],
            "service_code": row["service_code"] or "",
            "amount": float(row["amount"] or 0),
            "source": row["source"] or "",
            "source_ref": row["source_ref"] or "",
            "comment": comment,
            "plate_raw": plate_raw,
            "plate": normalize_plate(plate_raw),
        })
    return rows


def candidate_score(query_plate, vehicle_plate):
    q = normalize_plate(query_plate)
    p = normalize_plate(vehicle_plate)
    if not q or not p:
        return 0, []
    reasons = []
    score = 0
    if q == p:
        return 100, ["exact"]
    if q in p or p in q:
        score += 75
        reasons.append("substring")
    q_digits = digits_only(q)
    p_digits = digits_only(p)
    if q_digits and p_digits:
        if q_digits == p_digits:
            score += 80
            reasons.append("digits_exact")
        elif q_digits in p_digits or p_digits in q_digits:
            score += 60
            reasons.append("digits_substring")
    ratio = plate_similarity(q, p)
    if ratio >= 0.70:
        score += int(ratio * 60)
        reasons.append(f"similar:{ratio:.2f}")
    if abs(len(q) - len(p)) <= 1 and ratio >= 0.70:
        score += 25
        reasons.append("one_char_missing_or_extra")
    if abs(len(q) - len(p)) == 2 and ratio >= 0.75:
        score += 15
        reasons.append("two_chars_difference")
    return min(score, 99), reasons


def find_plate_candidates(query_plate, vehicles, limit=5, min_score=45):
    candidates = []
    for vehicle in vehicles:
        score, reasons = candidate_score(query_plate, vehicle["plate"])
        if score >= min_score:
            item = dict(vehicle)
            item["score"] = score
            item["reason"] = ", ".join(reasons)
            candidates.append(item)
    candidates.sort(key=lambda x: (-x["score"], x["apartment_number"], x["plate"]))
    return candidates[:limit]


def get_vehicle_quality_tasks(period_code=DEFAULT_PERIOD_CODE, limit=100):
    db_file = get_db_file()
    conn = sqlite3.connect(db_file)
    vehicles = load_vehicles(conn)
    payments = load_payments(conn, period_code)
    conn.close()
    tasks = []
    for payment in payments:
        if payment["vehicle_id"]:
            continue
        if not payment["plate"]:
            continue
        if payment["source"] and payment["source"] != "import_ohorona_sheet1":
            continue
        candidates = find_plate_candidates(payment["plate"], vehicles)
        tasks.append({
            "payment_id": payment["payment_id"],
            "payment_date": payment["payment_date"],
            "amount": payment["amount"],
            "source": payment["source"],
            "source_ref": payment["source_ref"],
            "plate_raw": payment["plate_raw"],
            "plate": payment["plate"],
            "apartment_number": payment["apartment_number"],
            "candidates": candidates,
        })
    tasks.sort(key=lambda x: (0 if x["candidates"] else 1, -x["candidates"][0]["score"] if x["candidates"] else 0, x["payment_id"]))
    return tasks[:limit]


def get_vehicle_quality_task_by_payment(payment_id, period_code=DEFAULT_PERIOD_CODE):
    for task in get_vehicle_quality_tasks(period_code=period_code, limit=10000):
        if str(task["payment_id"]) == str(payment_id):
            return task
    return None


def format_vehicle_quality_task(task, index=None, total=None):
    prefix = f"Задача {index}/{total}\n" if index is not None and total is not None else ""
    lines = []
    lines.append(prefix + "🚗 Проверка номера авто")
    lines.append("")
    lines.append(f"Платёж ID: {task['payment_id']}")
    lines.append(f"Дата: {task['payment_date'] or '-'}")
    lines.append(f"Сумма: {task['amount']:g}")
    lines.append(f"Номер из оплаты: {task['plate_raw']} → {task['plate']}")
    lines.append("")
    if not task["candidates"]:
        lines.append("Кандидаты в базе не найдены.")
        lines.append("")
        lines.append("Действия: исправить вручную / оставить без привязки")
        return "\n".join(lines)
    lines.append("Похожие номера в базе:")
    for i, c in enumerate(task["candidates"], start=1):
        lines.append(f"{i}. кв {c['apartment_number']} | {c['plate']} | {c['model'] or '-'} | {c['parking_time'] or '-'} | score={c['score']} | {c['reason']}")
    lines.append("")
    lines.append("Действия: подтвердить кандидата / исправить номер вручную / пропустить")
    return "\n".join(lines)


def apply_vehicle_plate_correction(payment_id, vehicle_id, operator_id=None, comment="vehicle quality correction from payment"):
    db_file = get_db_file()
    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    payments_table = pick_table(cur, "payments", "service_payments")
    if not payments_table:
        conn.close()
        raise RuntimeError("payments table not found")
    payment_cols = table_columns(cur, payments_table)
    cur.execute("""
        SELECT v.id AS vehicle_id, a.apartment_number, v.parking_time,
               COALESCE(v.license_plate_normalized, v.license_plate) AS plate
        FROM vehicles v
        JOIN apartments a ON a.id = v.apartment_id
        WHERE v.id = ?
    """, (vehicle_id,))
    vehicle = cur.fetchone()
    if not vehicle:
        conn.close()
        raise RuntimeError(f"vehicle_id not found: {vehicle_id}")
    service_code = None
    if vehicle["parking_time"] == "Day":
        service_code = "PARKING_DAY"
    elif vehicle["parking_time"] == "Night":
        service_code = "PARKING_NIGHT"
    sets = []
    params = []
    if "vehicle_id" in payment_cols:
        sets.append("vehicle_id = ?")
        params.append(vehicle_id)
    if "apartment_number" in payment_cols:
        sets.append("apartment_number = ?")
        params.append(vehicle["apartment_number"])
    if "service_code" in payment_cols and service_code:
        sets.append("service_code = ?")
        params.append(service_code)
    if "comment" in payment_cols:
        sets.append("comment = COALESCE(comment, '') || ?")
        params.append(f"; correction: linked to vehicle_id={vehicle_id}, plate={vehicle['plate']}, operator={operator_id}, note={comment}")
    if not sets:
        conn.close()
        raise RuntimeError("payments table has no editable columns")
    params.append(payment_id)
    cur.execute(f"UPDATE {payments_table} SET {', '.join(sets)} WHERE id = ?", tuple(params))
    updated = cur.rowcount
    conn.commit()
    conn.close()
    return {"updated_payments": updated, "payment_id": payment_id, "vehicle_id": vehicle_id, "apartment_number": vehicle["apartment_number"], "plate": vehicle["plate"], "service_code": service_code}


def write_report(report_file, tasks, period_code):
    lines = []
    lines.append("=" * 110)
    lines.append("VEHICLE DATA QUALITY TASKS")
    lines.append("=" * 110)
    lines.append(f"Generated : {datetime.now():%Y-%m-%d %H:%M:%S}")
    lines.append(f"DB        : {get_db_file()}")
    lines.append(f"MODE      : {'TEST/WORK' if USE_TEST_DB else 'PROD'}")
    lines.append(f"Period    : {period_code}")
    lines.append("")
    with_candidates = [t for t in tasks if t["candidates"]]
    without_candidates = [t for t in tasks if not t["candidates"]]
    lines.append("=" * 110)
    lines.append("SUMMARY")
    lines.append("=" * 110)
    lines.append(f"Tasks total      : {len(tasks)}")
    lines.append(f"With candidates  : {len(with_candidates)}")
    lines.append(f"No candidates    : {len(without_candidates)}")
    lines.append("")
    for idx, task in enumerate(tasks, start=1):
        lines.append("-" * 110)
        lines.append(format_vehicle_quality_task(task, index=idx, total=len(tasks)))
        lines.append("")
    report_file.write_text("\n".join(lines), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description="Find vehicle plate quality tasks from unlinked payments.")
    parser.add_argument("--period", default=DEFAULT_PERIOD_CODE)
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()
    tasks = get_vehicle_quality_tasks(period_code=args.period, limit=args.limit)
    report_dir = paths.OSBB_EXPORTS_DIR / "billing"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_file = report_dir / f"vehicle_data_quality_tasks_{args.period}_{now_ts()}.txt"
    write_report(report_file, tasks, args.period)
    print("=" * 70)
    print("VEHICLE DATA QUALITY TASKS")
    print("=" * 70)
    print("DB:", get_db_file())
    print("MODE:", "TEST/WORK" if USE_TEST_DB else "PROD")
    print("Period:", args.period)
    print("Tasks:", len(tasks))
    print("With candidates:", len([t for t in tasks if t["candidates"]]))
    print("No candidates:", len([t for t in tasks if not t["candidates"]]))
    print("")
    print("Report:")
    print(report_file)
    print("=" * 70)


if __name__ == "__main__":
    main()
