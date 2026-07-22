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


def get_quarantine_db_file():
    return getattr(paths, "OSBB_QUARANTINE_DB_FILE", paths.OSBB_DB_DIR / "osbb_quarantine.db")


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


def apt_key(value):
    text = norm_text(value)

    if text.endswith(".0"):
        text = text[:-2]

    return text


def apt_sort_key(apt):
    s = apt_key(apt)
    return (0, int(s)) if s.isdigit() else (1, s)


def table_exists(cur, table_name):
    cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    )
    return cur.fetchone() is not None


def table_columns(cur, table_name):
    cur.execute(f"PRAGMA table_info({table_name})")
    return [row[1] for row in cur.fetchall()]


def pick_col(columns, *names):
    normalized = {c.lower(): c for c in columns}

    for name in names:
        if name in columns:
            return name
        if name.lower() in normalized:
            return normalized[name.lower()]

    return None


def plate_similarity(a, b):
    a = normalize_plate(a)
    b = normalize_plate(b)

    if not a or not b:
        return 0

    return SequenceMatcher(None, a, b).ratio()


def candidate_score(query_plate, db_plate):
    q = normalize_plate(query_plate)
    p = normalize_plate(db_plate)

    if not q or not p:
        return 0, []

    if q == p:
        return 100, ["exact"]

    score = 0
    reasons = []

    q_digits = digits_only(q)
    p_digits = digits_only(p)

    if q in p or p in q:
        score += 70
        reasons.append("substring")

    if q_digits and p_digits:
        if q_digits == p_digits:
            score += 85
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


def load_main_vehicles(conn):
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("""
        SELECT
            v.id AS vehicle_id,
            a.apartment_number,
            v.license_plate AS plate_raw,
            v.license_plate_normalized,
            v.car_model,
            v.car_model_normalized,
            v.parking_time
        FROM vehicles v
        JOIN apartments a
            ON a.id = v.apartment_id
        ORDER BY a.apartment_number, v.id
    """)

    result = []

    for row in cur.fetchall():
        plate = normalize_plate(row["license_plate_normalized"] or row["plate_raw"])
        result.append({
            "source": "main_db",
            "vehicle_id": row["vehicle_id"],
            "apartment_number": apt_key(row["apartment_number"]),
            "plate_raw": row["plate_raw"] or "",
            "plate": plate,
            "model": row["car_model_normalized"] or row["car_model"] or "",
            "parking_time": row["parking_time"] or "",
        })

    return result


def load_tbot_vehicles():
    qdb = get_quarantine_db_file()

    if not qdb.exists():
        return []

    conn = sqlite3.connect(qdb)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    if not table_exists(cur, "tbot_parking_import"):
        conn.close()
        return []

    cols = table_columns(cur, "tbot_parking_import")

    apt_col = pick_col(cols, "apartment_number", "Номер квартири", "Квартира", "Власність")
    plate_col = pick_col(cols, "license_plate_normalized", "license_plate", "Номер Авто", "Номер авто", "Plate")
    model_col = pick_col(cols, "car_model_normalized", "car_model", "Марка авто")
    phone_col = pick_col(cols, "phone_normalized", "phone_number", "Телефон")
    name_col = pick_col(cols, "owner_name", "ПІБ", "ФИО", "ПИБ", "name")
    status_col = pick_col(cols, "Статус", "status")

    select_parts = [
        f"{apt_col if apt_col else 'NULL'} AS apartment_number",
        f"{plate_col if plate_col else 'NULL'} AS plate_raw",
        f"{model_col if model_col else 'NULL'} AS model",
        f"{phone_col if phone_col else 'NULL'} AS phone",
        f"{name_col if name_col else 'NULL'} AS owner_name",
        f"{status_col if status_col else 'NULL'} AS status",
    ]

    cur.execute(f"""
        SELECT {", ".join(select_parts)}
        FROM tbot_parking_import
    """)

    result = []

    for row in cur.fetchall():
        plate = normalize_plate(row["plate_raw"])
        if not plate:
            continue

        result.append({
            "source": "tbot",
            "apartment_number": apt_key(row["apartment_number"]),
            "plate_raw": row["plate_raw"] or "",
            "plate": plate,
            "model": row["model"] or "",
            "phone": row["phone"] or "",
            "owner_name": row["owner_name"] or "",
            "status": row["status"] or "",
        })

    conn.close()
    return result


def load_telegram_fact_plates(conn):
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    candidates = []

    for table_name in ["telegram_facts", "telegram_message_facts", "message_facts"]:
        if not table_exists(cur, table_name):
            continue

        cols = table_columns(cur, table_name)
        plate_col = pick_col(cols, "license_plate_normalized", "license_plate", "plate", "plate_raw")
        apt_col = pick_col(cols, "apartment_number", "apt")
        msg_col = pick_col(cols, "msg_id", "message_id")
        chat_col = pick_col(cols, "chat", "chat_title", "sender_name")
        text_col = pick_col(cols, "raw_text", "text", "message_text")

        if not plate_col:
            continue

        select_parts = [
            f"{plate_col} AS plate_raw",
            f"{apt_col if apt_col else 'NULL'} AS apartment_number",
            f"{msg_col if msg_col else 'NULL'} AS msg_id",
            f"{chat_col if chat_col else 'NULL'} AS chat",
            f"{text_col if text_col else 'NULL'} AS text",
        ]

        cur.execute(f"SELECT {', '.join(select_parts)} FROM {table_name}")

        for row in cur.fetchall():
            plate = normalize_plate(row["plate_raw"])

            if not plate:
                continue

            candidates.append({
                "source": table_name,
                "apartment_number": apt_key(row["apartment_number"]),
                "plate_raw": row["plate_raw"] or "",
                "plate": plate,
                "msg_id": row["msg_id"] or "",
                "chat": row["chat"] or "",
                "text": row["text"] or "",
            })

    return candidates


def load_payment_plates(conn, period_code):
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    payments_table = None
    for name in ["payments", "service_payments"]:
        if table_exists(cur, name):
            payments_table = name
            break

    if not payments_table:
        return []

    cols = table_columns(cur, payments_table)
    comment_col = pick_col(cols, "comment")
    apt_col = pick_col(cols, "apartment_number")
    amount_col = pick_col(cols, "amount")
    date_col = pick_col(cols, "payment_date")
    vehicle_col = pick_col(cols, "vehicle_id")
    source_col = pick_col(cols, "source")

    select_parts = [
        "id AS payment_id",
        f"{comment_col if comment_col else 'NULL'} AS comment",
        f"{apt_col if apt_col else 'NULL'} AS apartment_number",
        f"{amount_col if amount_col else 'NULL'} AS amount",
        f"{date_col if date_col else 'NULL'} AS payment_date",
        f"{vehicle_col if vehicle_col else 'NULL'} AS vehicle_id",
        f"{source_col if source_col else 'NULL'} AS source",
    ]

    cur.execute(f"""
        SELECT {", ".join(select_parts)}
        FROM {payments_table}
        WHERE period_code = ?
    """, (period_code,))

    result = []

    for row in cur.fetchall():
        if row["vehicle_id"]:
            continue

        comment = row["comment"] or ""
        m = re.search(r"plate_raw=([^;]*)", comment)
        plate_raw = m.group(1).strip() if m else ""

        plate = normalize_plate(plate_raw)
        if not plate:
            continue

        result.append({
            "source": "payments",
            "payment_id": row["payment_id"],
            "apartment_number": apt_key(row["apartment_number"]),
            "plate_raw": plate_raw,
            "plate": plate,
            "amount": row["amount"] or "",
            "payment_date": row["payment_date"] or "",
            "source_name": row["source"] or "",
        })

    return result


def best_candidates(plate, main_vehicles, limit=5, min_score=45):
    result = []

    for v in main_vehicles:
        score, reasons = candidate_score(plate, v["plate"])

        if score >= min_score:
            item = dict(v)
            item["score"] = score
            item["reason"] = ", ".join(reasons)
            result.append(item)

    result.sort(key=lambda x: (-x["score"], apt_sort_key(x["apartment_number"]), x["plate"]))
    return result[:limit]


def exact_plate_exists(plate, main_vehicles):
    plate = normalize_plate(plate)
    return any(v["plate"] == plate for v in main_vehicles)


def build_tasks(period_code=DEFAULT_PERIOD_CODE, include_telegram=True, include_tbot=True, include_payments=True):
    db_file = get_db_file()
    conn = sqlite3.connect(db_file)

    main_vehicles = load_main_vehicles(conn)

    external = []

    if include_tbot:
        external.extend(load_tbot_vehicles())

    if include_telegram:
        external.extend(load_telegram_fact_plates(conn))

    if include_payments:
        external.extend(load_payment_plates(conn, period_code))

    conn.close()

    tasks = []
    seen = set()

    for item in external:
        plate = item["plate"]

        if not plate:
            continue

        # Главная цель: всё, что есть во внешних источниках, но нет точного совпадения в бумажной/main базе.
        if exact_plate_exists(plate, main_vehicles):
            continue

        key = (
            item.get("source"),
            item.get("plate"),
            item.get("apartment_number"),
            item.get("payment_id", ""),
            item.get("msg_id", ""),
        )

        if key in seen:
            continue

        seen.add(key)

        candidates = best_candidates(plate, main_vehicles)

        tasks.append({
            "source": item.get("source"),
            "apartment_number": item.get("apartment_number", ""),
            "plate_raw": item.get("plate_raw", ""),
            "plate": plate,
            "model": item.get("model", ""),
            "owner_name": item.get("owner_name", ""),
            "phone": item.get("phone", ""),
            "status": item.get("status", ""),
            "payment_id": item.get("payment_id", ""),
            "amount": item.get("amount", ""),
            "payment_date": item.get("payment_date", ""),
            "msg_id": item.get("msg_id", ""),
            "chat": item.get("chat", ""),
            "candidates": candidates,
        })

    tasks.sort(
        key=lambda x: (
            0 if x["candidates"] else 1,
            -x["candidates"][0]["score"] if x["candidates"] else 0,
            str(x["source"]),
            apt_sort_key(x["apartment_number"]),
            x["plate"],
        )
    )

    return tasks


def format_task(task, idx=None, total=None):
    lines = []

    if idx is not None and total is not None:
        lines.append(f"Задача {idx}/{total}")

    lines.append("🚗 Верификация авто")
    lines.append(f"Источник: {task['source']}")
    lines.append(f"Квартира: {task['apartment_number'] or '-'}")
    lines.append(f"Номер из источника: {task['plate_raw']} → {task['plate']}")

    if task.get("model"):
        lines.append(f"Марка: {task['model']}")

    if task.get("owner_name") or task.get("phone"):
        lines.append(f"Контакт: {task.get('owner_name') or '-'} | {task.get('phone') or '-'}")

    if task.get("payment_id"):
        lines.append(f"Платёж: id={task['payment_id']} | {task.get('payment_date') or '-'} | {task.get('amount') or '-'} грн")

    if task.get("msg_id"):
        lines.append(f"Telegram: msg_id={task['msg_id']} | chat={task.get('chat') or '-'}")

    lines.append("")

    if task["candidates"]:
        lines.append("Похожие номера в main/бумажной базе:")
        for i, c in enumerate(task["candidates"], start=1):
            lines.append(
                f"{i}. кв {c['apartment_number']} | {c['plate']} | "
                f"{c['model'] or '-'} | {c['parking_time'] or '-'} | "
                f"score={c['score']} | {c['reason']}"
            )
        lines.append("")
        lines.append("Действия: подтвердить / исправить номер / добавить новое авто / пропустить")
    else:
        lines.append("Кандидаты в main/бумажной базе не найдены.")
        lines.append("Действия: добавить новое авто / исправить вручную / пропустить")

    return "\n".join(lines)


def write_report(report_file, tasks, period_code):
    lines = []
    lines.append("=" * 120)
    lines.append("VEHICLE VERIFICATION TASKS — BROAD MODE")
    lines.append("=" * 120)
    lines.append(f"Generated : {datetime.now():%Y-%m-%d %H:%M:%S}")
    lines.append(f"DB        : {get_db_file()}")
    lines.append(f"QDB       : {get_quarantine_db_file()}")
    lines.append(f"MODE      : {'TEST/WORK' if USE_TEST_DB else 'PROD'}")
    lines.append(f"Period    : {period_code}")
    lines.append("")

    by_source = {}
    with_candidates = 0

    for task in tasks:
        by_source[task["source"]] = by_source.get(task["source"], 0) + 1
        if task["candidates"]:
            with_candidates += 1

    lines.append("=" * 120)
    lines.append("SUMMARY")
    lines.append("=" * 120)
    lines.append(f"Tasks total     : {len(tasks)}")
    lines.append(f"With candidates : {with_candidates}")
    lines.append(f"No candidates   : {len(tasks) - with_candidates}")

    for source, count in sorted(by_source.items()):
        lines.append(f"{source:16}: {count}")

    lines.append("")

    for idx, task in enumerate(tasks, start=1):
        lines.append("-" * 120)
        lines.append(format_task(task, idx, len(tasks)))
        lines.append("")

    report_file.write_text("\n".join(lines), encoding="utf-8")


# Bot-ready aliases
get_vehicle_verification_tasks = build_tasks
format_vehicle_verification_task = format_task


def main():
    parser = argparse.ArgumentParser(
        description="Broad vehicle verification tasks: tbot/messages/payments vs main vehicles."
    )
    parser.add_argument("--period", default=DEFAULT_PERIOD_CODE)
    parser.add_argument("--no-telegram", action="store_true")
    parser.add_argument("--no-tbot", action="store_true")
    parser.add_argument("--no-payments", action="store_true")

    args = parser.parse_args()

    tasks = build_tasks(
        period_code=args.period,
        include_telegram=not args.no_telegram,
        include_tbot=not args.no_tbot,
        include_payments=not args.no_payments,
    )

    report_dir = paths.OSBB_EXPORTS_DIR / "billing"
    report_dir.mkdir(parents=True, exist_ok=True)

    report_file = report_dir / f"vehicle_verification_tasks_{args.period}_{now_ts()}.txt"
    write_report(report_file, tasks, args.period)

    print("=" * 70)
    print("VEHICLE VERIFICATION TASKS — BROAD MODE")
    print("=" * 70)
    print("DB:", get_db_file())
    print("QDB:", get_quarantine_db_file())
    print("MODE:", "TEST/WORK" if USE_TEST_DB else "PROD")
    print("Tasks:", len(tasks))
    print("With candidates:", len([t for t in tasks if t["candidates"]]))
    print("No candidates:", len([t for t in tasks if not t["candidates"]]))
    print("")
    print("Report:")
    print(report_file)
    print("=" * 70)


if __name__ == "__main__":
    main()
