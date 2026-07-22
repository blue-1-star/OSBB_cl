from pathlib import Path
import sys
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
    format_consensus_item,
    get_db_file,
    get_quarantine_db_file,
)


def now_ts():
    return datetime.now().strftime("%Y-%m-%d_%H-%M-%S")


def write_report(report_file, tie_items, period_code, limit):
    lines = []

    lines.append("=" * 120)
    lines.append("PLATE CONSENSUS TOP TIES")
    lines.append("=" * 120)
    lines.append(f"Generated : {datetime.now():%Y-%m-%d %H:%M:%S}")
    lines.append(f"DB        : {get_db_file()}")
    lines.append(f"QDB       : {get_quarantine_db_file()}")
    lines.append(f"MODE      : {'TEST/WORK' if USE_TEST_DB else 'PROD'}")
    lines.append(f"Period    : {period_code}")
    lines.append(f"Limit     : {limit}")
    lines.append("")

    lines.append("=" * 120)
    lines.append("SUMMARY")
    lines.append("=" * 120)
    lines.append(f"Tie items shown : {len(tie_items)}")
    lines.append("")

    if not tie_items:
        lines.append("Нет спорных случаев Tie.")
    else:
        for idx, item in enumerate(tie_items, start=1):
            lines.append("-" * 120)
            lines.append(format_consensus_item(item, idx))
            lines.append("")

    report_file.write_text("\n".join(lines), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(
        description="Show only unresolved Tie cases from plate consensus report."
    )
    parser.add_argument("--period", default=DEFAULT_PERIOD_CODE)
    parser.add_argument("--limit", type=int, default=10)

    args = parser.parse_args()

    consensus = build_consensus(
        period_code=args.period,
        include_single=False,
    )

    tie_items = [item for item in consensus if item["status"] == "tie"]
    tie_items = tie_items[: args.limit]

    report_dir = paths.OSBB_EXPORTS_DIR / "billing"
    report_dir.mkdir(parents=True, exist_ok=True)

    report_file = report_dir / f"plate_consensus_top_ties_{args.period}_{now_ts()}.txt"

    write_report(
        report_file=report_file,
        tie_items=tie_items,
        period_code=args.period,
        limit=args.limit,
    )

    print("=" * 70)
    print("PLATE CONSENSUS TOP TIES")
    print("=" * 70)
    print("DB:", get_db_file())
    print("QDB:", get_quarantine_db_file())
    print("MODE:", "TEST/WORK" if USE_TEST_DB else "PROD")
    print("Period:", args.period)
    print("Tie items:", len(tie_items))
    print("")
    print("Report:")
    print(report_file)
    print("=" * 70)


if __name__ == "__main__":
    main()
