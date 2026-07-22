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
from utils import normalize_plate, normalize_phone


SERVICE_CHAT_KEYWORDS = [
    "дима охр",
]


APARTMENT_RE = re.compile(
    r"(?:кв\.?|квартира|квартира\s*/\s*приміщення\s*/\s*компанія)"
    r"\s*[:№.]?\s*(\d+)",
    re.IGNORECASE,
)

PHONE_RE = re.compile(
    r"\+?\d[\d\s\-\(\)]{7,}\d"
)

PLATE_RE = re.compile(
    r"\b[А-ЯA-ZІЇЄҐ]{1,2}\s*\d{3,5}\s*[А-ЯA-ZІЇЄҐ]{0,2}\b",
    re.IGNORECASE,
)

REMOTE_RE = re.compile(
    r"(\d+)\s*(?:шт\.?|штук|брелк\w*|брелок|брелки|пульт\w*)",
    re.IGNORECASE,
)

NAME_LABEL_RE = re.compile(
    r"(?:ПІБ|ФІО|П\.?І\.?Б\.?)\s*[:\-]\s*(.+)",
    re.IGNORECASE,
)


def now_ts():
    return datetime.now().strftime("%Y-%m-%d_%H-%M-%S")


def clean_text(text):
    return (text or "").replace("\r", "").strip()


def one_line(text):
    return clean_text(text).replace("\n", " ")


def is_service_chat(chat_name):
    name = (chat_name or "").lower()
    return any(k in name for k in SERVICE_CHAT_KEYWORDS)


def extract_apartments(text):
    values = APARTMENT_RE.findall(text or "")
    return sorted(set(values), key=lambda x: int(x))


def extract_phones(text):
    phones = []

    for raw in PHONE_RE.findall(text or ""):
        norm = normalize_phone(raw)
        if norm:
            phones.extend([x.strip() for x in norm.split(";") if x.strip()])

    return sorted(set(phones))


def extract_remote_count(text):
    matches = REMOTE_RE.findall(text or "")
    if not matches:
        return None

    try:
        return max(int(x) for x in matches)
    except ValueError:
        return None


def extract_plates(text):
    result = []

    for raw in PLATE_RE.findall(text or ""):
        normalized, status = normalize_plate(raw)

        if not normalized:
            continue

        if len(normalized) < 5:
            continue

        # Отсекаем явные короткие числовые фрагменты
        if normalized.isdigit():
            continue

        result.append({
            "raw": raw.strip(),
            "normalized": normalized,
            "status": status,
        })

    # Уникальность по normalized
    unique = {}
    for item in result:
        unique[item["normalized"]] = item

    return list(unique.values())


def extract_name(text):
    """
    1) Явный формат: ПІБ: ...
    2) Нумерованный ответ: строка после '2.'
    """

    lines = [x.strip() for x in (text or "").splitlines() if x.strip()]

    for line in lines:
        m = NAME_LABEL_RE.search(line)
        if m:
            value = m.group(1).strip()
            return value if value else None

    for line in lines:
        m = re.match(r"^2\.\s*(.+)$", line)
        if m:
            value = m.group(1).strip()

            # Не считаем телефоны/авто именем
            if not PHONE_RE.search(value) and not PLATE_RE.search(value):
                return value

    return None


def extract_models_from_numbered_answer(text):
    """
    В эталонных ответах модели часто идут в пункте 4:
    4. Ауди Q8, Mercedes EQE, Zeekr 001.
    """

    lines = [x.strip() for x in (text or "").splitlines() if x.strip()]

    for line in lines:
        m = re.match(r"^4\.\s*(.+)$", line)
        if m:
            value = m.group(1).strip().strip(".")
            parts = [p.strip() for p in re.split(r"[,;]", value) if p.strip()]
            return parts

    return []


def looks_relevant(text):
    lower = (text or "").lower()

    keywords = [
        "пульт",
        "брел",
        "шлагбаум",
        "авто",
        "номер",
        "парков",
        "паркінг",
        "квартира",
        "кв.",
        "піб",
        "телефон",
    ]

    return any(k in lower for k in keywords)


def main():
    conn = sqlite3.connect(paths.OSBB_TELEGRAM_DB_FILE)
    cur = conn.cursor()

    cur.execute("""
        DELETE FROM telegram_facts
        WHERE fact_type = 'vehicle_info'
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
        ORDER BY c.chat_name, m.message_date
    """)

    rows = cur.fetchall()

    message_blocks = []
    service_skipped = []

    for msg_db_id, chat_id, chat_name, msg_id, msg_date, text in rows:
        if not looks_relevant(text):
            continue

        apartments = extract_apartments(text)
        phones = extract_phones(text)
        plates = extract_plates(text)
        person_name = extract_name(text)
        models = extract_models_from_numbered_answer(text)
        remote_count = extract_remote_count(text)

        has_any_fact = bool(
            apartments
            or phones
            or plates
            or person_name
            or models
            or remote_count
        )

        if not has_any_fact:
            continue

        block = {
            "msg_db_id": msg_db_id,
            "chat_id": chat_id,
            "chat_name": chat_name,
            "msg_id": msg_id,
            "date": msg_date,
            "apartments": apartments,
            "person_name": person_name,
            "phones": phones,
            "plates": plates,
            "models": models,
            "remote_count": remote_count,
            "text": clean_text(text),
        }

        if is_service_chat(chat_name):
            service_skipped.append(block)
            continue

        message_blocks.append(block)

        apartment_number = apartments[0] if apartments else None

        # В telegram_facts пишем одну строку на авто,
        # но в отчёте группируем по сообщению.
        if plates:
            for plate in plates:
                cur.execute("""
                    INSERT INTO telegram_facts (
                        telegram_message_db_id,
                        chat_id,
                        fact_type,
                        apartment_number,
                        person_name,
                        phone_normalized,
                        license_plate,
                        license_plate_normalized,
                        car_model,
                        remote_count,
                        fact_status,
                        comment,
                        created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    msg_db_id,
                    chat_id,
                    "vehicle_info",
                    apartment_number,
                    person_name,
                    "; ".join(phones) if phones else None,
                    plate["raw"],
                    plate["normalized"],
                    "; ".join(models) if models else None,
                    remote_count,
                    "new",
                    f"chat={chat_name}; msg_id={msg_id}; plate_status={plate['status']}",
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                ))
        else:
            cur.execute("""
                INSERT INTO telegram_facts (
                    telegram_message_db_id,
                    chat_id,
                    fact_type,
                    apartment_number,
                    person_name,
                    phone_normalized,
                    car_model,
                    remote_count,
                    fact_status,
                    comment,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                msg_db_id,
                chat_id,
                "vehicle_info",
                apartment_number,
                person_name,
                "; ".join(phones) if phones else None,
                "; ".join(models) if models else None,
                remote_count,
                "new",
                f"chat={chat_name}; msg_id={msg_id}; no plate found",
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            ))

    conn.commit()

    report_dir = paths.OSBB_EXPORTS_DIR / "audits"
    report_dir.mkdir(parents=True, exist_ok=True)

    report_file = report_dir / f"telegram_vehicle_facts_report_{now_ts()}.txt"

    def apt_sort_key(block):
        apts = block["apartments"]
        if apts:
            return (int(apts[0]), block["date"] or "", block["chat_name"] or "")
        return (999999, block["date"] or "", block["chat_name"] or "")

    message_blocks.sort(key=apt_sort_key)

    lines = []
    lines.append("=" * 80)
    lines.append("TELEGRAM VEHICLE FACTS REPORT")
    lines.append("=" * 80)
    lines.append(f"Generated: {datetime.now():%Y-%m-%d %H:%M:%S}")
    lines.append(f"Message blocks: {len(message_blocks)}")
    lines.append(f"Service skipped: {len(service_skipped)}")
    lines.append("")

    lines.append("=" * 80)
    lines.append("DETAILS BY MESSAGE")
    lines.append("=" * 80)

    for b in message_blocks:
        lines.append("-" * 80)
        lines.append(f"Chat         : {b['chat_name']}")
        lines.append(f"Date         : {b['date']}")
        lines.append(f"Message ID   : {b['msg_id']}")
        lines.append(f"Apartment    : {', '.join(b['apartments']) if b['apartments'] else '-'}")
        lines.append(f"Name         : {b['person_name'] or '-'}")
        lines.append(f"Phones       : {'; '.join(b['phones']) if b['phones'] else '-'}")
        lines.append(
            "Plates       : "
            + (
                "; ".join(
                    f"{p['normalized']} ({p['status']})"
                    for p in b["plates"]
                )
                if b["plates"]
                else "-"
            )
        )
        lines.append(f"Models       : {'; '.join(b['models']) if b['models'] else '-'}")
        lines.append(f"Remote count : {b['remote_count'] if b['remote_count'] is not None else '-'}")
        lines.append("")
        lines.append("Text:")
        lines.append(b["text"][:1200])

    lines.append("")
    lines.append("=" * 80)
    lines.append("SERVICE CHATS SKIPPED")
    lines.append("=" * 80)

    for b in service_skipped:
        lines.append("-" * 80)
        lines.append(f"Chat       : {b['chat_name']}")
        lines.append(f"Date       : {b['date']}")
        lines.append(f"Message ID : {b['msg_id']}")
        lines.append(f"Apartment  : {', '.join(b['apartments']) if b['apartments'] else '-'}")
        lines.append(f"Plates     : {'; '.join(p['normalized'] for p in b['plates']) if b['plates'] else '-'}")
        lines.append("Text:")
        lines.append(b["text"][:700])

    report_file.write_text("\n".join(lines), encoding="utf-8")

    cur.execute("""
        SELECT COUNT(*)
        FROM telegram_facts
        WHERE fact_type = 'vehicle_info'
    """)
    total_facts = cur.fetchone()[0]

    conn.close()

    print("Telegram vehicle facts extracted.")
    print("Message blocks:", len(message_blocks))
    print("Service skipped:", len(service_skipped))
    print("Facts in telegram_facts:", total_facts)
    print("Report:")
    print(report_file)


if __name__ == "__main__":
    main()