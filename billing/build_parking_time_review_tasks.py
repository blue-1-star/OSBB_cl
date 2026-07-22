from pathlib import Path
import sys
import sqlite3
from datetime import datetime

MODULE_DIR = Path(__file__).resolve().parent
OSBB_ROOT = MODULE_DIR.parent
PY_ROOT = OSBB_ROOT.parent

for p in (OSBB_ROOT, PY_ROOT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from config import paths


CREATED_BY = "build_parking_time_review_tasks"


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def create_table(cur):

    cur.execute("""
    CREATE TABLE IF NOT EXISTS parking_time_review_tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,

        apartment_number TEXT,

        vehicle_id INTEGER,
        license_plate TEXT,
        car_model TEXT,

        current_parking_time TEXT,

        suggested_day_count INTEGER,
        suggested_night_count INTEGER,

        task_type TEXT,
        priority INTEGER DEFAULT 50,

        source_name TEXT,
        source_details TEXT,

        status TEXT DEFAULT 'new',

        created_at TEXT,
        created_by TEXT,

        reviewed_at TEXT,
        reviewed_by TEXT,

        decision TEXT,
        decision_comment TEXT
    )
    """)

    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_ptrt_status
    ON parking_time_review_tasks(status)
    """)

    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_ptrt_apartment
    ON parking_time_review_tasks(apartment_number)
    """)


def clear_previous(cur):

    cur.execute("""
        DELETE FROM parking_time_review_tasks
        WHERE created_by = ?
          AND status = 'new'
    """, (CREATED_BY,))


def create_missing_tasks(cur):

    cur.execute("""
        SELECT
            a.apartment_number,
            v.id,
            v.license_plate,
            v.car_model,
            v.parking_time
        FROM vehicles v
        JOIN apartments a
             ON a.id = v.apartment_id
        WHERE
            v.parking_time IS NULL
            OR trim(v.parking_time) = ''
    """)

    count = 0

    for (
        apartment_number,
        vehicle_id,
        license_plate,
        car_model,
        parking_time,
    ) in cur.fetchall():

        cur.execute("""
            INSERT INTO parking_time_review_tasks (
                apartment_number,
                vehicle_id,
                license_plate,
                car_model,
                current_parking_time,
                task_type,
                priority,
                source_name,
                source_details,
                status,
                created_at,
                created_by
            )
            VALUES (
                ?, ?, ?, ?, ?,
                'parking_time_missing',
                100,
                'vehicles',
                'parking_time is empty',
                'new',
                ?,
                ?
            )
        """, (
            apartment_number,
            vehicle_id,
            license_plate,
            car_model,
            parking_time,
            now(),
            CREATED_BY,
        ))

        count += 1

    return count


def create_conflict_tasks(cur):

    """
    Пока заготовка.

    Сюда позже добавим:
    - сравнение с Охорона.xlsx
    - Day/Night конфликты
    - нестандартные записи:
      '1-д і сутки'
    """

    return 0


def build_tasks():

    conn = sqlite3.connect(paths.OSBB_DB_FILE)
    cur = conn.cursor()

    create_table(cur)
    clear_previous(cur)

    missing_count = create_missing_tasks(cur)
    conflict_count = create_conflict_tasks(cur)

    conn.commit()

    cur.execute("""
        SELECT COUNT(*)
        FROM parking_time_review_tasks
        WHERE status = 'new'
    """)

    total = cur.fetchone()[0]

    conn.close()

    print()
    print("=" * 70)
    print("PARKING TIME REVIEW TASKS")
    print("=" * 70)
    print("Missing tasks :", missing_count)
    print("Conflict tasks:", conflict_count)
    print("Total tasks   :", total)
    print()


if __name__ == "__main__":
    build_tasks()