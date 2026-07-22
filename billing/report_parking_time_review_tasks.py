from pathlib import Path
import sys
import sqlite3
from datetime import datetime
from collections import defaultdict

MODULE_DIR = Path(__file__).resolve().parent
OSBB_ROOT = MODULE_DIR.parent
PY_ROOT = OSBB_ROOT.parent

for p in (OSBB_ROOT, PY_ROOT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from config import paths
from utils import apartment_sort_sql


def now_ts():
    return datetime.now().strftime("%Y-%m-%d_%H-%M-%S")


def get_telegram_facts():
    result = defaultdict(list)

    if not paths.OSBB_TELEGRAM_DB_FILE.exists():
        return result

    conn = sqlite3.connect(paths.OSBB_TELEGRAM_DB_FILE)
    cur = conn.cursor()

    cur.execute("""
        SELECT
            apartment_number,
            license_plate_normalized,
            person_name,
            phone_normalized,
            car_model,
            comment
        FROM telegram_facts
        WHERE fact_type = 'vehicle_info'
          AND apartment_number IS NOT NULL
    """)

    for apt, plate, person, phone, model, comment in cur.fetchall():
        result[str(apt)].append({
            "plate": plate,
            "person": person,
            "phone": phone,
            "model": model,
            "comment": comment,
        })

    conn.close()
    return result


def main():
    conn = sqlite3.connect(paths.OSBB_DB_FILE)
    cur = conn.cursor()

    telegram_by_apt = get_telegram_facts()

    report_dir = paths.OSBB_EXPORTS_DIR / "audits"
    report_dir.mkdir(parents=True, exist_ok=True)

    report_file = report_dir / f"parking_time_review_tasks_{now_ts()}.txt"

    lines = []
    lines.append("=" * 80)
    lines.append("PARKING TIME REVIEW TASKS REPORT")
    lines.append("=" * 80)
    lines.append(f"Generated: {datetime.now():%Y-%m-%d %H:%M:%S}")
    lines.append("")

    cur.execute("""
        SELECT COUNT(*)
        FROM parking_time_review_tasks
        WHERE status = 'new'
    """)
    total = cur.fetchone()[0]

    cur.execute("""
        SELECT COUNT(DISTINCT apartment_number)
        FROM parking_time_review_tasks
        WHERE status = 'new'
    """)
    apt_count = cur.fetchone()[0]

    lines.append("=" * 80)
    lines.append("SUMMARY")
    lines.append("=" * 80)
    lines.append(f"Open tasks        : {total}")
    lines.append(f"Apartments affected: {apt_count}")
    lines.append("")

    cur.execute(f"""
        SELECT
            id,
            apartment_number,
            vehicle_id,
            license_plate,
            car_model,
            current_parking_time,
            task_type,
            priority,
            source_name,
            source_details
        FROM parking_time_review_tasks
        WHERE status = 'new'
        ORDER BY
            {apartment_sort_sql("apartment_number")},
            vehicle_id,
            id
    """)

    tasks = cur.fetchall()

    tasks_by_apt = defaultdict(list)

    for row in tasks:
        tasks_by_apt[str(row[1])].append(row)

    for apt in sorted(
        tasks_by_apt.keys(),
        key=lambda x: (0, int(x)) if x.isdigit() else (1, x),
    ):
        lines.append("=" * 80)
        lines.append(f"APARTMENT {apt}")
        lines.append("=" * 80)

        cur.execute("""
            SELECT
                v.id,
                v.license_plate_normalized,
                v.license_plate,
                v.car_model_normalized,
                v.car_model,
                v.parking_time
            FROM vehicles v
            JOIN apartments a
                ON a.id = v.apartment_id
            WHERE a.apartment_number = ?
            ORDER BY v.id
        """, (apt,))

        all_vehicles = cur.fetchall()

        lines.append("Vehicles in DB:")
        for (
            vehicle_id,
            plate_norm,
            plate_raw,
            model_norm,
            model_raw,
            parking_time,
        ) in all_vehicles:
            lines.append(
                f"  id={vehicle_id} | "
                f"{plate_norm or plate_raw or '-'} | "
                f"{model_norm or model_raw or '-'} | "
                f"parking_time={parking_time or '-'}"
            )

        lines.append("")
        lines.append("Open review tasks:")
        for (
            task_id,
            apartment_number,
            vehicle_id,
            license_plate,
            car_model,
            current_parking_time,
            task_type,
            priority,
            source_name,
            source_details,
        ) in tasks_by_apt[apt]:
            lines.append(
                f"  Task {task_id} | "
                f"vehicle_id={vehicle_id} | "
                f"{license_plate or '-'} | "
                f"{car_model or '-'} | "
                f"type={task_type} | "
                f"priority={priority}"
            )
            lines.append(f"    current parking_time: {current_parking_time or '-'}")
            lines.append(f"    source: {source_name} | {source_details}")

        tg_facts = telegram_by_apt.get(apt, [])

        lines.append("")
        lines.append("Telegram hints:")
        if tg_facts:
            seen = set()
            for f in tg_facts:
                key = (
                    f["plate"],
                    f["person"],
                    f["phone"],
                    f["model"],
                )
                if key in seen:
                    continue
                seen.add(key)

                lines.append(
                    f"  plate={f['plate'] or '-'} | "
                    f"person={f['person'] or '-'} | "
                    f"phone={f['phone'] or '-'} | "
                    f"model={f['model'] or '-'}"
                )
                if f["comment"]:
                    lines.append(f"    {f['comment']}")
        else:
            lines.append("  -")

        lines.append("")
        lines.append("Operator decision options:")
        lines.append("  [Day] [Night] [Ask owner] [Skip]")
        lines.append("")

    report_file.write_text("\n".join(lines), encoding="utf-8")

    conn.close()

    print("Parking time review tasks report created.")
    print("Report:")
    print(report_file)


if __name__ == "__main__":
    main()