from pathlib import Path
import sys
import sqlite3
import argparse
import re
from datetime import datetime
from difflib import SequenceMatcher
from collections import defaultdict, Counter

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


def apt_key(value):
    text = norm_text(value)
    return text[:-2] if text.endswith(".0") else text


def apt_sort_key(value):
    text = apt_key(value)
    return (0, int(text)) if text.isdigit() else (1, text)


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


def is_full_ua_plate(plate):
    plate = normalize_plate(plate)
    return re.fullmatch(r"[A-Z]{2}\d{4}[A-Z]{2}", plate) is not None


def is_short_missing_second_letter(short_plate, full_plate):
    s = normalize_plate(short_plate)
    f = normalize_plate(full_plate)

    if len(s) != 7 or not is_full_ua_plate(f):
        return False

    if digits_only(s) != digits_only(f):
        return False

    variants = [
        f[0] + f[2:],
        f[1:],
    ]

    return s in variants


def is_obviously_fuller_plate(candidate, other):
    return is_full_ua_plate(candidate) and is_short_missing_second_letter(other, candidate)


def same_digits(candidate, other):
    cd = digits_only(candidate)
    od = digits_only(other)
    return cd and od and cd == od


def common_chars_same_positions(a, b):
    """
    Counts equal characters at the same positions.
    This is intentionally simple and conservative.
    """
    a = normalize_plate(a)
    b = normalize_plate(b)
    return sum(1 for x, y in zip(a, b) if x == y)


def is_truncated_support_for_full_plate(candidate, other):
    """
    Broader conservative rule.

    candidate: full UA plate LLDDDDLL
    other: shortened/paper/payment variant, 4-7 symbols

    Examples accepted:
      5003     -> AA5003MX
      9868     -> AA9868KC
      KA6739E  -> KA6739IE
      AA5200H  -> AA5200HI
      A8756HK  -> AI8756HK

    Not accepted:
      AM1317HT -> AM1317MT
    because both are full valid plates.
    """
    c = normalize_plate(candidate)
    o = normalize_plate(other)

    if not is_full_ua_plate(c):
        return False

    if is_full_ua_plate(o):
        return False

    if not (4 <= len(o) <= 7):
        return False

    cd = digits_only(c)
    od = digits_only(o)

    if not cd or not od:
        return False

    # Strongest case: shortened value is only 4 digits.
    if re.fullmatch(r"\d{4}", o):
        return od == cd

    # General case: all digits match.
    if od != cd:
        return False

    # Need some letter/position evidence as well.
    return common_chars_same_positions(c, o) >= 4


def is_full_plate_supported_by_shorter(candidate, other):
    return (
        is_obviously_fuller_plate(candidate, other)
        or is_truncated_support_for_full_plate(candidate, other)
    )


def table_exists(cur, table_name):
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table_name,))
    return cur.fetchone() is not None


def table_columns(cur, table_name):
    cur.execute(f"PRAGMA table_info({table_name})")
    return [row[1] for row in cur.fetchall()]


def pick_col(columns, *names):
    lower_map = {c.lower(): c for c in columns}
    for name in names:
        if name in columns:
            return name
        if name.lower() in lower_map:
            return lower_map[name.lower()]
    return None


def similarity(a, b):
    a = normalize_plate(a)
    b = normalize_plate(b)
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def plate_match_score(a, b):
    a = normalize_plate(a)
    b = normalize_plate(b)
    if not a or not b:
        return 0
    if a == b:
        return 100
    score = 0
    ad = digits_only(a)
    bd = digits_only(b)
    if a in b or b in a:
        score += 65
    if ad and bd:
        if ad == bd:
            score += 80
        elif ad in bd or bd in ad:
            score += 55
    ratio = similarity(a, b)
    if ratio >= 0.70:
        score += int(ratio * 60)
    if abs(len(a) - len(b)) <= 1 and ratio >= 0.70:
        score += 20
    return min(score, 99)


def make_fact(source, apartment_number, plate_raw, model="", note="", ref_id=""):
    plate = normalize_plate(plate_raw)
    if not plate:
        return None
    return {
        "source": source,
        "apartment_number": apt_key(apartment_number),
        "plate_raw": norm_text(plate_raw),
        "plate": plate,
        "model": norm_text(model),
        "note": norm_text(note),
        "ref_id": norm_text(ref_id),
    }


def load_main_facts(conn):
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("""
        SELECT v.id AS vehicle_id, a.apartment_number,
               v.license_plate AS plate_raw, v.license_plate_normalized,
               v.car_model, v.car_model_normalized, v.parking_time
        FROM vehicles v
        JOIN apartments a ON a.id = v.apartment_id
        ORDER BY a.apartment_number, v.id
    """)
    facts = []
    for row in cur.fetchall():
        plate_value = row["license_plate_normalized"] or row["plate_raw"]
        note = f"vehicle_id={row['vehicle_id']}; tariff={row['parking_time'] or ''}"
        fact = make_fact("paper", row["apartment_number"], plate_value,
                         row["car_model_normalized"] or row["car_model"] or "", note, str(row["vehicle_id"]))
        if fact:
            facts.append(fact)
    return facts


def load_tbot_facts():
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
    cur.execute(f"SELECT {', '.join(select_parts)} FROM tbot_parking_import")
    facts = []
    for row in cur.fetchall():
        note = f"{row['owner_name'] or ''}; {row['phone'] or ''}; status={row['status'] or ''}"
        fact = make_fact("tbot", row["apartment_number"], row["plate_raw"], row["model"], note)
        if fact:
            facts.append(fact)
    conn.close()
    return facts


def extract_plate_raw_from_comment(comment):
    m = re.search(r"plate_raw=([^;]*)", comment or "")
    return m.group(1).strip() if m else ""


def load_payment_facts(conn, period_code):
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
    cur.execute(f"SELECT {', '.join(select_parts)} FROM {payments_table} WHERE period_code = ?", (period_code,))
    facts = []
    for row in cur.fetchall():
        plate_raw = extract_plate_raw_from_comment(row["comment"] or "")
        if not plate_raw:
            continue
        note = f"payment_id={row['payment_id']}; date={row['payment_date'] or ''}; amount={row['amount'] or ''}; source={row['source'] or ''}; vehicle_id={row['vehicle_id'] or ''}"
        fact = make_fact("payments", row["apartment_number"], plate_raw, "", note, str(row["payment_id"]))
        if fact:
            facts.append(fact)
    return facts


def load_telegram_facts(conn):
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    facts = []
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
            note = f"table={table_name}; msg_id={row['msg_id'] or ''}; chat={row['chat'] or ''}; {row['text'] or ''}"
            fact = make_fact("telegram", row["apartment_number"], row["plate_raw"], "", note, str(row["msg_id"] or ""))
            if fact:
                facts.append(fact)
    return facts


def load_video_facts(conn):
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    facts = []
    possible_tables = [
        "video_vehicles", "video_plate_observations", "parking_video_vehicles",
        "ocr_plate_observations", "verification_candidates",
    ]
    for table_name in possible_tables:
        if not table_exists(cur, table_name):
            continue
        cols = table_columns(cur, table_name)
        plate_col = pick_col(cols, "license_plate_normalized", "license_plate", "plate", "plate_candidate", "ocr_plate")
        apt_col = pick_col(cols, "apartment_number", "apt")
        model_col = pick_col(cols, "car_model", "model", "car_model_normalized")
        video_col = pick_col(cols, "video_file", "source_video", "file_name")
        ts_col = pick_col(cols, "timestamp", "frame_time", "observed_at", "video_time")
        score_col = pick_col(cols, "score", "confidence", "ocr_similarity_bonus")
        if not plate_col:
            continue
        select_parts = [
            f"{plate_col} AS plate_raw",
            f"{apt_col if apt_col else 'NULL'} AS apartment_number",
            f"{model_col if model_col else 'NULL'} AS model",
            f"{video_col if video_col else 'NULL'} AS video_file",
            f"{ts_col if ts_col else 'NULL'} AS timestamp",
            f"{score_col if score_col else 'NULL'} AS score",
        ]
        cur.execute(f"SELECT {', '.join(select_parts)} FROM {table_name}")
        for row in cur.fetchall():
            note = f"table={table_name}; video={row['video_file'] or ''}; time={row['timestamp'] or ''}; score={row['score'] or ''}"
            fact = make_fact("video", row["apartment_number"], row["plate_raw"], row["model"], note)
            if fact:
                facts.append(fact)
    return facts


def all_facts(period_code):
    conn = sqlite3.connect(get_db_file())
    facts = []
    facts.extend(load_main_facts(conn))
    facts.extend(load_payment_facts(conn, period_code))
    facts.extend(load_telegram_facts(conn))
    facts.extend(load_video_facts(conn))
    facts.extend(load_tbot_facts())
    conn.close()
    return facts


def group_related_facts(facts, min_score=75):
    by_apt = defaultdict(list)
    for fact in facts:
        by_apt[fact["apartment_number"] or "__NO_APT__"].append(fact)
    groups = []
    for apt, apt_facts in by_apt.items():
        if apt == "__NO_APT__":
            continue
        local_groups = []
        for fact in apt_facts:
            placed = False
            for group in local_groups:
                if any(plate_match_score(fact["plate"], other["plate"]) >= min_score for other in group):
                    group.append(fact)
                    placed = True
                    break
            if not placed:
                local_groups.append([fact])
        groups.extend(local_groups)
    for fact in by_apt.get("__NO_APT__", []):
        best = None
        best_score = 0
        for group in groups:
            score = max(plate_match_score(fact["plate"], other["plate"]) for other in group)
            if score > best_score:
                best_score = score
                best = group
        if best is not None and best_score >= min_score:
            best.append(fact)
        else:
            groups.append([fact])
    return groups


def summarize_group(group):
    source_by_plate = defaultdict(set)
    facts_by_plate = defaultdict(list)

    for fact in group:
        source_by_plate[fact["plate"]].add(fact["source"])
        facts_by_plate[fact["plate"]].append(fact)

    vote_rows = []

    for plate, sources in source_by_plate.items():
        vote_rows.append({
            "plate": plate,
            "votes": len(sources),
            "effective_votes": len(sources),
            "sources": sorted(sources),
            "facts": facts_by_plate[plate],
            "rule_notes": [],
        })

    # Rule: malformed paper plate vs valid full external plate.
    # Example: paper A8756HK, payments AI8756HK.
    for row in vote_rows:
        plate = row["plate"]
        for other in vote_rows:
            if other is row:
                continue
            other_plate = other["plate"]
            if is_full_plate_supported_by_shorter(plate, other_plate):
                row["effective_votes"] += 1
                row["rule_notes"].append(
                    f"bonus: {other_plate} supports full plate {plate}"
                )
                other["rule_notes"].append(
                    f"weakened: looks like shortened/incomplete variant of {plate}"
                )

    vote_rows.sort(key=lambda x: (-x["effective_votes"], -x["votes"], x["plate"]))

    top = vote_rows[0] if vote_rows else None
    second = vote_rows[1] if len(vote_rows) > 1 else None

    if not top:
        status = "empty"
        recommendation = ""
        recommendation_reason = ""
    elif second is None:
        status = "single_variant"
        recommendation = top["plate"]
        recommendation_reason = "единственный вариант"
    elif top["effective_votes"] > second["effective_votes"]:
        status = "majority"
        recommendation = top["plate"]
        if top["effective_votes"] > top["votes"]:
            recommendation_reason = "полный номер предпочтительнее: другой вариант похож на усечённую/неполную запись"
        else:
            recommendation_reason = "большинство источников"
    else:
        status = "tie"
        recommendation = ""
        recommendation_reason = "равенство голосов"

    apartments = sorted({f["apartment_number"] for f in group if f["apartment_number"]}, key=apt_sort_key)
    models = sorted({f["model"] for f in group if f["model"]})

    return {
        "apartments": apartments,
        "models": models,
        "vote_rows": vote_rows,
        "status": status,
        "recommendation": recommendation,
        "recommendation_reason": recommendation_reason,
        "total_facts": len(group),
    }


def build_consensus(period_code=DEFAULT_PERIOD_CODE, include_single=False):
    groups = group_related_facts(all_facts(period_code))
    result = []
    for group in groups:
        summary = summarize_group(group)
        if not include_single and len(summary["vote_rows"]) <= 1:
            continue
        result.append(summary)
    status_order = {"majority": 0, "tie": 1, "single_variant": 2, "empty": 9}
    result.sort(key=lambda x: (status_order.get(x["status"], 8),
                               apt_sort_key(x["apartments"][0] if x["apartments"] else ""),
                               x["recommendation"] or x["vote_rows"][0]["plate"]))
    return result


def format_consensus_item(item, index=None):
    lines = []
    lines.append(f"🚗 Consensus #{index}" if index is not None else "🚗 Consensus")
    if item["apartments"]:
        lines.append(f"Квартира: {', '.join(item['apartments'])}")
    if item["models"]:
        lines.append(f"Марка/модель: {', '.join(item['models'][:3])}")
    lines.append("")
    lines.append("Варианты номера:")
    for row in item["vote_rows"]:
        if row.get("effective_votes", row["votes"]) != row["votes"]:
            vote_text = f"{row['votes']} голос(а), вес {row['effective_votes']}"
        else:
            vote_text = f"{row['votes']} голос(а)"
        lines.append(f"  {row['plate']} — {vote_text}: {', '.join(row['sources'])}")
        for note_text in row.get("rule_notes", []):
            lines.append(f"    * {note_text}")
        for fact in row["facts"]:
            note = fact["note"]
            if len(note) > 120:
                note = note[:117] + "..."
            lines.append(f"    - {fact['source']}: apt={fact['apartment_number'] or '-'} raw={fact['plate_raw']} model={fact['model'] or '-'} {note}")
    lines.append("")
    if item["status"] == "majority":
        lines.append(f"Рекомендация: {item['recommendation']} — {item.get('recommendation_reason') or 'большинство источников'}")
    elif item["status"] == "tie":
        lines.append("Рекомендация: нет — равенство голосов, нужна ручная проверка / видео")
    elif item["status"] == "single_variant":
        lines.append(f"Рекомендация: {item['recommendation']} — {item.get('recommendation_reason') or 'единственный вариант'}")
    else:
        lines.append("Рекомендация: нет")
    return "\n".join(lines)


def write_report(report_file, consensus, period_code):
    lines = []
    lines.append("=" * 120)
    lines.append("PLATE CONSENSUS REPORT")
    lines.append("=" * 120)
    lines.append(f"Generated : {datetime.now():%Y-%m-%d %H:%M:%S}")
    lines.append(f"DB        : {get_db_file()}")
    lines.append(f"QDB       : {get_quarantine_db_file()}")
    lines.append(f"MODE      : {'TEST/WORK' if USE_TEST_DB else 'PROD'}")
    lines.append(f"Period    : {period_code}")
    lines.append("")
    counts = Counter(item["status"] for item in consensus)
    lines.append("=" * 120)
    lines.append("SUMMARY")
    lines.append("=" * 120)
    lines.append(f"Consensus groups : {len(consensus)}")
    lines.append(f"Majority         : {counts.get('majority', 0)}")
    lines.append(f"Tie              : {counts.get('tie', 0)}")
    lines.append(f"Single variant   : {counts.get('single_variant', 0)}")
    lines.append("")
    for idx, item in enumerate(consensus, start=1):
        lines.append("-" * 120)
        lines.append(format_consensus_item(item, idx))
        lines.append("")
    report_file.write_text("\n".join(lines), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description="Build consensus by plate across paper/tbot/payments/telegram/video sources.")
    parser.add_argument("--period", default=DEFAULT_PERIOD_CODE)
    parser.add_argument("--include-single", action="store_true")
    args = parser.parse_args()
    consensus = build_consensus(period_code=args.period, include_single=args.include_single)
    report_dir = paths.OSBB_EXPORTS_DIR / "billing"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_file = report_dir / f"plate_consensus_report_{args.period}_{now_ts()}.txt"
    write_report(report_file, consensus, args.period)
    counts = Counter(item["status"] for item in consensus)
    print("=" * 70)
    print("PLATE CONSENSUS REPORT")
    print("=" * 70)
    print("DB:", get_db_file())
    print("QDB:", get_quarantine_db_file())
    print("MODE:", "TEST/WORK" if USE_TEST_DB else "PROD")
    print("Period:", args.period)
    print("Groups:", len(consensus))
    print("Majority:", counts.get("majority", 0))
    print("Tie:", counts.get("tie", 0))
    print("Single:", counts.get("single_variant", 0))
    print("")
    print("Report:")
    print(report_file)
    print("=" * 70)


if __name__ == "__main__":
    main()
