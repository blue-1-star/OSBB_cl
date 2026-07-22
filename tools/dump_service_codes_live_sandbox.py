#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
dump_service_codes_live_sandbox.py

READ ONLY.
Выгружает реальные service/action коды из выбранной SQLite базы в TXT.
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


DEFAULT_DB = r"G:\Programming\Py\OSBB\Data\db\sandbox\osbb_test_live_services_2026-06-26_20-13-26.db"


def table_exists(cur, table: str) -> bool:
    cur.execute("SELECT 1 FROM sqlite_master WHERE type=? AND name=?", ("table", table))
    return cur.fetchone() is not None


def columns(cur, table: str) -> list[str]:
    cur.execute(f"PRAGMA table_info({table})")
    return [row[1] for row in cur.fetchall()]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB)
    args = ap.parse_args()

    db = Path(args.db)
    out_dir = Path(r"G:\Programming\Py\OSBB\Data\exports\debt_policy")
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "service_codes_live_sandbox.txt"

    conn = sqlite3.connect(db)
    try:
        cur = conn.cursor()
        lines = []
        lines.append(f"DB: {db}")
        lines.append("")

        for table in ["charges", "service_items", "service_catalog", "service_tariffs"]:
            lines.append("=" * 100)
            lines.append(f"TABLE: {table}")
            lines.append("=" * 100)

            if not table_exists(cur, table):
                lines.append("MISSING")
                lines.append("")
                continue

            cols = columns(cur, table)
            lines.append("COLUMNS:")
            lines.append(", ".join(cols))
            lines.append("")

            interesting = [
                c for c in cols
                if "code" in c.lower()
                or "name" in c.lower()
                or "title" in c.lower()
                or "profile" in c.lower()
                or c.lower() in {"amount", "price", "service_code", "base_service_code"}
            ]

            if interesting:
                lines.append("DISTINCT VALUES:")
                for col in interesting:
                    try:
                        cur.execute(
                            f'SELECT DISTINCT "{col}" FROM {table} '
                            f'WHERE "{col}" IS NOT NULL AND TRIM(CAST("{col}" AS TEXT)) <> "" '
                            f'ORDER BY "{col}" LIMIT 100'
                        )
                        vals = [str(r[0]) for r in cur.fetchall()]
                        lines.append(f"- {col}:")
                        for v in vals:
                            lines.append(f"    {v}")
                    except Exception as exc:
                        lines.append(f"- {col}: ERROR {type(exc).__name__}: {exc}")
                lines.append("")

            lines.append("SAMPLE ROWS:")
            try:
                cur.execute(f"SELECT * FROM {table} LIMIT 20")
                rows = cur.fetchall()
                for row in rows:
                    lines.append(str(row))
            except Exception as exc:
                lines.append(f"ERROR: {type(exc).__name__}: {exc}")

            lines.append("")

        out.write_text("\n".join(lines), encoding="utf-8")

        print("OSBB service codes dump - READ ONLY")
        print(f"DB: {db}")
        print(f"Report: {out}")
        print("READ ONLY COMPLETED")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
