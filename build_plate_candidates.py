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


CREATED_BY = "build_plate_candidates"


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def plate_digits4(value):
    if not value:
        return None
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    return digits if len(digits) == 4 else None


def confidence_for(task_plate, candidate_plate, match_types):
    task_plate = (task_plate or "").upper()
    candidate_plate = (candidate_plate or "").upper()

    task_digits = plate_digits4(task_plate)
    cand_digits = plate_digits4(candidate_plate)

    match_types = set(match_types)

    if "EXACT_MATCH" in match_types:
        return "VERY_HIGH", 100

    if "MISSING_LAST_CHAR" in match_types or "MISSING_FIRST_CHAR" in match_types:
        return "HIGH", 90

    if task_digits and cand_digits and task_digits == cand_digits:
        if "SAME_APARTMENT_PLATE_LIST" in match_types:
            return "HIGH", 85
        return "MEDIUM", 70

    if "CONTAINS_VALUE" in match_types:
        return "MEDIUM", 65

    if "SAME_APARTMENT_PLATE_LIST" in match_types:
        return "LOW", 40

    return "LOW", 20


def create_table(cur):
    cur.execute("""
    CREATE TABLE IF NOT EXISTS verification_candidates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,

        task_id INTEGER,

        candidate_type TEXT,
        candidate_value TEXT,
        candidate_normalized TEXT,

        confidence_label TEXT,
        confidence_score INTEGER,

        source_names TEXT,
        match_types TEXT,

        reason TEXT,
        status TEXT DEFAULT 'new',

        created_at TEXT,
        created_by TEXT,

        decided_at TEXT,
        decided_by TEXT,
        decision TEXT,
        decision_comment TEXT,

        FOREIGN KEY(task_id) REFERENCES verification_tasks(id)
    )
    """)

    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_verification_candidates_task
    ON verification_candidates(task_id)
    """)

    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_verification_candidates_status
    ON verification_candidates(status)
    """)


def clear_old_candidates(cur):
    cur.execute("""
        DELETE FROM verification_candidates
        WHERE created_by = ?
          AND status = 'new'
    """, (CREATED_BY,))


def load_tasks(cur):
    cur.execute("""
        SELECT
            id,
            apartment_number,
            main_value,
            normalized_main_value,
            task_type,
            comment
        FROM verification_tasks
        WHERE task_group = 'vehicle'
          AND field_name = 'license_plate'
          AND status IN ('new', 'in_progress')
    """)
    return cur.fetchall()


def load_evidence(cur):
    cur.execute("""
        SELECT
            task_id,
            source_name,
            evidence_value,
            normalized_value,
            match_type,
            comment
        FROM verification_evidence
        WHERE evidence_type = 'license_plate'
          AND normalized_value IS NOT NULL
    """)
    return cur.fetchall()


def is_bad_candidate(value):
    text = (value or "").upper().replace(" ", "")

    if not text:
        return True

    # квартира, ошибочно распознанная как номер: КВ 122 -> KB122
    if text.startswith("KB") and text[2:].isdigit():
        return True

    if text.startswith("KV") and text[2:].isdigit():
        return True

    # слишком короткие мусорные кандидаты
    if len(text) < 5:
        return True

    return False


def build_candidates():
    conn = sqlite3.connect(paths.OSBB_DB_FILE)
    cur = conn.cursor()

    create_table(cur)
    clear_old_candidates(cur)

    tasks = load_tasks(cur)
    evidence_rows = load_evidence(cur)

    evidence_by_task = defaultdict(list)

    for row in evidence_rows:
        (
            task_id,
            source_name,
            evidence_value,
            normalized_value,
            match_type,
            comment,
        ) = row

        # служебные пересылки не используем для кандидатов
        comment_lower = (comment or "").lower()
        if "дима_охр" in comment_lower or "дима охр" in comment_lower:
            continue

        if is_bad_candidate(normalized_value):
            continue

        evidence_by_task[task_id].append({
            "source_name": source_name,
            "evidence_value": evidence_value,
            "normalized_value": normalized_value,
            "match_type": match_type,
            "comment": comment,
        })

    inserted = 0

    for (
        task_id,
        apartment_number,
        main_value,
        normalized_main_value,
        task_type,
        task_comment,
    ) in tasks:

        task_plate = normalized_main_value or main_value
        task_digits = plate_digits4(task_plate)

        grouped = defaultdict(list)

        for ev in evidence_by_task.get(task_id, []):
            candidate = ev["normalized_value"]

            if not candidate:
                continue

            if candidate == task_plate:
                continue

            grouped[candidate].append(ev)

        for candidate, ev_list in grouped.items():
            match_types = sorted(set(ev["match_type"] for ev in ev_list))
            source_names = sorted(set(ev["source_name"] for ev in ev_list))

            confidence_label, confidence_score = confidence_for(
                task_plate,
                candidate,
                match_types,
            )

            candidate_digits = plate_digits4(candidate)

            # Слабые кандидаты той же квартиры, но с другими 4 цифрами,
            # оставляем только как LOW, но не поднимаем наверх.
            reason_parts = [
                f"task_plate={task_plate}",
                f"candidate={candidate}",
                f"task_digits={task_digits or '-'}",
                f"candidate_digits={candidate_digits or '-'}",
                f"sources={', '.join(source_names)}",
                f"match_types={', '.join(match_types)}",
            ]

            first_comments = []
            for ev in ev_list[:3]:
                first_comments.append(ev["comment"] or "")

            if first_comments:
                reason_parts.append("comments=" + " || ".join(first_comments))

            cur.execute("""
                INSERT INTO verification_candidates (
                    task_id,
                    candidate_type,
                    candidate_value,
                    candidate_normalized,
                    confidence_label,
                    confidence_score,
                    source_names,
                    match_types,
                    reason,
                    status,
                    created_at,
                    created_by
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', ?, ?)
            """, (
                task_id,
                "license_plate",
                candidate,
                candidate,
                confidence_label,
                confidence_score,
                "; ".join(source_names),
                "; ".join(match_types),
                " | ".join(reason_parts),
                now(),
                CREATED_BY,
            ))

            inserted += 1

    conn.commit()

    cur.execute("""
        SELECT confidence_label, COUNT(*)
        FROM verification_candidates
        WHERE created_by = ?
        GROUP BY confidence_label
        ORDER BY
            CASE confidence_label
                WHEN 'VERY_HIGH' THEN 1
                WHEN 'HIGH' THEN 2
                WHEN 'MEDIUM' THEN 3
                WHEN 'LOW' THEN 4
                ELSE 5
            END
    """, (CREATED_BY,))

    summary = cur.fetchall()

    cur.execute("""
        SELECT COUNT(DISTINCT task_id)
        FROM verification_candidates
        WHERE created_by = ?
          AND status = 'new'
    """, (CREATED_BY,))

    tasks_with_candidates = cur.fetchone()[0]

    conn.close()

    print("Plate candidates created:", inserted)
    print("Tasks with candidates:", tasks_with_candidates)
    print()
    print("By confidence:")
    for label, count in summary:
        print(f"{label:10}: {count}")


if __name__ == "__main__":
    build_candidates()