from pathlib import Path
import sys
import sqlite3
import re
from datetime import datetime
from utils import (
    norm_text,
    normalize_phone,
    normalize_plate,
    normalize_color,
    normalize_car_model,
)
OSBB_ROOT = Path(__file__).resolve().parent
PY_ROOT = OSBB_ROOT.parent

if str(PY_ROOT) not in sys.path:
    sys.path.insert(0, str(PY_ROOT))

from config import paths
from utils import norm_text

def now_ts():
    return datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

class Report:
    def __init__(self):
        self.report_dir = paths.OSBB_EXPORTS_DIR / "audits"
        self.report_dir.mkdir(parents=True, exist_ok=True)
        self.report_file = self.report_dir / f"normalization_audit_{now_ts()}.txt"
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


def normalize_main_db(report):
    conn = sqlite3.connect(paths.OSBB_DB_FILE)
    cur = conn.cursor()

    cur.execute("""
        SELECT id, license_plate, car_model, car_color
        FROM vehicles
    """)
    rows = cur.fetchall()

    unknown_models = set()
    unknown_colors = set()
    suspicious_plates = []

    updated = 0

    for vehicle_id, plate, model, color in rows:
        plate_norm, plate_status = normalize_plate(plate)
        model_norm = normalize_car_model(model)
        color_norm = normalize_color(color)

        if norm_text(model) and not model_norm:
            unknown_models.add(norm_text(model))

        if norm_text(color) and not color_norm:
            unknown_colors.add(norm_text(color))

        if plate_status == "SUSPICIOUS":
            suspicious_plates.append((vehicle_id, plate, plate_norm))

        cur.execute("""
            UPDATE vehicles
            SET license_plate_normalized = ?,
                plate_format_status = ?,
                car_model_normalized = ?,
                car_color_normalized = ?
            WHERE id = ?
        """, (
            plate_norm,
            plate_status,
            model_norm,
            color_norm,
            vehicle_id,
        ))

        updated += 1

    conn.commit()
    conn.close()

    report.section("MAIN DB NORMALIZATION")
    report.add(f"Vehicles updated: {updated}")

    report.section("MAIN DB UNKNOWN CAR MODELS")
    report.add(f"Count: {len(unknown_models)}")
    for item in sorted(unknown_models):
        report.add(item)

    report.section("MAIN DB UNKNOWN COLORS")
    report.add(f"Count: {len(unknown_colors)}")
    for item in sorted(unknown_colors):
        report.add(item)

    report.section("MAIN DB SUSPICIOUS PLATES")
    report.add(f"Count: {len(suspicious_plates)}")
    for vehicle_id, raw, normalized in suspicious_plates:
        report.add(f"id={vehicle_id} raw={raw} normalized={normalized}")


def normalize_quarantine_db(report):
    conn = sqlite3.connect(paths.OSBB_QUARANTINE_DB_FILE)
    cur = conn.cursor()

    cur.execute("""
        SELECT id, license_plate, car_model, car_color, phone_raw
        FROM tbot_parking_import
    """)
    rows = cur.fetchall()

    unknown_models = set()
    unknown_colors = set()
    suspicious_plates = []

    updated = 0

    for row_id, plate, model, color, phone_raw in rows:
        plate_norm, plate_status = normalize_plate(plate)
        model_norm = normalize_car_model(model)
        color_norm = normalize_color(color)
        phone_norm = normalize_phone(phone_raw)

        if norm_text(model) and not model_norm:
            unknown_models.add(norm_text(model))

        if norm_text(color) and not color_norm:
            unknown_colors.add(norm_text(color))

        if plate_status == "SUSPICIOUS":
            suspicious_plates.append((row_id, plate, plate_norm))

        cur.execute("""
            UPDATE tbot_parking_import
            SET license_plate_normalized = ?,
                plate_format_status = ?,
                car_model_normalized = ?,
                car_color_normalized = ?,
                phone_normalized = ?
            WHERE id = ?
        """, (
            plate_norm,
            plate_status,
            model_norm,
            color_norm,
            phone_norm,
            row_id,
        ))

        updated += 1

    conn.commit()
    conn.close()

    report.section("QUARANTINE DB NORMALIZATION")
    report.add(f"TBot records updated: {updated}")

    report.section("QUARANTINE UNKNOWN CAR MODELS")
    report.add(f"Count: {len(unknown_models)}")
    for item in sorted(unknown_models):
        report.add(item)

    report.section("QUARANTINE UNKNOWN COLORS")
    report.add(f"Count: {len(unknown_colors)}")
    for item in sorted(unknown_colors):
        report.add(item)

    report.section("QUARANTINE SUSPICIOUS PLATES")
    report.add(f"Count: {len(suspicious_plates)}")
    for row_id, raw, normalized in suspicious_plates:
        report.add(f"id={row_id} raw={raw} normalized={normalized}")


def main():
    report = Report()

    report.section("NORMALIZATION AUDIT")
    report.add(f"Main DB       : {paths.OSBB_DB_FILE}")
    report.add(f"Quarantine DB : {paths.OSBB_QUARANTINE_DB_FILE}")
    report.add(f"Time          : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    normalize_main_db(report)
    normalize_quarantine_db(report)

    report.section("NORMALIZATION FINISHED")

    report_file = report.save()

    print("Normalization completed.")
    print(f"Report saved to:\n{report_file}")


if __name__ == "__main__":
    main()