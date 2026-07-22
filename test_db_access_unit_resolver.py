"""
Read-only smoke test for db_access + unit_resolver integration.

Place in the OSBB project root and run:
  g:/Programming/Py/venv/Scripts/python.exe G:/Programming/Py/OSBB/test_db_access_unit_resolver.py

No database records are changed.
"""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
BOTS_DIR = ROOT / "Bots"

for folder in (ROOT, BOTS_DIR):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))

import db_access


def main() -> int:
    result = db_access.unit_resolver_db_access_self_test()

    print("=" * 96)
    print("DB_ACCESS + UNIT_RESOLVER SMOKE TEST")
    print("=" * 96)

    if not result["ok"]:
        print("FAILED:", result.get("error") or "one or more alias checks failed")
        for item in result.get("tests", []):
            print(item)
        return 1

    for item in result["tests"]:
        print(
            f"PASS: {item['alias']!r} -> {item['group_code']} | "
            f"physical: {', '.join(item['members'])}"
        )

    print()
    print("Group alias checks:", len(result["tests"]))
    print()
    print("Card checks:")

    seen_groups = set()
    for item in result["tests"]:
        group_code = item["group_code"]
        if group_code in seen_groups:
            continue
        seen_groups.add(group_code)

        context = db_access.resolve_unit_context(item["alias"])
        card = db_access.get_apartment_card(item["alias"])

        ok = (
            context["is_group"]
            and card is not None
            and card["apartment_number"] == group_code
            and len(card["lookup_numbers"]) >= 2
        )

        marker = "PASS" if ok else "FAIL"
        print(
            f"{marker}: {item['alias']!r} -> card {card['apartment_number'] if card else '-'} "
            f"| physical: {', '.join(card['lookup_numbers']) if card else '-'}"
        )

        if not ok:
            return 1

    print()
    print("READ-ONLY COMPLETED — БД НЕ ИЗМЕНЯЛАСЬ.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
