#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
debt_policy_matrix_preview.py

READ ONLY.

Первый модуль для "Системы управления задолженностью":
- читает активную БД или БД из --db;
- собирает сервисы из service_items/charges;
- строит предварительную матрицу:
    какие долги считаются блокирующими;
    какие услуги/действия могут блокироваться;
- ничего не пишет в БД.

PowerShell:
  python .\OSBB\tools\debt_policy_matrix_preview.py

Для live-services sandbox:
  python .\OSBB\tools\debt_policy_matrix_preview.py --db "G:\Programming\Py\OSBB\Data\db\sandbox\osbb_test_live_services_2026-06-26_20-13-26.db"
"""

from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


def text(v: Any) -> str:
    return "" if v is None else str(v).strip()


def project_root() -> Path:
    here = Path(__file__).resolve()
    if here.parent.name.lower() == "tools":
        return here.parent.parent
    cwd = Path.cwd().resolve()
    if (cwd / "OSBB").exists():
        return cwd / "OSBB"
    return cwd


def db_from_config(root: Path) -> tuple[Path, str]:
    py_root = root.parent
    if str(py_root) not in sys.path:
        sys.path.insert(0, str(py_root))
    import config  # type: ignore
    use_test = bool(getattr(config, "USE_TEST_DB", False))
    paths = getattr(config, "paths")
    return Path(paths.OSBB_TEST_DB_FILE if use_test else paths.OSBB_DB_FILE), ("test" if use_test else "prod")


def connect_ro(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def table_exists(cur: sqlite3.Cursor, table: str) -> bool:
    cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,))
    return cur.fetchone() is not None


def columns(cur: sqlite3.Cursor, table: str) -> set[str]:
    if not table_exists(cur, table):
        return set()
    cur.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in cur.fetchall()}


def collect_services(cur: sqlite3.Cursor) -> list[dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}

    if table_exists(cur, "service_items"):
        cols = columns(cur, "service_items")
        code_col = "service_item_code" if "service_item_code" in cols else ("item_code" if "item_code" in cols else "")
        name_col = "service_item_name" if "service_item_name" in cols else ("item_name" if "item_name" in cols else "")
        service_col = "service_code" if "service_code" in cols else ""
        if code_col:
            cur.execute(f"SELECT * FROM service_items ORDER BY {code_col}")
            for r in cur.fetchall():
                code = text(r[code_col])
                if not code:
                    continue
                result.setdefault(code, {
                    "source": "service_items",
                    "service_code": text(r[service_col]) if service_col else "",
                    "item_code": code,
                    "name": text(r[name_col]) if name_col else code,
                })

    if table_exists(cur, "charges"):
        cols = columns(cur, "charges")
        if "service_code" in cols:
            cur.execute("""
                SELECT service_code, COUNT(*) AS rows_count, COALESCE(SUM(amount), 0) AS amount_sum
                FROM charges
                GROUP BY service_code
                ORDER BY service_code
            """)
            for r in cur.fetchall():
                code = text(r["service_code"])
                if not code:
                    continue
                result.setdefault(code, {
                    "source": "charges",
                    "service_code": code,
                    "item_code": "",
                    "name": code,
                })
                result[code]["charge_rows"] = int(r["rows_count"] or 0)
                result[code]["charge_sum"] = float(r["amount_sum"] or 0)

    return sorted(result.values(), key=lambda x: (text(x.get("service_code")), text(x.get("item_code"))))


def classify_service(row: dict[str, Any]) -> dict[str, Any]:
    hay = " ".join([text(row.get("service_code")), text(row.get("item_code")), text(row.get("name"))]).upper()
    is_parking_debt = "PARKING" in hay or "ПАРК" in hay
    is_barrier = "BARRIER" in hay or "ШЛАГ" in hay or "PHONE_ACCESS" in hay or "ТЕЛЕФ" in hay
    is_remote = "REMOTE" in hay or "ПУЛЬТ" in hay

    return {
        "debt_source_default": "YES" if (is_parking_debt or is_barrier) else "NO",
        "block_remote_order_default": "YES" if (is_remote or is_barrier) else "NO",
        "block_remote_issue_default": "YES" if (is_remote or is_barrier) else "NO",
        "block_phone_access_order_default": "YES" if is_barrier or "PHONE" in hay else "NO",
        "operator_review_default": "YES" if (is_remote or is_barrier) else "NO",
    }


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="", help="Explicit SQLite DB path.")
    args = ap.parse_args()

    root = project_root()
    if args.db:
        db_path = Path(args.db)
        db_mode = "explicit"
    else:
        db_path, db_mode = db_from_config(root)

    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    out_dir = root / "Data" / "exports" / "debt_policy"
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / f"debt_policy_matrix_preview_{stamp}.csv"
    txt_path = out_dir / f"debt_policy_matrix_preview_{stamp}.txt"

    conn = connect_ro(db_path)
    try:
        cur = conn.cursor()
        services = collect_services(cur)

        rows = []
        for s in services:
            flags = classify_service(s)
            rows.append({
                "service_code": text(s.get("service_code")),
                "item_code": text(s.get("item_code")),
                "name": text(s.get("name")),
                "source": text(s.get("source")),
                "charge_rows": s.get("charge_rows", ""),
                "charge_sum": s.get("charge_sum", ""),
                "debt_source_default": flags["debt_source_default"],
                "block_remote_order_default": flags["block_remote_order_default"],
                "block_remote_issue_default": flags["block_remote_issue_default"],
                "block_phone_access_order_default": flags["block_phone_access_order_default"],
                "operator_review_default": flags["operator_review_default"],
                "operator_decision": "",
                "operator_note": "",
            })

        fields = [
            "service_code", "item_code", "name", "source", "charge_rows", "charge_sum",
            "debt_source_default",
            "block_remote_order_default",
            "block_remote_issue_default",
            "block_phone_access_order_default",
            "operator_review_default",
            "operator_decision",
            "operator_note",
        ]
        write_csv(csv_path, rows, fields)

        lines = [
            "OSBB debt policy matrix preview - READ ONLY",
            f"Generated: {datetime.now():%Y-%m-%d %H:%M:%S}",
            f"DB mode: {db_mode}",
            f"DB: {db_path}",
            f"Services found: {len(rows)}",
            "",
            "Columns:",
            " - debt_source_default: этот сервисный долг должен считаться блокирующим",
            " - block_remote_order_default: блокировать заказ нового пульта",
            " - block_remote_issue_default: блокировать выдачу пульта",
            " - block_phone_access_order_default: блокировать подключение телефонного доступа",
            " - operator_decision/operator_note: будущие поля для ручной политики",
            "",
        ]

        for r in rows:
            lines.append(
                f"{r['service_code'] or '-'} | {r['item_code'] or '-'} | {r['name'] or '-'} | "
                f"debt={r['debt_source_default']} | remote_order={r['block_remote_order_default']} | "
                f"remote_issue={r['block_remote_issue_default']} | phone={r['block_phone_access_order_default']}"
            )

        txt_path.write_text("\n".join(lines), encoding="utf-8")

        print("OSBB debt policy matrix preview - READ ONLY")
        print(f"DB mode: {db_mode}")
        print(f"DB: {db_path}")
        print(f"Services found: {len(rows)}")
        print(f"TXT: {txt_path}")
        print(f"CSV: {csv_path}")
        print("READ ONLY COMPLETED")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
