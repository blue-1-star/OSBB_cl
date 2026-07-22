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


def main():
    conn = sqlite3.connect(paths.OSBB_DB_FILE)
    cur = conn.cursor()

    report_dir = paths.OSBB_EXPORTS_DIR / "audits"
    report_dir.mkdir(parents=True, exist_ok=True)

    report_file = report_dir / f"verification_evidence_report_{now_ts()}.txt"

    lines = []
    lines.append("=" * 80)
    lines.append("VERIFICATION EVIDENCE REPORT")
    lines.append("=" * 80)
    lines.append(f"Generated: {datetime.now():%Y-%m-%d %H:%M:%S}")
    lines.append("")

    cur.execute("""
        SELECT COUNT(*)
        FROM verification_evidence
    """)
    total_evidence = cur.fetchone()[0]

    cur.execute("""
        SELECT COUNT(DISTINCT task_id)
        FROM verification_evidence
    """)
    tasks_with_evidence = cur.fetchone()[0]

    cur.execute("""
        SELECT COUNT(*)
        FROM verification_tasks
        WHERE status IN ('new', 'in_progress')
    """)
    open_tasks = cur.fetchone()[0]

    lines.append("=" * 80)
    lines.append("SUMMARY")
    lines.append("=" * 80)
    lines.append(f"Open tasks          : {open_tasks}")
    lines.append(f"Evidence records    : {total_evidence}")
    lines.append(f"Tasks with evidence : {tasks_with_evidence}")
    lines.append("")

    lines.append("=" * 80)
    lines.append("EVIDENCE BY MATCH TYPE")
    lines.append("=" * 80)

    cur.execute("""
        SELECT match_type, COUNT(*)
        FROM verification_evidence
        GROUP BY match_type
        ORDER BY COUNT(*) DESC
    """)

    for match_type, count in cur.fetchall():
        lines.append(f"{match_type:25} : {count}")

    lines.append("")
    lines.append("=" * 80)
    lines.append("TASKS WITH TELEGRAM EVIDENCE")
    lines.append("=" * 80)

    cur.execute(f"""
        SELECT
            vt.id,
            vt.apartment_number,
            vt.task_type,
            vt.main_value,
            vt.normalized_main_value,
            vt.suggestion,
            vt.comment,

            ve.source_name,
            ve.evidence_value,
            ve.normalized_value,
            ve.match_type,
            ve.comment

        FROM verification_tasks vt
        JOIN verification_evidence ve
            ON ve.task_id = vt.id

        WHERE ve.source_name = 'telegram'

        ORDER BY
            {apartment_sort_sql("vt.apartment_number")},
            vt.id
    """)

    rows = cur.fetchall()

    if not rows:
        lines.append("No Telegram evidence found.")
    else:
        for row in rows:
            (
                task_id,
                apartment_number,
                task_type,
                main_value,
                normalized_main_value,
                suggestion,
                task_comment,
                source_name,
                evidence_value,
                evidence_normalized,
                match_type,
                evidence_comment,
            ) = row

            lines.append("-" * 80)
            lines.append(f"Task ID       : {task_id}")
            lines.append(f"Apartment     : {apartment_number}")
            lines.append(f"Task type     : {task_type}")
            lines.append(f"Task value    : {main_value}")
            lines.append(f"Task normalized: {normalized_main_value}")
            lines.append(f"Suggestion    : {suggestion}")
            lines.append(f"Task comment  : {task_comment}")
            lines.append("")
            lines.append(f"Evidence src  : {source_name}")
            lines.append(f"Evidence value: {evidence_value}")
            lines.append(f"Evidence norm : {evidence_normalized}")
            lines.append(f"Match type    : {match_type}")
            lines.append(f"Evidence note : {evidence_comment}")

    lines.append("")
    lines.append("=" * 80)
    lines.append("OPEN TASKS WITHOUT EVIDENCE")
    lines.append("=" * 80)

    cur.execute(f"""
        SELECT
            vt.id,
            vt.apartment_number,
            vt.task_type,
            vt.main_value,
            vt.normalized_main_value,
            vt.comment
        FROM verification_tasks vt
        LEFT JOIN verification_evidence ve
            ON ve.task_id = vt.id
        WHERE vt.status IN ('new', 'in_progress')
          AND ve.id IS NULL
        ORDER BY
            vt.priority,
            {apartment_sort_sql("vt.apartment_number")},
            vt.id
    """)

    rows = cur.fetchall()

    lines.append(f"Count: {len(rows)}")
    lines.append("")

    for (
        task_id,
        apartment_number,
        task_type,
        main_value,
        normalized_main_value,
        comment,
    ) in rows:
        lines.append(
            f"Task {task_id:4} | apt={str(apartment_number):6} | "
            f"{task_type:30} | value={main_value} | norm={normalized_main_value} | {comment}"
        )

    report_file.write_text("\n".join(lines), encoding="utf-8")

    conn.close()

    print("Verification evidence report created.")
    print("Report:")
    print(report_file)


if __name__ == "__main__":
    main()