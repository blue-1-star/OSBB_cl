from pathlib import Path
import sys
import sqlite3
from datetime import datetime

OSBB_ROOT = Path(__file__).resolve().parent
PY_ROOT = OSBB_ROOT.parent

if str(PY_ROOT) not in sys.path:
    sys.path.insert(0, str(PY_ROOT))

from config import paths


CREATED_BY = "build_verification_tasks"


CUSTOM_OR_FOREIGN_PLATES = {
    "D00077",
    "LNH519",
    "INDIGO",
}


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def clear_old_auto_tasks(cur):
    cur.execute("""
        DELETE FROM verification_tasks
        WHERE created_by = ?
          AND status = 'new'
    """, (CREATED_BY,))


def task_exists(cur, object_table, object_id, task_type, field_name):
    cur.execute("""
        SELECT id
        FROM verification_tasks
        WHERE object_table = ?
          AND object_id = ?
          AND task_type = ?
          AND field_name = ?
          AND status IN ('new', 'in_progress')
    """, (object_table, object_id, task_type, field_name))

    return cur.fetchone() is not None


def insert_task(
    cur,
    apartment_id,
    apartment_number,
    task_group,
    task_type,
    priority,
    source_name,
    object_table,
    object_id,
    field_name,
    main_value,
    normalized_main_value,
    suggestion,
    comment,
):
    if task_exists(cur, object_table, object_id, task_type, field_name):
        return False

    cur.execute("""
        INSERT INTO verification_tasks (
            apartment_id,
            apartment_number,
            task_group,
            task_type,
            priority,
            status,
            source_name,
            object_table,
            object_id,
            field_name,
            main_value,
            normalized_main_value,
            suggestion,
            comment,
            created_at,
            created_by
        )
        VALUES (?, ?, ?, ?, ?, 'new', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        apartment_id,
        apartment_number,
        task_group,
        task_type,
        priority,
        source_name,
        object_table,
        object_id,
        field_name,
        main_value,
        normalized_main_value,
        suggestion,
        comment,
        now(),
        CREATED_BY,
    ))

    return True


def build_plate_tasks(cur):
    cur.execute("""
        SELECT
            v.id,
            v.apartment_id,
            a.apartment_number,
            v.license_plate,
            v.license_plate_normalized,
            v.plate_format_status,
            v.car_model,
            v.car_model_normalized
        FROM vehicles v
        JOIN apartments a ON a.id = v.apartment_id
        WHERE v.plate_format_status = 'SUSPICIOUS'
        ORDER BY a.apartment_number
    """)

    rows = cur.fetchall()

    created = 0

    for (
        vehicle_id,
        apartment_id,
        apartment_number,
        raw_plate,
        normalized_plate,
        plate_status,
        car_model,
        car_model_normalized,
    ) in rows:

        plate = normalized_plate or raw_plate or ""

        if plate in CUSTOM_OR_FOREIGN_PLATES:
            task_type = "plate_custom_or_foreign"
            priority = 80
            suggestion = "Вероятно именной или иностранный номер. Подтвердить вручную."
        elif len(plate) in (6, 7):
            task_type = "plate_possible_missing_suffix"
            priority = 20
            suggestion = "Похоже на украинский номер с потерянной буквой в конце."
        elif len(plate) < 6:
            task_type = "plate_too_short"
            priority = 10
            suggestion = "Номер слишком короткий. Требуется ручная проверка."
        else:
            task_type = "plate_needs_check"
            priority = 50
            suggestion = "Номер не соответствует стандартному шаблону AA0000AA."

        comment = (
            f"Авто: {car_model or '-'}; "
            f"Марка нормализована: {car_model_normalized or '-'}; "
            f"Статус номера: {plate_status}"
        )

        if insert_task(
            cur=cur,
            apartment_id=apartment_id,
            apartment_number=apartment_number,
            task_group="vehicle",
            task_type=task_type,
            priority=priority,
            source_name="main_db",
            object_table="vehicles",
            object_id=vehicle_id,
            field_name="license_plate",
            main_value=raw_plate,
            normalized_main_value=normalized_plate,
            suggestion=suggestion,
            comment=comment,
        ):
            created += 1

    return created


def build_verification_tasks():
    db_file = paths.OSBB_DB_FILE

    print("=" * 70)
    print("BUILD VERIFICATION TASKS")
    print("=" * 70)
    print(f"DB: {db_file}")
    print()

    conn = sqlite3.connect(db_file)
    cur = conn.cursor()

    clear_old_auto_tasks(cur)

    plate_tasks = build_plate_tasks(cur)

    conn.commit()

    cur.execute("SELECT COUNT(*) FROM verification_tasks")
    total_tasks = cur.fetchone()[0]

    cur.execute("""
        SELECT task_type, COUNT(*)
        FROM verification_tasks
        GROUP BY task_type
        ORDER BY COUNT(*) DESC
    """)
    by_type = cur.fetchall()

    conn.close()

    print("Tasks created:")
    print(f"Plate tasks: {plate_tasks}")
    print()
    print(f"Total tasks in DB: {total_tasks}")
    print()
    print("By type:")
    for task_type, count in by_type:
        print(f"{task_type:35}: {count}")


if __name__ == "__main__":
    build_verification_tasks()