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


DB_FILE = paths.OSBB_TELEGRAM_DB_FILE


APARTMENT_RE = re.compile(
    r"(?:кв\.?|квартира)\s*(\d+)",
    re.IGNORECASE
)

PHONE_RE = re.compile(
    r"\+?\d[\d\s\-\(\)]{7,}\d"
)

PLATE_RE = re.compile(
    r"[A-ZА-ЯІЇЄ]{1,2}\s*\d{3,5}\s*[A-ZА-ЯІЇЄ]{0,2}",
    re.IGNORECASE
)

PAYMENT_WORDS = [
    "оплат",
    "сплат",
    "квитанц",
    "платіж",
    "платеж",
]

REMOTE_WORDS = [
    "брел",
    "пульт",
]

PARKING_WORDS = [
    "авто",
    "номер",
    "парков",
    "паркінг",
]


def save_report(lines):
    report_dir = paths.OSBB_EXPORTS_DIR / "audits"
    report_dir.mkdir(parents=True, exist_ok=True)

    report_file = (
        report_dir /
        f"telegram_messages_audit_{datetime.now():%Y-%m-%d_%H-%M-%S}.txt"
    )

    report_file.write_text(
        "\n".join(lines),
        encoding="utf-8"
    )

    return report_file


def main():
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()

    lines = []

    lines.append("=" * 80)
    lines.append("OSBB TELEGRAM AUDIT")
    lines.append("=" * 80)
    lines.append("")

    cur.execute("""
        SELECT COUNT(*)
        FROM telegram_chats
    """)

    chat_count = cur.fetchone()[0]

    cur.execute("""
        SELECT COUNT(*)
        FROM telegram_messages
    """)

    message_count = cur.fetchone()[0]

    lines.append(f"Chats     : {chat_count}")
    lines.append(f"Messages  : {message_count}")
    lines.append("")

    cur.execute("""
        SELECT
            telegram_message_id,
            text_raw
        FROM telegram_messages
        WHERE text_raw IS NOT NULL
          AND TRIM(text_raw) <> ''
    """)

    apartments = set()
    phones = set()
    plates = set()

    payment_examples = []
    remote_examples = []
    parking_examples = []

    for msg_id, text in cur.fetchall():

        lower = text.lower()

        for m in APARTMENT_RE.finditer(text):
            apartments.add(m.group(1))

        for m in PHONE_RE.finditer(text):
            phones.add(m.group(0))

        for m in PLATE_RE.finditer(text):
            plates.add(m.group(0))

        if any(word in lower for word in PAYMENT_WORDS):
            if len(payment_examples) < 20:
                payment_examples.append(
                    (msg_id, text[:250])
                )

        if any(word in lower for word in REMOTE_WORDS):
            if len(remote_examples) < 20:
                remote_examples.append(
                    (msg_id, text[:250])
                )

        if any(word in lower for word in PARKING_WORDS):
            if len(parking_examples) < 20:
                parking_examples.append(
                    (msg_id, text[:250])
                )

    lines.append("=" * 80)
    lines.append("EXTRACTED PATTERNS")
    lines.append("=" * 80)
    lines.append("")

    lines.append(f"Apartments found : {len(apartments)}")
    lines.append(f"Phones found     : {len(phones)}")
    lines.append(f"Plates found     : {len(plates)}")
    lines.append("")

    lines.append("Apartments:")
    lines.append(", ".join(sorted(apartments)[:200]))
    lines.append("")

    lines.append("=" * 80)
    lines.append("PAYMENT EXAMPLES")
    lines.append("=" * 80)

    for msg_id, text in payment_examples:
        lines.append("")
        lines.append(f"Message ID: {msg_id}")
        lines.append(text)

    lines.append("")
    lines.append("=" * 80)
    lines.append("REMOTE / FOB EXAMPLES")
    lines.append("=" * 80)

    for msg_id, text in remote_examples:
        lines.append("")
        lines.append(f"Message ID: {msg_id}")
        lines.append(text)

    lines.append("")
    lines.append("=" * 80)
    lines.append("PARKING EXAMPLES")
    lines.append("=" * 80)

    for msg_id, text in parking_examples:
        lines.append("")
        lines.append(f"Message ID: {msg_id}")
        lines.append(text)

    conn.close()

    report_file = save_report(lines)

    print("Audit completed.")
    print("Report:")
    print(report_file)


if __name__ == "__main__":
    main()