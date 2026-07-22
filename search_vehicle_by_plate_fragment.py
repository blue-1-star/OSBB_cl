from pathlib import Path
import sys
import sqlite3
import argparse
import re
from difflib import SequenceMatcher

OSBB_ROOT = Path(__file__).resolve().parent
PY_ROOT = OSBB_ROOT.parent

if str(PY_ROOT) not in sys.path:
    sys.path.insert(0, str(PY_ROOT))

from config import paths, USE_TEST_DB


CYR_TO_LAT = str.maketrans({
    "А": "A", "В": "B", "Е": "E", "І": "I", "К": "K",
    "М": "M", "Н": "H", "О": "O", "Р": "P", "С": "C",
    "Т": "T", "Х": "X", "У": "Y",
})


def get_db_file():
    return paths.OSBB_TEST_DB_FILE if USE_TEST_DB else paths.OSBB_DB_FILE


def norm_text(value):
    return "" if value is None else str(value).strip()


def normalize_plate(value):
    text = norm_text(value).upper()

    if not text:
        return ""

    text = text.replace("О", "O")

    # Спецслучай из Охорона.xlsx: O186 / О186 = 0186
    if re.fullmatch(r"O\d{3}", text):
        text = "0" + text[1:]

    text = text.translate(CYR_TO_LAT)
    text = re.sub(r"[^A-Z0-9]", "", text)

    if re.fullmatch(r"O\d{3}", text):
        text = "0" + text[1:]

    return text


def digits_only(value):
    return re.sub(r"\D", "", norm_text(value))


def table_columns(cur, table_name):
    cur.execute(f"PRAGMA table_info({table_name})")
    return [row[1] for row in cur.fetchall()]


def pick_column(columns, *names):
    for name in names:
        if name in columns:
            return name
    return None


def load_vehicle_contact_rows(conn):
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    contact_join = ""
    select_contact = "NULL AS owner_name, NULL AS phone_number"

    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='contacts'")
    if cur.fetchone():
        cols = table_columns(cur, "contacts")
        apartment_id_col = pick_column(cols, "apartment_id")
        name_col = pick_column(cols, "owner_name", "name", "full_name", "contact_name")
        phone_col = pick_column(cols, "phone_number", "phone", "phone_normalized")

        if apartment_id_col:
            contact_join = f"LEFT JOIN contacts c ON c.{apartment_id_col} = a.id"
            select_contact = (
                f"{'c.' + name_col if name_col else 'NULL'} AS owner_name, "
                f"{'c.' + phone_col if phone_col else 'NULL'} AS phone_number"
            )

    cur.execute(f"""
        SELECT
            v.id AS vehicle_id,
            a.apartment_number,
            v.license_plate AS license_plate_raw,
            v.license_plate_normalized,
            v.car_model,
            v.car_model_normalized,
            v.parking_time,
            {select_contact}
        FROM vehicles v
        JOIN apartments a
            ON a.id = v.apartment_id
        {contact_join}
        ORDER BY
            CASE
                WHEN a.apartment_number GLOB '[0-9]*'
                THEN CAST(a.apartment_number AS INTEGER)
                ELSE 999999
            END,
            a.apartment_number,
            v.id
    """)

    rows = []
    seen = set()

    for row in cur.fetchall():
        plate = normalize_plate(row["license_plate_normalized"] or row["license_plate_raw"])
        key = (row["vehicle_id"], row["apartment_number"], plate, row["owner_name"], row["phone_number"])

        if key in seen:
            continue

        seen.add(key)

        rows.append({
            "vehicle_id": row["vehicle_id"],
            "apartment_number": row["apartment_number"],
            "plate": plate,
            "plate_raw": row["license_plate_raw"],
            "model": row["car_model_normalized"] or row["car_model"] or "",
            "parking_time": row["parking_time"] or "",
            "owner_name": row["owner_name"] or "",
            "phone_number": row["phone_number"] or "",
        })

    return rows


def score_match(query, plate):
    if not query or not plate:
        return 0, []

    q = normalize_plate(query)
    p = normalize_plate(plate)

    reasons = []
    score = 0

    if q == p:
        return 100, ["exact"]

    if q in p:
        score += 80
        reasons.append("substring")

    q_digits = digits_only(q)
    p_digits = digits_only(p)

    if q_digits and q_digits == p_digits:
        score += 75
        reasons.append("digits_exact")
    elif q_digits and q_digits in p_digits:
        score += 65
        reasons.append("digits_substring")

    ratio = SequenceMatcher(None, q, p).ratio()
    if ratio >= 0.75:
        score += int(ratio * 50)
        reasons.append(f"similar:{ratio:.2f}")

    if abs(len(q) - len(p)) <= 1 and ratio >= 0.70:
        score += 20
        reasons.append("one_char_missing_or_extra")

    return min(score, 99), reasons


def search_plate_fragment(fragment, min_score=40, limit=30):
    db_file = get_db_file()
    conn = sqlite3.connect(db_file)

    rows = load_vehicle_contact_rows(conn)
    conn.close()

    results = []

    for row in rows:
        score, reasons = score_match(fragment, row["plate"])

        if score >= min_score:
            item = dict(row)
            item["score"] = score
            item["reasons"] = ", ".join(reasons)
            results.append(item)

    results.sort(key=lambda x: (-x["score"], str(x["apartment_number"]), str(x["plate"])))
    return results[:limit]


def format_results(fragment, results):
    lines = []
    lines.append("=" * 110)
    lines.append(f"PLATE FRAGMENT SEARCH: {fragment}")
    lines.append("=" * 110)
    lines.append("score | apt | plate | model | parking | owner | phone | reason")
    lines.append("-" * 110)

    if not results:
        lines.append("Ничего не найдено.")
        return "\n".join(lines)

    for item in results:
        lines.append(
            f"{item['score']:>5} | "
            f"{item['apartment_number']} | "
            f"{item['plate']} | "
            f"{item['model']} | "
            f"{item['parking_time'] or '-'} | "
            f"{item['owner_name'] or '-'} | "
            f"{item['phone_number'] or '-'} | "
            f"{item['reasons']}"
        )

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Search vehicle by full/partial license plate fragment."
    )
    parser.add_argument("fragment", help="Номер или кусок номера: 0186, O186, 5003, AI8756HK, ...")
    parser.add_argument("--min-score", type=int, default=40)
    parser.add_argument("--limit", type=int, default=30)

    args = parser.parse_args()

    results = search_plate_fragment(args.fragment, min_score=args.min_score, limit=args.limit)
    print(format_results(args.fragment, results))


if __name__ == "__main__":
    main()
