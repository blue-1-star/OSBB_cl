from pathlib import Path
import sys
import sqlite3
from datetime import datetime

OSBB_ROOT = Path(__file__).resolve().parent
PY_ROOT = OSBB_ROOT.parent

if str(PY_ROOT) not in sys.path:
    sys.path.insert(0, str(PY_ROOT))

from config import paths
from utils import norm_text, norm_apartment


def fetch_all(cur, sql, params=()):
    cur.execute(sql, params)
    return cur.fetchall()


class Report:
    def __init__(self):
        ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        self.report_dir = paths.OSBB_EXPORTS_DIR / "audits"
        self.report_dir.mkdir(parents=True, exist_ok=True)
        self.report_file = self.report_dir / f"tbot_quarantine_audit_{ts}.txt"
        self.lines = []

    def add(self, text=""):
        self.lines.append(str(text))

    def section(self, title):
        self.add()
        self.add("=" * 80)
        self.add(title)
        self.add("=" * 80)

    def save(self):
        self.report_file.write_text("\n".join(self.lines), encoding="utf-8")
        return self.report_file


def get_main_snapshot(cur):
    """
    Основная рабочая база:
    apartment_number -> данные квартиры, контакты, авто
    """
    rows = fetch_all(
        cur,
        """
        SELECT
            a.id,
            a.apartment_number,
            a.entrance,
            a.source,
            p.full_name,
            p.phone_raw,
            v.license_plate,
            v.car_model,
            v.car_color,
            v.parking_time
        FROM apartments a
        LEFT JOIN persons p ON p.apartment_id = a.id
        LEFT JOIN vehicles v ON v.apartment_id = a.id
        """
    )

    data = {}

    for row in rows:
        (
            apartment_id,
            apartment_number,
            entrance,
            apartment_source,
            full_name,
            phone_raw,
            license_plate,
            car_model,
            car_color,
            parking_time,
        ) = row

        apt = norm_apartment(apartment_number)

        if apt not in data:
            data[apt] = {
                "apartment_id": apartment_id,
                "apartment_number": apt,
                "entrance": entrance,
                "source": apartment_source,
                "names": set(),
                "phones": set(),
                "plates": set(),
                "models": set(),
                "colors": set(),
                "parking_times": set(),
            }

        if norm_text(full_name):
            data[apt]["names"].add(norm_text(full_name))

        if norm_text(phone_raw):
            data[apt]["phones"].add(norm_text(phone_raw))

        if norm_text(license_plate):
            data[apt]["plates"].add(norm_text(license_plate).upper())

        if norm_text(car_model):
            data[apt]["models"].add(norm_text(car_model).upper())

        if norm_text(car_color):
            data[apt]["colors"].add(norm_text(car_color).upper())

        if norm_text(parking_time):
            data[apt]["parking_times"].add(norm_text(parking_time))

    return data


def get_tbot_snapshot(cur):
    rows = fetch_all(
        cur,
        """
        SELECT
            apartment_number,
            ownership_type,
            ownership_type_raw,
            full_name,
            phone_raw,
            car_model,
            car_color,
            license_plate,
            status_raw
        FROM tbot_parking_import
        """
    )

    data = {}

    for row in rows:
        (
            apartment_number,
            ownership_type,
            ownership_type_raw,
            full_name,
            phone_raw,
            car_model,
            car_color,
            license_plate,
            status_raw,
        ) = row

        apt = norm_apartment(apartment_number)

        if apt not in data:
            data[apt] = {
                "apartment_number": apt,
                "ownership": set(),
                "ownership_raw": set(),
                "names": set(),
                "phones": set(),
                "plates": set(),
                "models": set(),
                "colors": set(),
                "statuses": set(),
            }

        if norm_text(ownership_type):
            data[apt]["ownership"].add(norm_text(ownership_type))

        if norm_text(ownership_type_raw):
            data[apt]["ownership_raw"].add(norm_text(ownership_type_raw))

        if norm_text(full_name):
            data[apt]["names"].add(norm_text(full_name))

        if norm_text(phone_raw):
            data[apt]["phones"].add(norm_text(phone_raw))

        if norm_text(license_plate):
            data[apt]["plates"].add(norm_text(license_plate).upper())

        if norm_text(car_model):
            data[apt]["models"].add(norm_text(car_model).upper())

        if norm_text(car_color):
            data[apt]["colors"].add(norm_text(car_color).upper())

        if norm_text(status_raw):
            data[apt]["statuses"].add(norm_text(status_raw))

    return data


def fmt_set(values):
    if not values:
        return "-"
    return "; ".join(sorted(str(v) for v in values if v is not None))


def audit_tbot_quarantine():
    main_db = paths.OSBB_DB_FILE
    quarantine_db = paths.OSBB_QUARANTINE_DB_FILE

    if not main_db.exists():
        raise FileNotFoundError(f"Не найдена рабочая БД:\n{main_db}")

    if not quarantine_db.exists():
        raise FileNotFoundError(f"Не найдена карантинная БД:\n{quarantine_db}")

    report = Report()

    report.section("TBOT QUARANTINE AUDIT")
    report.add(f"Main DB       : {main_db}")
    report.add(f"Quarantine DB : {quarantine_db}")
    report.add(f"Audit time    : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    main_conn = sqlite3.connect(main_db)
    main_cur = main_conn.cursor()

    q_conn = sqlite3.connect(quarantine_db)
    q_cur = q_conn.cursor()

    main = get_main_snapshot(main_cur)
    tbot = get_tbot_snapshot(q_cur)

    main_apts = set(main.keys())
    tbot_apts = set(tbot.keys())

    report.section("GENERAL COUNTS")
    report.add(f"Main apartments        : {len(main_apts)}")
    report.add(f"TBot apartments        : {len(tbot_apts)}")
    report.add(f"In both                : {len(main_apts & tbot_apts)}")
    report.add(f"Only in TBot           : {len(tbot_apts - main_apts)}")
    report.add(f"Only in Main           : {len(main_apts - tbot_apts)}")

    report.section("APARTMENTS ONLY IN TBOT")
    only_tbot = sorted(tbot_apts - main_apts, key=lambda x: str(x))

    report.add(f"Count: {len(only_tbot)}")

    for apt in only_tbot:
        tb = tbot[apt]
        report.add(
            f"{apt:12} "
            f"ownership={fmt_set(tb['ownership_raw'])} | "
            f"name={fmt_set(tb['names'])} | "
            f"phone={fmt_set(tb['phones'])} | "
            f"plate={fmt_set(tb['plates'])} | "
            f"model={fmt_set(tb['models'])} | "
            f"color={fmt_set(tb['colors'])} | "
            f"status={fmt_set(tb['statuses'])}"
        )

    report.section("TBOT RECORDS WITH NON-NUMERIC APARTMENT NUMBERS")
    suspicious_apts = [
        apt for apt in sorted(tbot_apts, key=lambda x: str(x))
        if any(ch for ch in str(apt) if not ch.isdigit())
    ]

    report.add(f"Count: {len(suspicious_apts)}")

    for apt in suspicious_apts:
        tb = tbot[apt]
        report.add(
            f"{apt:12} "
            f"name={fmt_set(tb['names'])} | "
            f"plate={fmt_set(tb['plates'])} | "
            f"model={fmt_set(tb['models'])}"
        )

    report.section("OWNERSHIP TYPE FROM TBOT")
    ownership_rows = []

    for apt in sorted(tbot_apts & main_apts, key=lambda x: str(x)):
        tb = tbot[apt]
        if tb["ownership"] or tb["ownership_raw"]:
            ownership_rows.append(apt)

    report.add(f"Count: {len(ownership_rows)}")

    for apt in ownership_rows:
        tb = tbot[apt]
        report.add(
            f"{apt:12} ownership={fmt_set(tb['ownership'])} "
            f"raw={fmt_set(tb['ownership_raw'])}"
        )

    report.section("CAR COLORS AVAILABLE IN TBOT")
    color_rows = []

    for apt in sorted(tbot_apts & main_apts, key=lambda x: str(x)):
        tb = tbot[apt]
        mn = main[apt]

        if tb["colors"] and not mn["colors"]:
            color_rows.append(apt)

    report.add(f"Count: {len(color_rows)}")

    for apt in color_rows:
        tb = tbot[apt]
        report.add(
            f"{apt:12} color_from_tbot={fmt_set(tb['colors'])}"
        )

    report.section("LICENSE PLATE DIFFERENCES")
    plate_diff = []

    for apt in sorted(tbot_apts & main_apts, key=lambda x: str(x)):
        tb = tbot[apt]
        mn = main[apt]

        if tb["plates"] and mn["plates"] and tb["plates"] != mn["plates"]:
            plate_diff.append(apt)

    report.add(f"Count: {len(plate_diff)}")

    for apt in plate_diff:
        report.add(
            f"{apt:12} "
            f"main={fmt_set(main[apt]['plates'])} | "
            f"tbot={fmt_set(tbot[apt]['plates'])}"
        )

    report.section("CAR MODEL DIFFERENCES")
    model_diff = []

    for apt in sorted(tbot_apts & main_apts, key=lambda x: str(x)):
        tb = tbot[apt]
        mn = main[apt]

        if tb["models"] and mn["models"] and tb["models"] != mn["models"]:
            model_diff.append(apt)

    report.add(f"Count: {len(model_diff)}")

    for apt in model_diff:
        report.add(
            f"{apt:12} "
            f"main={fmt_set(main[apt]['models'])} | "
            f"tbot={fmt_set(tbot[apt]['models'])}"
        )

    report.section("NAME DIFFERENCES")
    name_diff = []

    for apt in sorted(tbot_apts & main_apts, key=lambda x: str(x)):
        tb = tbot[apt]
        mn = main[apt]

        if tb["names"] and mn["names"] and tb["names"] != mn["names"]:
            name_diff.append(apt)

    report.add(f"Count: {len(name_diff)}")

    for apt in name_diff:
        report.add(
            f"{apt:12} "
            f"main={fmt_set(main[apt]['names'])} | "
            f"tbot={fmt_set(tbot[apt]['names'])}"
        )

    report.section("PHONE DIFFERENCES")
    phone_diff = []

    for apt in sorted(tbot_apts & main_apts, key=lambda x: str(x)):
        tb = tbot[apt]
        mn = main[apt]

        if tb["phones"] and mn["phones"] and tb["phones"] != mn["phones"]:
            phone_diff.append(apt)

    report.add(f"Count: {len(phone_diff)}")

    for apt in phone_diff:
        report.add(
            f"{apt:12} "
            f"main={fmt_set(main[apt]['phones'])} | "
            f"tbot={fmt_set(tbot[apt]['phones'])}"
        )

    report.section("TBOT HAS VEHICLE, MAIN HAS NO VEHICLE")
    rows = []

    for apt in sorted(tbot_apts & main_apts, key=lambda x: str(x)):
        tb = tbot[apt]
        mn = main[apt]

        tbot_has_vehicle = bool(tb["plates"] or tb["models"])
        main_has_vehicle = bool(mn["plates"] or mn["models"])

        if tbot_has_vehicle and not main_has_vehicle:
            rows.append(apt)

    report.add(f"Count: {len(rows)}")

    for apt in rows:
        tb = tbot[apt]
        report.add(
            f"{apt:12} "
            f"plate={fmt_set(tb['plates'])} | "
            f"model={fmt_set(tb['models'])} | "
            f"color={fmt_set(tb['colors'])}"
        )

    report.section("MAIN HAS VEHICLE, TBOT HAS NO VEHICLE")
    rows = []

    for apt in sorted(tbot_apts & main_apts, key=lambda x: str(x)):
        tb = tbot[apt]
        mn = main[apt]

        tbot_has_vehicle = bool(tb["plates"] or tb["models"])
        main_has_vehicle = bool(mn["plates"] or mn["models"])

        if main_has_vehicle and not tbot_has_vehicle:
            rows.append(apt)

    report.add(f"Count: {len(rows)}")

    for apt in rows:
        mn = main[apt]
        report.add(
            f"{apt:12} "
            f"plate={fmt_set(mn['plates'])} | "
            f"model={fmt_set(mn['models'])}"
        )

    report.section("SOURCE FILES IN QUARANTINE DB")

    try:
        rows = fetch_all(
            q_cur,
            """
            SELECT source_name, file_path, records_count, imported_at, imported_by, notes
            FROM source_files
            ORDER BY imported_at DESC
            """
        )

        if rows:
            for source_name, file_path, records_count, imported_at, imported_by, notes in rows:
                report.add(f"source_name  : {source_name}")
                report.add(f"file_path    : {file_path}")
                report.add(f"records_count: {records_count}")
                report.add(f"imported_at  : {imported_at}")
                report.add(f"imported_by  : {imported_by}")
                report.add(f"notes        : {notes}")
                report.add("-" * 40)
        else:
            report.add("No source_files records.")
    except sqlite3.OperationalError as e:
        report.add(f"source_files table error: {e}")

    main_conn.close()
    q_conn.close()

    report.section("AUDIT FINISHED")

    report_file = report.save()

    print("TBot quarantine audit completed.")
    print(f"Report saved to:\n{report_file}")


if __name__ == "__main__":
    audit_tbot_quarantine()