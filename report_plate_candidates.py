from pathlib import Path
import sys
import sqlite3
from datetime import datetime

OSBB_ROOT = Path(__file__).resolve().parent
PY_ROOT = OSBB_ROOT.parent

if str(PY_ROOT) not in sys.path:
    sys.path.insert(0, str(PY_ROOT))

from config import paths
from utils import apartment_sort_sql


def now_ts():
    return datetime.now().strftime("%Y-%m-%d_%H-%M-%S")


def confidence_order(label):
    order = {
        "VERY_HIGH": 1,
        "HIGH": 2,
        "MEDIUM": 3,
        "LOW": 4,
    }
    return order.get(label, 99)


def load_candidates(cur):
    cur.execute(f"""
        SELECT
            vc.id,
            vc.task_id,
            vc.candidate_normalized,
            vc.confidence_label,
            vc.confidence_score,
            vc.source_names,
            vc.match_types,
            vc.reason,

            vt.apartment_number,
            vt.main_value,
            vt.normalized_main_value,
            vt.task_type,
            vt.comment

        FROM verification_candidates vc
        JOIN verification_tasks vt
            ON vt.id = vc.task_id

        WHERE vc.status = 'new'

        ORDER BY
            CASE vc.confidence_label
                WHEN 'VERY_HIGH' THEN 1
                WHEN 'HIGH' THEN 2
                WHEN 'MEDIUM' THEN 3
                WHEN 'LOW' THEN 4
                ELSE 5
            END,

            vc.confidence_score DESC,

            {apartment_sort_sql("vt.apartment_number")},

            vc.task_id
    """)

    return cur.fetchall()


def write_section(lines, title):
    lines.append("")
    lines.append("=" * 80)
    lines.append(title)
    lines.append("=" * 80)


def main():
    conn = sqlite3.connect(paths.OSBB_DB_FILE)
    cur = conn.cursor()

    rows = load_candidates(cur)

    report_dir = paths.OSBB_EXPORTS_DIR / "audits"
    report_dir.mkdir(parents=True, exist_ok=True)

    report_file = (
        report_dir /
        f"plate_candidates_report_{now_ts()}.txt"
    )

    lines = []

    lines.append("=" * 80)
    lines.append("PLATE CANDIDATES REPORT")
    lines.append("=" * 80)
    lines.append(
        f"Generated: {datetime.now():%Y-%m-%d %H:%M:%S}"
    )
    lines.append("")

    cur.execute("""
        SELECT confidence_label, COUNT(*)
        FROM verification_candidates
        WHERE status = 'new'
        GROUP BY confidence_label
    """)

    stats = {
        row[0]: row[1]
        for row in cur.fetchall()
    }

    lines.append("SUMMARY")
    lines.append("-" * 80)
    lines.append(
        f"VERY_HIGH : {stats.get('VERY_HIGH', 0)}"
    )
    lines.append(
        f"HIGH      : {stats.get('HIGH', 0)}"
    )
    lines.append(
        f"MEDIUM    : {stats.get('MEDIUM', 0)}"
    )
    lines.append(
        f"LOW       : {stats.get('LOW', 0)}"
    )

    sections = [
        "VERY_HIGH",
        "HIGH",
        "MEDIUM",
        "LOW",
    ]

    for section in sections:

        section_rows = [
            row for row in rows
            if row[3] == section
        ]

        if not section_rows:
            continue

        write_section(
            lines,
            f"{section} CONFIDENCE"
        )

        current_task = None

        for row in section_rows:

            (
                candidate_id,
                task_id,
                candidate_plate,
                confidence_label,
                confidence_score,
                source_names,
                match_types,
                reason,

                apartment_number,
                main_value,
                normalized_main_value,
                task_type,
                task_comment,
            ) = row

            if current_task != task_id:

                current_task = task_id

                lines.append("")
                lines.append("-" * 80)
                lines.append(
                    f"Task ID      : {task_id}"
                )
                lines.append(
                    f"Apartment    : {apartment_number}"
                )
                lines.append(
                    f"Task type    : {task_type}"
                )
                lines.append(
                    f"Current plate: {normalized_main_value or main_value}"
                )
                lines.append(
                    f"Comment      : {task_comment}"
                )
                lines.append("")
                lines.append("Candidates:")

            lines.append(
                f"  [{confidence_score:3}] "
                f"{candidate_plate}"
            )

            lines.append(
                f"       Sources    : {source_names}"
            )

            lines.append(
                f"       Match type : {match_types}"
            )

    write_section(
        lines,
        "TOP HIGH CONFIDENCE CANDIDATES"
    )

    cur.execute("""
        SELECT
            vc.task_id,
            vt.apartment_number,
            vt.normalized_main_value,
            vc.candidate_normalized,
            vc.confidence_score,
            vc.source_names,
            vc.match_types
        FROM verification_candidates vc
        JOIN verification_tasks vt
            ON vt.id = vc.task_id
        WHERE vc.confidence_label IN ('VERY_HIGH', 'HIGH')
          AND vc.status = 'new'
        ORDER BY
            vc.confidence_score DESC,
            vt.apartment_number
    """)

    for (
        task_id,
        apartment_number,
        task_plate,
        candidate_plate,
        confidence_score,
        source_names,
        match_types,
    ) in cur.fetchall():

        lines.append(
            f"Task {task_id:3} | "
            f"apt={str(apartment_number):6} | "
            f"{task_plate:12} -> "
            f"{candidate_plate:12} | "
            f"{confidence_score:3} | "
            f"{source_names} | "
            f"{match_types}"
        )

    report_file.write_text(
        "\n".join(lines),
        encoding="utf-8"
    )

    conn.close()

    print("Plate candidates report created.")
    print("Report:")
    print(report_file)


if __name__ == "__main__":
    main()