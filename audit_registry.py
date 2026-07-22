from pathlib import Path
import sys
import sqlite3
from datetime import datetime

OSBB_ROOT = Path(__file__).resolve().parent
PY_ROOT = OSBB_ROOT.parent

if str(PY_ROOT) not in sys.path:
    sys.path.insert(0, str(PY_ROOT))

from config import paths


def fetch_all(cur, sql, params=()):
    cur.execute(sql, params)
    return cur.fetchall()


class AuditReport:
    def __init__(self):
        report_time = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        self.report_dir = paths.OSBB_EXPORTS_DIR / "audits"
        self.report_dir.mkdir(parents=True, exist_ok=True)
        self.report_file = self.report_dir / f"registry_audit_{report_time}.txt"
        self.lines = []

    def add(self, text=""):
        self.lines.append(str(text))

    def section(self, title):
        self.add()
        self.add("=" * 70)
        self.add(title)
        self.add("=" * 70)

    def save(self):
        self.report_file.write_text(
            "\n".join(self.lines),
            encoding="utf-8"
        )
        return self.report_file


def audit_registry():
    db_file = paths.OSBB_DB_FILE
    report = AuditReport()

    report.section("OSBB REGISTRY AUDIT")
    report.add(f"DB: {db_file}")
    report.add(f"Audit time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    if not db_file.exists():
        raise FileNotFoundError(f"База данных не найдена:\n{db_file}")

    conn = sqlite3.connect(db_file)
    cur = conn.cursor()

    report.section("GENERAL COUNTS")

    tables = [
        "apartments",
        "persons",
        "vehicles",
        "contact_methods",
        "events",
        "verification_log",
        "audit_log",
        "message_sources",
        "raw_messages",
        "extracted_facts",
    ]

    for table in tables:
        try:
            cur.execute(f"SELECT COUNT(*) FROM {table}")
            count = cur.fetchone()[0]
            report.add(f"{table:20}: {count}")
        except sqlite3.OperationalError:
            report.add(f"{table:20}: table not found")

    report.section("APARTMENTS BY SOURCE")

    rows = fetch_all(
        cur,
        """
        SELECT source, COUNT(*)
        FROM apartments
        GROUP BY source
        ORDER BY COUNT(*) DESC
        """
    )

    for source, count in rows:
        report.add(f"{source or 'NULL':25}: {count}")

    report.section("APARTMENTS CREATED FROM PAPER PARKING")

    rows = fetch_all(
        cur,
        """
        SELECT apartment_number, entrance, source, notes
        FROM apartments
        WHERE source = 'paper_parking'
        ORDER BY apartment_number
        """
    )

    if rows:
        for apartment_number, entrance, source, notes in rows:
            report.add(
                f"{apartment_number:10} entrance={entrance} "
                f"source={source} notes={notes}"
            )
    else:
        report.add("Нет квартир, созданных из paper_parking.")

    report.section("APARTMENTS WITHOUT PERSONS")

    rows = fetch_all(
        cur,
        """
        SELECT a.apartment_number, a.source
        FROM apartments a
        LEFT JOIN persons p ON p.apartment_id = a.id
        WHERE p.id IS NULL
        ORDER BY a.apartment_number
        """
    )

    report.add(f"Count: {len(rows)}")

    for apartment_number, source in rows:
        report.add(f"{apartment_number:10} source={source}")

    report.section("APARTMENTS WITH MULTIPLE PERSON RECORDS")

    rows = fetch_all(
        cur,
        """
        SELECT a.apartment_number, COUNT(p.id) AS persons_count
        FROM apartments a
        JOIN persons p ON p.apartment_id = a.id
        GROUP BY a.id
        HAVING COUNT(p.id) > 1
        ORDER BY persons_count DESC, a.apartment_number
        """
    )

    report.add(f"Count: {len(rows)}")

    for apartment_number, count in rows:
        report.add(f"{apartment_number:10} persons={count}")

    report.section("VEHICLES WITHOUT LICENSE PLATE")

    rows = fetch_all(
        cur,
        """
        SELECT a.apartment_number, v.car_model, v.source
        FROM vehicles v
        JOIN apartments a ON a.id = v.apartment_id
        WHERE v.license_plate IS NULL OR TRIM(v.license_plate) = ''
        ORDER BY a.apartment_number
        """
    )

    report.add(f"Count: {len(rows)}")

    for apartment_number, car_model, source in rows:
        report.add(f"{apartment_number:10} car={car_model} source={source}")

    report.section("VEHICLES WITHOUT CAR MODEL")

    rows = fetch_all(
        cur,
        """
        SELECT a.apartment_number, v.license_plate, v.source
        FROM vehicles v
        JOIN apartments a ON a.id = v.apartment_id
        WHERE v.car_model IS NULL OR TRIM(v.car_model) = ''
        ORDER BY a.apartment_number
        """
    )

    report.add(f"Count: {len(rows)}")

    for apartment_number, plate, source in rows:
        report.add(f"{apartment_number:10} plate={plate} source={source}")

    report.section("DUPLICATE LICENSE PLATES")

    rows = fetch_all(
        cur,
        """
        SELECT UPPER(TRIM(license_plate)) AS plate, COUNT(*) AS cnt
        FROM vehicles
        WHERE license_plate IS NOT NULL AND TRIM(license_plate) <> ''
        GROUP BY UPPER(TRIM(license_plate))
        HAVING COUNT(*) > 1
        ORDER BY cnt DESC, plate
        """
    )

    report.add(f"Count: {len(rows)}")

    for plate, count in rows:
        report.add(f"{plate:15} count={count}")

        details = fetch_all(
            cur,
            """
            SELECT a.apartment_number, v.car_model, v.source
            FROM vehicles v
            JOIN apartments a ON a.id = v.apartment_id
            WHERE UPPER(TRIM(v.license_plate)) = ?
            ORDER BY a.apartment_number
            """,
            (plate,)
        )

        for apartment_number, car_model, source in details:
            report.add(
                f"    apt={apartment_number:10} "
                f"car={car_model} source={source}"
            )

    report.section("NON-NUMERIC APARTMENT NUMBERS")

    rows = fetch_all(
        cur,
        """
        SELECT apartment_number, source
        FROM apartments
        WHERE apartment_number GLOB '*[^0-9]*'
        ORDER BY apartment_number
        """
    )

    report.add(f"Count: {len(rows)}")

    for apartment_number, source in rows:
        report.add(f"{apartment_number:15} source={source}")

    conn.close()

    report.section("AUDIT FINISHED")

    report_file = report.save()

    print("Audit completed.")
    print(f"Report saved to:\n{report_file}")


if __name__ == "__main__":
    audit_registry()