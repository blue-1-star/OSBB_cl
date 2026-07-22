from pathlib import Path
import sys
import sqlite3
from datetime import datetime
from utils import apartment_sort_sql

OSBB_ROOT = Path(__file__).resolve().parent
PY_ROOT = OSBB_ROOT.parent

if str(PY_ROOT) not in sys.path:
    sys.path.insert(0, str(PY_ROOT))

from config import paths


def now_ts():
    return datetime.now().strftime("%Y-%m-%d_%H-%M-%S")


class Report:
    def __init__(self):
        report_dir = paths.OSBB_EXPORTS_DIR / "audits"
        report_dir.mkdir(parents=True, exist_ok=True)

        self.report_file = (
            report_dir /
            f"verification_tasks_{now_ts()}.txt"
        )

        self.lines = []

    def add(self, text=""):
        self.lines.append(str(text))

    def section(self, title):
        self.add()
        self.add("=" * 80)
        self.add(title)
        self.add("=" * 80)

    def save(self):
        self.report_file.write_text(
            "\n".join(self.lines),
            encoding="utf-8"
        )
        return self.report_file


def generate_report():
    conn = sqlite3.connect(paths.OSBB_DB_FILE)
    cur = conn.cursor()

    report = Report()

    report.section("VERIFICATION TASKS REPORT")

    report.add(f"DB: {paths.OSBB_DB_FILE}")
    report.add(
        f"Generated: "
        f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )

    cur.execute("""
        SELECT COUNT(*)
        FROM verification_tasks
    """)
    total_tasks = cur.fetchone()[0]

    report.section("SUMMARY")

    report.add(f"Total tasks: {total_tasks}")

    cur.execute("""
        SELECT task_type, COUNT(*)
        FROM verification_tasks
        GROUP BY task_type
        ORDER BY COUNT(*) DESC
    """)

    rows = cur.fetchall()

    for task_type, count in rows:
        report.add(f"{task_type:35} : {count}")

    report.section("OPEN TASKS")

    cur.execute(f"""
        SELECT
            id,
            apartment_number,
            task_type,
            priority,
            status,
            main_value,
            normalized_main_value,
            suggestion,
            comment
        FROM verification_tasks
        WHERE status IN ('new', 'in_progress')
        ORDER BY priority,
                 {apartment_sort_sql("apartment_number")}
    """)

    rows = cur.fetchall()

    report.add(f"Count: {len(rows)}")

    for (
        task_id,
        apartment_number,
        task_type,
        priority,
        status,
        main_value,
        normalized_value,
        suggestion,
        comment,
    ) in rows:

        report.add("-" * 80)

        report.add(f"Task ID     : {task_id}")
        report.add(f"Apartment   : {apartment_number}")
        report.add(f"Type        : {task_type}")
        report.add(f"Priority    : {priority}")
        report.add(f"Status      : {status}")

        report.add(f"Value       : {main_value}")
        report.add(f"Normalized  : {normalized_value}")

        report.add(f"Suggestion  : {suggestion}")
        report.add(f"Comment     : {comment}")

    report.section("TASKS BY APARTMENT")

    cur.execute(f"""
    SELECT
        apartment_number,
        COUNT(*)
    FROM verification_tasks
    GROUP BY apartment_number
    ORDER BY {apartment_sort_sql("apartment_number")}
""")

    rows = cur.fetchall()

    for apartment_number, count in rows:
        report.add(
            f"{str(apartment_number):12} : {count}"
        )

    conn.close()

    report_path = report.save()

    print()
    print("=" * 70)
    print("REPORT CREATED")
    print("=" * 70)
    print(report_path)


if __name__ == "__main__":
    generate_report()