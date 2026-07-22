from pathlib import Path
import sys
import sqlite3
import re
from datetime import datetime

OSBB_ROOT = Path(__file__).resolve().parent
PY_ROOT = OSBB_ROOT.parent

if str(PY_ROOT) not in sys.path:
    sys.path.insert(0, str(PY_ROOT))

from config import paths
from utils import normalize_plate, apartment_sort_sql


APARTMENT_RE = re.compile(
    r"(?:кв\.?|квартира|квартира\s*/\s*приміщення\s*/\s*компанія)\s*[:№]?\s*(\d+)",
    re.IGNORECASE,
)

PLATE_RE = re.compile(
    r"\b[А-ЯA-ZІЇЄҐ]{1,2}\s*\d{3,5}\s*[А-ЯA-ZІЇЄҐ]{0,2}\b",
    re.IGNORECASE,
)

REMOTE_RE = re.compile(
    r"(\d+)\s*(?:шт\.?|штук|брелк\w*|брелок|брелки|пульт\w*)",
    re.IGNORECASE,
)


def now_ts():
    return datetime.now().strftime("%Y-%m-%d_%H-%M-%S")


def clean_text(text):
    return (text or "").replace("\n", " ").strip()


def extract_remote_count(text):
    matches = REMOTE_RE.findall(text or "")
    if not matches:
        return None

    try:
        return max(int(x) for x in matches)
    except ValueError:
        return None


def extract_apartments(text):
    return sorted(set(APARTMENT_RE.findall(text or "")), key=lambda x: int(x))


def extract_plates(text):
    result = []

    for raw in PLATE_RE.findall(text or ""):
        normalized, status = normalize_plate(raw)

        if normalized and len(normalized) >= 5:
            result.append((raw, normalized, status))

    unique = {}
    for raw, normalized, status in result:
        unique[normalized] = (raw, normalized, status)

    return list(unique.values())


def main():
    conn = sqlite3.connect(paths.OSBB_TELEGRAM_DB_FILE)
    cur = conn.cursor()

    cur.execute("""
        DELETE FROM telegram_facts
        WHERE fact_type = 'remote_order'
    """)

    cur.execute("""
        SELECT
            m.id,
            m.chat_id,
            c.chat_name,
            m.telegram_message_id,
            m.message_date,
            m.text_raw
        FROM telegram_messages m
        JOIN telegram_chats c ON c.id = m.chat_id
        WHERE m.text_raw IS NOT NULL
          AND TRIM(m.text_raw) <> ''
    """)

    rows = cur.fetchall()

    facts = []

    for msg_db_id, chat_id, chat_name, msg_id, msg_date, text in rows:
        apartments = extract_apartments(text)
        plates = extract_plates(text)
        remote_count = extract_remote_count(text)

        lower = text.lower()
        has_remote_word = (
            "брел" in lower
            or "пульт" in lower
            or "шлагбаум" in lower
        )

        if not (apartments or plates or remote_count or has_remote_word):
            continue

        if not apartments and not plates and remote_count is None:
            continue

        apartment_number = apartments[0] if apartments else None

        if plates:
            for raw_plate, normalized_plate, plate_status in plates:
                cur.execute("""
                    INSERT INTO telegram_facts (
                        telegram_message_db_id,
                        chat_id,
                        fact_type,
                        apartment_number,
                        license_plate,
                        license_plate_normalized,
                        remote_count,
                        fact_status,
                        comment,
                        created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    msg_db_id,
                    chat_id,
                    "remote_order",
                    apartment_number,
                    raw_plate,
                    normalized_plate,
                    remote_count,
                    "new",
                    f"chat={chat_name}; msg_id={msg_id}; plate_status={plate_status}",
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                ))

                facts.append({
                    "chat": chat_name,
                    "date": msg_date,
                    "message_id": msg_id,
                    "apartment": apartment_number,
                    "plate": normalized_plate,
                    "remote_count": remote_count,
                    "text": clean_text(text),
                })

        else:
            cur.execute("""
                INSERT INTO telegram_facts (
                    telegram_message_db_id,
                    chat_id,
                    fact_type,
                    apartment_number,
                    remote_count,
                    fact_status,
                    comment,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                msg_db_id,
                chat_id,
                "remote_order",
                apartment_number,
                remote_count,
                "new",
                f"chat={chat_name}; msg_id={msg_id}; no plate found",
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            ))

            facts.append({
                "chat": chat_name,
                "date": msg_date,
                "message_id": msg_id,
                "apartment": apartment_number,
                "plate": None,
                "remote_count": remote_count,
                "text": clean_text(text),
            })

    conn.commit()

    report_dir = paths.OSBB_EXPORTS_DIR / "audits"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_file = report_dir / f"telegram_remote_facts_report_{now_ts()}.txt"

    lines = []
    lines.append("=" * 80)
    lines.append("TELEGRAM REMOTE FACTS REPORT")
    lines.append("=" * 80)
    lines.append(f"Generated: {datetime.now():%Y-%m-%d %H:%M:%S}")
    lines.append(f"Facts extracted: {len(facts)}")
    lines.append("")

    cur.execute("""
        SELECT
            apartment_number,
            COUNT(*)
        FROM telegram_facts
        WHERE fact_type = 'remote_order'
        GROUP BY apartment_number
        ORDER BY
            CASE
                WHEN apartment_number GLOB '[0-9]*'
                THEN CAST(apartment_number AS INTEGER)
                ELSE 999999
            END,
            apartment_number
    """)

    lines.append("=" * 80)
    lines.append("FACTS BY APARTMENT")
    lines.append("=" * 80)

    for apartment_number, count in cur.fetchall():
        lines.append(f"{str(apartment_number):12} : {count}")

    lines.append("")
    lines.append("=" * 80)
    lines.append("DETAILS")
    lines.append("=" * 80)

    facts.sort(
        key=lambda x: (
            int(x["apartment"]) if x["apartment"] and str(x["apartment"]).isdigit() else 999999,
            x["date"] or "",
            x["chat"] or "",
        )
    )

    for item in facts:
        lines.append("-" * 80)
        lines.append(f"Chat         : {item['chat']}")
        lines.append(f"Date         : {item['date']}")
        lines.append(f"Message ID   : {item['message_id']}")
        lines.append(f"Apartment    : {item['apartment']}")
        lines.append(f"Plate        : {item['plate']}")
        lines.append(f"Remote count : {item['remote_count']}")
        lines.append(f"Text         : {item['text'][:700]}")

    report_file.write_text("\n".join(lines), encoding="utf-8")

    cur.execute("""
        SELECT COUNT(*)
        FROM telegram_facts
        WHERE fact_type = 'remote_order'
    """)
    total = cur.fetchone()[0]

    conn.close()

    print("Telegram remote facts extracted.")
    print("Facts:", total)
    print("Report:")
    print(report_file)


if __name__ == "__main__":
    main()