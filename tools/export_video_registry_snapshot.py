#!/usr/bin/env python3
"""Export the current vehicle registry fields used by the video report builder."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import paths


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    with sqlite3.connect(paths.OSBB_DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        rows = [dict(row) for row in conn.execute("""
            SELECT v.license_plate_normalized AS plate_normalized,
                   a.apartment_number,
                   COALESCE((SELECT group_concat(name, '; ')
                             FROM (SELECT DISTINCT TRIM(p.full_name) AS name
                                   FROM persons p WHERE p.apartment_id = a.id
                                     AND TRIM(COALESCE(p.full_name, '')) <> '')), '') AS full_name,
                   COALESCE(v.car_model, v.car_model_normalized, '') AS registry_model
            FROM vehicles v
            LEFT JOIN apartments a ON a.id = v.apartment_id
            WHERE v.license_plate_normalized IS NOT NULL
              AND v.lifecycle_status = 'ACTIVE'
            ORDER BY v.id
        """)]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    print(f"Registry rows: {len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
