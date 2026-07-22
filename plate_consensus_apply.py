from pathlib import Path
import sys
import sqlite3
import argparse
from datetime import datetime

OSBB_ROOT = Path(__file__).resolve().parent
PY_ROOT = OSBB_ROOT.parent

if str(PY_ROOT) not in sys.path:
    sys.path.insert(0, str(PY_ROOT))

from config import paths, USE_TEST_DB

from plate_consensus_report import (
    DEFAULT_PERIOD_CODE,
    build_consensus,
    get_db_file,
    normalize_plate,
)


def now_ts():
    return datetime.now().strftime("%Y-%m-%d_%H-%M-%S")


def table_exists(cur, table_name):
    cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    )
    return cur.fetchone() is not None


def table_columns(cur, table_name):
    cur.execute(f"PRAGMA table_info({table_name})")
    return [row[1] for row in cur.fetchall()]


def insert_operator_audit(
    cur,
    operator_id,
    table_name,
    row_id,
    field_name,
    old_value,
    new_value,
    source_context,
    comment="",
):
    if not table_exists(cur, "operator_audit_log"):
        return

    columns = table_columns(cur, "operator_audit_log")

    values = {
        "operator_id": str(operator_id or "plate_consensus_apply"),
        "user_id": str(operator_id or "plate_consensus_apply"),
        "actor_type": "system",
        "action_type": "plate_consensus_apply",
        "table_name": table_name,
        "row_id": str(row_id or ""),
        "field_name": field_name,
        "old_value": str(old_value or ""),
        "new_value": str(new_value or ""),
        "action_status": "applied",
        "review_status": "pending",
        "source_context": source_context,
        "comment": comment,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

    insert_cols = [k for k in values if k in columns]

    if not insert_cols:
        return

    placeholders = ",".join("?" for _ in insert_cols)

    cur.execute(f"""
        INSERT INTO operator_audit_log ({", ".join(insert_cols)})
        VALUES ({placeholders})
    """, tuple(values[k] for k in insert_cols))


def get_paper_fact(item):
    """
    Returns the paper/main vehicle fact if present.
    Consensus correction updates only vehicles from paper/main DB.
    """
    for row in item["vote_rows"]:
        for fact in row["facts"]:
            if fact["source"] == "paper":
                return fact
    return None


def get_vote_row(item, plate):
    plate = normalize_plate(plate)
    for row in item["vote_rows"]:
        if normalize_plate(row["plate"]) == plate:
            return row
    return None


def should_apply_item(item):
    if item["status"] != "majority":
        return False, "not_majority"

    recommendation = normalize_plate(item.get("recommendation"))

    if not recommendation:
        return False, "no_recommendation"

    paper_fact = get_paper_fact(item)

    if not paper_fact:
        return False, "no_paper_vehicle"

    old_plate = normalize_plate(paper_fact["plate"])

    if old_plate == recommendation:
        return False, "paper_already_ok"

    vehicle_id = paper_fact.get("ref_id") or ""

    if not str(vehicle_id).isdigit():
        return False, "no_vehicle_id"

    return True, "ok"


def build_apply_plan(period_code=DEFAULT_PERIOD_CODE):
    consensus = build_consensus(
        period_code=period_code,
        include_single=False,
    )

    plan = []

    for item in consensus:
        apply_ok, reason = should_apply_item(item)

        paper_fact = get_paper_fact(item)
        recommendation = normalize_plate(item.get("recommendation"))
        recommended_row = get_vote_row(item, recommendation) if recommendation else None

        plan.append({
            "item": item,
            "apply_ok": apply_ok,
            "skip_reason": reason,
            "vehicle_id": paper_fact.get("ref_id") if paper_fact else "",
            "apartment_number": ", ".join(item.get("apartments") or []),
            "old_plate": normalize_plate(paper_fact["plate"]) if paper_fact else "",
            "new_plate": recommendation,
            "status": item.get("status"),
            "recommendation_reason": item.get("recommendation_reason") or "",
            "recommended_sources": ", ".join(recommended_row["sources"]) if recommended_row else "",
            "models": ", ".join(item.get("models") or []),
        })

    return plan


def apply_plan(plan, operator_id="plate_consensus_apply"):
    db_file = get_db_file()
    conn = sqlite3.connect(db_file)
    cur = conn.cursor()

    vehicle_cols = table_columns(cur, "vehicles")

    updated = 0
    skipped = 0

    for row in plan:
        if not row["apply_ok"]:
            skipped += 1
            continue

        vehicle_id = int(row["vehicle_id"])
        old_plate = row["old_plate"]
        new_plate = row["new_plate"]

        sets = []
        params = []

        if "license_plate" in vehicle_cols:
            sets.append("license_plate = ?")
            params.append(new_plate)

        if "license_plate_normalized" in vehicle_cols:
            sets.append("license_plate_normalized = ?")
            params.append(new_plate)

        if not sets:
            skipped += 1
            continue

        params.append(vehicle_id)

        cur.execute(f"""
            UPDATE vehicles
            SET {", ".join(sets)}
            WHERE id = ?
        """, tuple(params))

        if cur.rowcount:
            updated += 1

            insert_operator_audit(
                cur=cur,
                operator_id=operator_id,
                table_name="vehicles",
                row_id=vehicle_id,
                field_name="license_plate",
                old_value=old_plate,
                new_value=new_plate,
                source_context=(
                    f"period={DEFAULT_PERIOD_CODE}; "
                    f"apt={row['apartment_number']}; "
                    f"sources={row['recommended_sources']}; "
                    f"reason={row['recommendation_reason']}"
                ),
                comment="Applied plate consensus majority recommendation",
            )
        else:
            skipped += 1

    conn.commit()
    conn.close()

    return {
        "updated": updated,
        "skipped": skipped,
    }


def write_report(report_file, plan, period_code, apply_mode, apply_result=None):
    lines = []

    lines.append("=" * 120)
    lines.append("PLATE CONSENSUS APPLY — DRY RUN" if not apply_mode else "PLATE CONSENSUS APPLY — APPLY")
    lines.append("=" * 120)
    lines.append(f"Generated : {datetime.now():%Y-%m-%d %H:%M:%S}")
    lines.append(f"DB        : {get_db_file()}")
    lines.append(f"MODE      : {'TEST/WORK' if USE_TEST_DB else 'PROD'}")
    lines.append(f"Period    : {period_code}")
    lines.append("")

    apply_rows = [r for r in plan if r["apply_ok"]]
    skipped_rows = [r for r in plan if not r["apply_ok"]]

    lines.append("=" * 120)
    lines.append("SUMMARY")
    lines.append("=" * 120)
    lines.append(f"Plan rows          : {len(plan)}")
    lines.append(f"Will update        : {len(apply_rows)}")
    lines.append(f"Skipped            : {len(skipped_rows)}")

    if apply_result:
        lines.append(f"Updated in DB      : {apply_result['updated']}")
        lines.append(f"Skipped in apply   : {apply_result['skipped']}")

    lines.append("")

    lines.append("=" * 120)
    lines.append("WILL UPDATE")
    lines.append("=" * 120)
    lines.append("vehicle_id | apt | old_plate | new_plate | sources | reason | model")
    lines.append("-" * 120)

    if not apply_rows:
        lines.append("нет записей к обновлению")
    else:
        for row in apply_rows:
            lines.append(
                f"{row['vehicle_id']} | "
                f"{row['apartment_number']} | "
                f"{row['old_plate']} | "
                f"{row['new_plate']} | "
                f"{row['recommended_sources']} | "
                f"{row['recommendation_reason']} | "
                f"{row['models']}"
            )

    lines.append("")
    lines.append("=" * 120)
    lines.append("SKIPPED")
    lines.append("=" * 120)
    lines.append("apt | old_plate | recommendation | status | reason")
    lines.append("-" * 120)

    if not skipped_rows:
        lines.append("нет пропусков")
    else:
        for row in skipped_rows:
            lines.append(
                f"{row['apartment_number']} | "
                f"{row['old_plate'] or '-'} | "
                f"{row['new_plate'] or '-'} | "
                f"{row['status']} | "
                f"{row['skip_reason']}"
            )

    if not apply_mode:
        lines.append("")
        lines.append("DRY RUN ONLY. Database was not changed.")
        lines.append("To apply, run with --apply")

    report_file.write_text("\n".join(lines), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(
        description="Apply majority plate consensus recommendations to vehicles table."
    )
    parser.add_argument("--period", default=DEFAULT_PERIOD_CODE)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--operator-id", default="plate_consensus_apply")

    args = parser.parse_args()

    plan = build_apply_plan(period_code=args.period)

    apply_result = None
    if args.apply:
        apply_result = apply_plan(
            plan,
            operator_id=args.operator_id,
        )

    report_dir = paths.OSBB_EXPORTS_DIR / "billing"
    report_dir.mkdir(parents=True, exist_ok=True)

    mode = "apply" if args.apply else "dry_run"
    report_file = report_dir / f"plate_consensus_apply_{args.period}_{mode}_{now_ts()}.txt"

    write_report(
        report_file=report_file,
        plan=plan,
        period_code=args.period,
        apply_mode=args.apply,
        apply_result=apply_result,
    )

    apply_rows = [r for r in plan if r["apply_ok"]]

    print("=" * 70)
    print("PLATE CONSENSUS APPLY")
    print("=" * 70)
    print("DB:", get_db_file())
    print("MODE:", "TEST/WORK" if USE_TEST_DB else "PROD")
    print("Period:", args.period)
    print("Apply:", args.apply)
    print("Will update:", len(apply_rows))

    if apply_result:
        print("Updated:", apply_result["updated"])
        print("Skipped:", apply_result["skipped"])

    print("")
    print("Report:")
    print(report_file)
    print("=" * 70)


if __name__ == "__main__":
    main()
