from pathlib import Path
import sys
import sqlite3
from datetime import datetime

OSBB_ROOT = Path(__file__).resolve().parent
PY_ROOT = OSBB_ROOT.parent

if str(PY_ROOT) not in sys.path:
    sys.path.insert(0, str(PY_ROOT))

from config import paths


SOURCE_NAME_TELEGRAM = "telegram"
SOURCE_NAME_TBOT = "tbot_parking"


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def plate_digits4(value):
    if not value:
        return None

    digits = "".join(ch for ch in str(value) if ch.isdigit())

    if len(digits) == 4:
        return digits

    return None


def evidence_exists(cur, task_id, source_name, source_table, source_record_id, match_type):
    cur.execute("""
        SELECT id
        FROM verification_evidence
        WHERE task_id = ?
          AND source_name = ?
          AND source_table = ?
          AND source_record_id = ?
          AND match_type = ?
    """, (
        task_id,
        source_name,
        source_table,
        str(source_record_id),
        match_type,
    ))

    return cur.fetchone() is not None


def insert_evidence(
    cur,
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
):
    if evidence_exists(
        cur,
        task_id,
        source_name,
        source_table,
        source_record_id,
        match_type,
    ):
        return False

    cur.execute("""
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
        source_name,
        source_db,
        source_table,
        str(source_record_id),
        evidence_type,
        evidence_value,
        normalized_value,
        match_type,
        comment,
        now(),
    ))

    return True


def load_tasks(cur):
    cur.execute("""
        SELECT
            id,
            apartment_number,
            main_value,
            normalized_main_value
        FROM verification_tasks
        WHERE task_group = 'vehicle'
          AND field_name = 'license_plate'
          AND status IN ('new', 'in_progress')
    """)

    return cur.fetchall()


def load_telegram_facts(cur):
    cur.execute("""
        SELECT
            id,
            apartment_number,
            license_plate,
            license_plate_normalized,
            person_name,
            phone_normalized,
            car_model,
            comment
        FROM telegram_facts
        WHERE fact_type = 'vehicle_info'
          AND license_plate_normalized IS NOT NULL
    """)

    return cur.fetchall()


def load_tbot_facts(cur):
    cur.execute("""
        SELECT
            id,
            apartment_number,
            license_plate,
            license_plate_normalized,
            car_model,
            car_color_normalized,
            ownership_type
        FROM tbot_parking_import
        WHERE license_plate_normalized IS NOT NULL
    """)

    return cur.fetchall()


def build_from_telegram(main_cur, tg_cur):
    tasks = load_tasks(main_cur)
    tg_facts = load_telegram_facts(tg_cur)

    inserted = 0

    for task_id, task_apt, task_raw, task_norm in tasks:
        task_plate = task_norm or task_raw
        task_digits = plate_digits4(task_plate)

        for (
            fact_id,
            tg_apt,
            tg_raw,
            tg_norm,
            person_name,
            phone_normalized,
            car_model,
            comment,
        ) in tg_facts:

            tg_digits = plate_digits4(tg_norm)

            if task_apt and tg_apt and str(task_apt) == str(tg_apt):
                if insert_evidence(
                    cur=main_cur,
                    task_id=task_id,
                    source_name=SOURCE_NAME_TELEGRAM,
                    source_db=str(paths.OSBB_TELEGRAM_DB_FILE),
                    source_table="telegram_facts",
                    source_record_id=fact_id,
                    evidence_type="license_plate",
                    evidence_value=tg_raw,
                    normalized_value=tg_norm,
                    match_type="SAME_APARTMENT_PLATE_LIST",
                    comment=(
                        f"Same apartment in Telegram. "
                        f"apartment={tg_apt}; "
                        f"person={person_name or '-'}; "
                        f"phone={phone_normalized or '-'}; "
                        f"model={car_model or '-'}; "
                        f"{comment or ''}"
                    ),
                ):
                    inserted += 1

            if task_digits and tg_digits and task_digits == tg_digits:
                if insert_evidence(
                    cur=main_cur,
                    task_id=task_id,
                    source_name=SOURCE_NAME_TELEGRAM,
                    source_db=str(paths.OSBB_TELEGRAM_DB_FILE),
                    source_table="telegram_facts",
                    source_record_id=fact_id,
                    evidence_type="license_plate",
                    evidence_value=tg_raw,
                    normalized_value=tg_norm,
                    match_type="SAME_DIGITS_4",
                    comment=(
                        f"Same 4-digit plate core in Telegram. "
                        f"task_digits={task_digits}; "
                        f"telegram_apartment={tg_apt or '-'}; "
                        f"person={person_name or '-'}; "
                        f"phone={phone_normalized or '-'}; "
                        f"model={car_model or '-'}; "
                        f"{comment or ''}"
                    ),
                ):
                    inserted += 1

    return inserted


def build_from_tbot(main_cur, q_cur):
    tasks = load_tasks(main_cur)
    tbot_facts = load_tbot_facts(q_cur)

    inserted = 0

    for task_id, task_apt, task_raw, task_norm in tasks:
        task_plate = task_norm or task_raw
        task_digits = plate_digits4(task_plate)

        for (
            record_id,
            tbot_apt,
            tbot_raw,
            tbot_norm,
            car_model,
            car_color,
            ownership_type,
        ) in tbot_facts:

            tbot_digits = plate_digits4(tbot_norm)

            if task_apt and tbot_apt and str(task_apt) == str(tbot_apt):
                if insert_evidence(
                    cur=main_cur,
                    task_id=task_id,
                    source_name=SOURCE_NAME_TBOT,
                    source_db=str(paths.OSBB_QUARANTINE_DB_FILE),
                    source_table="tbot_parking_import",
                    source_record_id=record_id,
                    evidence_type="license_plate",
                    evidence_value=tbot_raw,
                    normalized_value=tbot_norm,
                    match_type="SAME_APARTMENT_PLATE_LIST",
                    comment=(
                        f"Same apartment in TBot. "
                        f"apartment={tbot_apt}; "
                        f"model={car_model or '-'}; "
                        f"color={car_color or '-'}; "
                        f"ownership={ownership_type or '-'}"
                    ),
                ):
                    inserted += 1

            if task_digits and tbot_digits and task_digits == tbot_digits:
                if insert_evidence(
                    cur=main_cur,
                    task_id=task_id,
                    source_name=SOURCE_NAME_TBOT,
                    source_db=str(paths.OSBB_QUARANTINE_DB_FILE),
                    source_table="tbot_parking_import",
                    source_record_id=record_id,
                    evidence_type="license_plate",
                    evidence_value=tbot_raw,
                    normalized_value=tbot_norm,
                    match_type="SAME_DIGITS_4",
                    comment=(
                        f"Same 4-digit plate core in TBot. "
                        f"task_digits={task_digits}; "
                        f"tbot_apartment={tbot_apt or '-'}; "
                        f"model={car_model or '-'}; "
                        f"color={car_color or '-'}; "
                        f"ownership={ownership_type or '-'}"
                    ),
                ):
                    inserted += 1

    return inserted


def main():
    main_conn = sqlite3.connect(paths.OSBB_DB_FILE)
    main_cur = main_conn.cursor()

    tg_conn = sqlite3.connect(paths.OSBB_TELEGRAM_DB_FILE)
    tg_cur = tg_conn.cursor()

    q_conn = sqlite3.connect(paths.OSBB_QUARANTINE_DB_FILE)
    q_cur = q_conn.cursor()

    inserted_tg = build_from_telegram(main_cur, tg_cur)
    inserted_tbot = build_from_tbot(main_cur, q_cur)

    main_conn.commit()

    main_cur.execute("""
        SELECT source_name, match_type, COUNT(*)
        FROM verification_evidence
        GROUP BY source_name, match_type
        ORDER BY source_name, COUNT(*) DESC
    """)

    summary = main_cur.fetchall()

    main_conn.close()
    tg_conn.close()
    q_conn.close()

    print("Evidence added:")
    print(f"Telegram : {inserted_tg}")
    print(f"TBot     : {inserted_tbot}")
    print()
    print("Summary:")
    for source_name, match_type, count in summary:
        print(f"{source_name:15} {match_type:30} {count}")


if __name__ == "__main__":
    main()