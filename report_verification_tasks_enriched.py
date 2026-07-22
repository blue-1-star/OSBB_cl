from pathlib import Path
import sys
import sqlite3
from datetime import datetime
from collections import defaultdict

OSBB_ROOT = Path(__file__).resolve().parent
PY_ROOT = OSBB_ROOT.parent

if str(PY_ROOT) not in sys.path:
    sys.path.insert(0, str(PY_ROOT))

from config import paths
from utils import apartment_sort_sql


SERVICE_CHAT_KEYWORDS = [
    "дима_охр",
    "дима охр",
]


def now_ts():
    return datetime.now().strftime("%Y-%m-%d_%H-%M-%S")


def is_service_evidence(comment):
    text = (comment or "").lower()
    return any(k in text for k in SERVICE_CHAT_KEYWORDS)


def is_bad_plate_like(value):
    """
    Отсекаем явный мусор типа:
    КВ 122 -> KB122
    """
    text = (value or "").upper().replace(" ", "")

    if text.startswith("KB") and text[2:].isdigit():
        return True

    if text.startswith("KV") and text[2:].isdigit():
        return True

    return False


def main():
    conn = sqlite3.connect(paths.OSBB_DB_FILE)
    cur = conn.cursor()

    report_dir = paths.OSBB_EXPORTS_DIR / "audits"
    report_dir.mkdir(parents=True, exist_ok=True)

    report_file = report_dir / f"verification_tasks_enriched_{now_ts()}.txt"

    lines = []

    lines.append("=" * 80)
    lines.append("VERIFICATION TASKS ENRICHED REPORT")
    lines.append("=" * 80)
    lines.append(f"Generated: {datetime.now():%Y-%m-%d %H:%M:%S}")
    lines.append("")

    cur.execute("""
        SELECT COUNT(*)
        FROM verification_tasks
        WHERE status IN ('new', 'in_progress')
    """)
    open_tasks = cur.fetchone()[0]

    cur.execute("""
        SELECT COUNT(DISTINCT task_id)
        FROM verification_evidence
    """)
    tasks_with_evidence = cur.fetchone()[0]

    lines.append("=" * 80)
    lines.append("SUMMARY")
    lines.append("=" * 80)
    lines.append(f"Open tasks          : {open_tasks}")
    lines.append(f"Tasks with evidence : {tasks_with_evidence}")
    lines.append("")

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
        ORDER BY
            {apartment_sort_sql("apartment_number")},
            id
    """)

    tasks = cur.fetchall()

    cur.execute("""
        SELECT
            task_id,
            source_name,
            source_table,
            source_record_id,
            evidence_value,
            normalized_value,
            match_type,
            comment
        FROM verification_evidence
        ORDER BY
            source_name,
            match_type,
            normalized_value
    """)

    evidence_by_task = defaultdict(list)

    for row in cur.fetchall():
        (
            task_id,
            source_name,
            source_table,
            source_record_id,
            evidence_value,
            normalized_value,
            match_type,
            comment,
        ) = row

        if is_service_evidence(comment):
            continue

        if is_bad_plate_like(evidence_value) or is_bad_plate_like(normalized_value):
            continue

        evidence_by_task[task_id].append({
            "source_name": source_name,
            "source_table": source_table,
            "source_record_id": source_record_id,
            "evidence_value": evidence_value,
            "normalized_value": normalized_value,
            "match_type": match_type,
            "comment": comment,
        })

    lines.append("=" * 80)
    lines.append("TASKS WITH EVIDENCE")
    lines.append("=" * 80)

    with_evidence_count = 0

    for task in tasks:
        (
            task_id,
            apartment_number,
            task_type,
            priority,
            status,
            main_value,
            normalized_main_value,
            suggestion,
            task_comment,
        ) = task

        ev_list = evidence_by_task.get(task_id, [])

        if not ev_list:
            continue

        with_evidence_count += 1

        lines.append("-" * 80)
        lines.append(f"Task ID      : {task_id}")
        lines.append(f"Apartment    : {apartment_number}")
        lines.append(f"Type         : {task_type}")
        lines.append(f"Priority     : {priority}")
        lines.append(f"Status       : {status}")
        lines.append(f"Value        : {main_value}")
        lines.append(f"Normalized   : {normalized_main_value}")
        lines.append(f"Suggestion   : {suggestion}")
        lines.append(f"Task comment : {task_comment}")
        lines.append("")
        lines.append(f"Evidence count: {len(ev_list)}")

        grouped = defaultdict(list)

        for ev in ev_list:
            key = (ev["source_name"], ev["match_type"])
            grouped[key].append(ev)

        for (source_name, match_type), items in grouped.items():
            lines.append("")
            lines.append(f"[{source_name}] {match_type}")

            # Убираем повторы одинаковых normalized_value
            seen_values = set()

            for ev in items:
                norm = ev["normalized_value"] or ev["evidence_value"]

                if norm in seen_values:
                    continue

                seen_values.add(norm)

                lines.append(
                    f"  - {ev['evidence_value']} "
                    f"-> {ev['normalized_value']} "
                    f"(record={ev['source_record_id']})"
                )
                lines.append(f"    {ev['comment']}")

        lines.append("")

    lines.append("")
    lines.append("=" * 80)
    lines.append("OPEN TASKS WITHOUT CLEAN EVIDENCE")
    lines.append("=" * 80)

    without_count = 0

    for task in tasks:
        (
            task_id,
            apartment_number,
            task_type,
            priority,
            status,
            main_value,
            normalized_main_value,
            suggestion,
            task_comment,
        ) = task

        if evidence_by_task.get(task_id):
            continue

        without_count += 1

        lines.append(
            f"Task {task_id:4} | apt={str(apartment_number):6} | "
            f"{task_type:30} | value={main_value} | "
            f"norm={normalized_main_value} | {task_comment}"
        )

    lines.insert(10, f"Tasks with clean evidence: {with_evidence_count}")
    lines.insert(11, f"Tasks without clean evidence: {without_count}")
    lines.insert(12, "")

    report_file.write_text("\n".join(lines), encoding="utf-8")

    conn.close()

    print("Enriched verification report created.")
    print("Tasks with clean evidence:", with_evidence_count)
    print("Tasks without clean evidence:", without_count)
    print("Report:")
    print(report_file)


if __name__ == "__main__":
    main()