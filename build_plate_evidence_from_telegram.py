from pathlib import Path
import sys
import sqlite3
from datetime import datetime

OSBB_ROOT = Path(__file__).resolve().parent
PY_ROOT = OSBB_ROOT.parent

if str(PY_ROOT) not in sys.path:
    sys.path.insert(0, str(PY_ROOT))

from config import paths


CREATED_BY = "build_plate_evidence_from_telegram"


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def match_type(task_plate, evidence_plate):
    if not task_plate or not evidence_plate:
        return None

    t = task_plate.upper()
    e = evidence_plate.upper()

    if t == e:
        return "EXACT_MATCH"

    if e.endswith(t):
        return "MISSING_FIRST_CHAR"

    if e.startswith(t):
        return "MISSING_LAST_CHAR"

    if t in e or e in t:
        return "CONTAINS_VALUE"

    return None


def main():
    main_conn = sqlite3.connect(paths.OSBB_DB_FILE)
    main_cur = main_conn.cursor()

    tg_conn = sqlite3.connect(paths.OSBB_TELEGRAM_DB_FILE)
    tg_cur = tg_conn.cursor()

    main_cur.execute("""
        DELETE FROM verification_evidence
        WHERE source_name = 'telegram'
    """)

    main_cur.execute("""
        SELECT
            id,
            apartment_number,
            normalized_main_value,
            main_value
        FROM verification_tasks
        WHERE task_group = 'vehicle'
          AND field_name = 'license_plate'
          AND status IN ('new', 'in_progress')
    """)

    tasks = main_cur.fetchall()

    tg_cur.execute("""
        SELECT
            tf.id,
            tf.apartment_number,
            tf.license_plate_normalized,
            tf.license_plate,
            tf.person_name,
            tf.phone_normalized,
            tf.comment
        FROM telegram_facts tf
        WHERE tf.fact_type = 'vehicle_info'
          AND tf.license_plate_normalized IS NOT NULL
    """)

    tg_facts = tg_cur.fetchall()

    inserted = 0

    for task_id, task_apt, task_norm, task_raw in tasks:
        task_plate = task_norm or task_raw

        if not task_plate:
            continue

        for (
            fact_id,
            tg_apt,
            tg_plate_norm,
            tg_plate_raw,
            person_name,
            phone_normalized,
            tg_comment,
        ) in tg_facts:

            mt = match_type(task_plate, tg_plate_norm)

            if not mt:
                continue

            # Если квартира известна в обеих сторонах и не совпадает — не связываем.
            if task_apt and tg_apt and str(task_apt) != str(tg_apt):
                continue

            comment = (
                f"Telegram fact. "
                f"telegram_apartment={tg_apt}; "
                f"person={person_name or '-'}; "
                f"phone={phone_normalized or '-'}; "
                f"{tg_comment or ''}"
            )

            main_cur.execute("""
                INSERT INTO verification_evidence (
                    task_id,
                    source_name,
                    source_db,
                    source_table,
                    source_record_id,
                    evidence_type,
                    evidence_value,
                    normalized_value,
                    match_type,
                    comment,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                task_id,
                "telegram",
                str(paths.OSBB_TELEGRAM_DB_FILE),
                "telegram_facts",
                str(fact_id),
                "license_plate",
                tg_plate_raw,
                tg_plate_norm,
                mt,
                comment,
                now(),
            ))

            inserted += 1

    main_conn.commit()

    main_cur.execute("""
        SELECT match_type, COUNT(*)
        FROM verification_evidence
        WHERE source_name = 'telegram'
        GROUP BY match_type
        ORDER BY COUNT(*) DESC
    """)

    rows = main_cur.fetchall()

    main_conn.close()
    tg_conn.close()

    print("Telegram evidence created:", inserted)
    print()
    print("By match type:")
    for mt, count in rows:
        print(f"{mt:25}: {count}")


if __name__ == "__main__":
    main()