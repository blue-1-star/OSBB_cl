#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
rank_candidate_working_dbs.py

Rank OSBB database candidates and find the strongest working/training DB.

Purpose:
  Compare current osbb_test.db with sandbox DBs and score them by schema/features:
    - service orders
    - simplified service catalog/preorders
    - phone barrier access
    - remote/pult requests
    - cashbox/kassa
    - debt policy/access gate
    - bot admins/roles/access control
    - premises/non-residential/commercial/technical units
    - vehicles/tariffs/parking_time
    - operator audit log
    - profile verification / agreement
    - payments/fulfillment

Safety:
  - READ ONLY
  - never modifies DB files
  - writes report only

Typical use:

  cd G:\Programming\Py\OSBB

  python .\tools\rank_candidate_working_dbs.py ^
    --db-dir "G:\Programming\Py\OSBB\Data\db" ^
    --out "G:\Programming\Py\OSBB\Data\exports\db_rank\working_db_candidates.md"

Optional:
  --include "G:\Programming\Py\OSBB\Data\db\osbb_test.db"
  --include "G:\Programming\Py\OSBB\Data\db\sandbox\*.db"

Output:
  - markdown ranking
  - score table
  - per-feature evidence
  - table counts
  - warnings
"""

from __future__ import annotations

import argparse
import glob
import os
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass
class Check:
    name: str
    weight: int
    evidence: list[str] = field(default_factory=list)
    score: int = 0
    max_score: int = 0


@dataclass
class DbReport:
    path: Path
    ok: bool
    error: str = ""
    size_bytes: int = 0
    mtime: str = ""
    table_count: int = 0
    row_counts: dict[str, int] = field(default_factory=dict)
    checks: list[Check] = field(default_factory=list)
    score: int = 0
    max_score: int = 0
    warnings: list[str] = field(default_factory=list)


FEATURES: list[tuple[str, int, dict[str, list[str]]]] = [
    ("service orders foundation", 14, {
        "tables_any": ["service_orders", "service_order_items", "service_order_steps", "service_fulfillment", "service_order_fulfillment"],
        "tables_all_soft": ["service_orders"],
        "columns_any": ["status", "service_code", "service_item_code", "resident_account_id", "apartment_id"],
    }),
    ("service catalog / simplified services", 12, {
        "tables_any": ["service_catalog", "service_items", "service_preorders", "service_interests", "service_interest"],
        "tables_all_soft": ["service_catalog", "service_items"],
        "columns_any": ["service_code", "item_code", "price", "is_active", "requires_payment"],
    }),
    ("phone barrier access", 14, {
        "tables_any": ["phone_access_requests", "phone_access_request_points", "phone_barrier_access_requests", "phone_barrier_access_points"],
        "tables_all_soft": ["phone_access_requests"],
        "columns_any": ["phone", "phone_number", "access_point_code", "barrier_code", "registration_date", "subscription_status"],
    }),
    ("remote / pult requests", 10, {
        "tables_any": ["remote_requests", "remote_request_items", "remote_orders", "pult_requests", "remote_reprogram_requests", "apartment_link_requests"],
        "tables_all_soft": ["remote_requests"],
        "columns_any": ["remote", "pult", "quantity", "request_status", "status"],
    }),
    ("cashbox / kassa", 10, {
        "tables_any": ["cashbox_payments", "cashier_payments", "cashier_operations", "cashbox_operations", "parking_payments", "payment_codes"],
        "tables_all_soft": ["cashier_payments"],
        "columns_any": ["amount", "payment_date", "period", "cashier", "payment_code"],
    }),
    ("debt policy / access gate", 12, {
        "tables_any": ["debt_policy_rules", "service_access_policy", "service_access_policies", "debt_policy_matrix", "service_policy_rules"],
        "tables_all_soft": ["debt_policy_rules"],
        "columns_any": ["service_code", "decision", "policy", "debt", "action", "rule_code"],
    }),
    ("bot admins / roles / staff access", 12, {
        "tables_any": ["bot_admins", "staff_access", "operator_roles", "access_control_roles", "user_roles", "role_permissions"],
        "tables_all_soft": ["bot_admins"],
        "columns_any": ["telegram_user_id", "role", "is_active", "permissions", "operator"],
    }),
    ("premises / non-residential units", 10, {
        "tables_any": ["units", "unit_registry", "commercial_units", "technical_units", "commercial_contracts", "unit_aliases"],
        "tables_all_soft": ["apartments"],
        "columns_any": ["unit_type", "unit_code", "object_type", "display_name", "record_status", "commercial", "technical"],
    }),
    ("vehicles / tariffs / parking time", 10, {
        "tables_any": ["vehicles", "cars", "parking_tariffs", "vehicle_tariffs", "parking_time"],
        "tables_all_soft": ["vehicles"],
        "columns_any": ["plate", "license_plate", "car_number", "parking_time", "tariff", "brand", "apartment_id"],
    }),
    ("operator audit log", 8, {
        "tables_any": ["operator_audit_log"],
        "tables_all_soft": ["operator_audit_log"],
        "columns_any": ["operator", "event", "action", "entity", "created_at", "audit_id"],
    }),
    ("profile verification / agreement", 8, {
        "tables_any": ["profile_verification", "profile_verifications", "profile_parking_time_tests", "agreement_tasks", "verification_tasks"],
        "tables_all_soft": [],
        "columns_any": ["verification", "confirmed", "parking_time", "profile", "status"],
    }),
    ("payments / fulfillment compatibility", 8, {
        "tables_any": ["payments", "payment_allocations", "service_payments", "fulfillment_events", "payment_links"],
        "tables_all_soft": [],
        "columns_any": ["payment_id", "order_id", "amount", "paid_at", "status"],
    }),
]


IMPORTANT_TABLES = [
    "apartments", "resident_accounts", "vehicles", "bot_admins",
    "operator_audit_log", "service_orders", "service_items",
    "service_catalog", "service_preorders", "service_interests",
    "phone_access_requests", "phone_access_request_points",
    "remote_requests", "cashier_payments", "cashbox_payments",
    "debt_policy_rules", "units", "commercial_contracts",
]


def connect_ro(path: Path) -> sqlite3.Connection:
    uri = f"file:{path.as_posix()}?mode=ro"
    return sqlite3.connect(uri, uri=True)


def tables(conn: sqlite3.Connection) -> set[str]:
    return {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
    }


def columns(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}
    except Exception:
        return set()


def count_rows(conn: sqlite3.Connection, table: str) -> int:
    try:
        return int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
    except Exception:
        return -1


def score_feature(conn: sqlite3.Connection, table_set: set[str], feature: tuple[str, int, dict[str, list[str]]]) -> Check:
    name, weight, rules = feature
    check = Check(name=name, weight=weight, max_score=weight)
    score = 0

    tables_any = rules.get("tables_any", [])
    tables_all_soft = rules.get("tables_all_soft", [])
    columns_any = rules.get("columns_any", [])

    present_any = [t for t in tables_any if t in table_set]
    present_all_soft = [t for t in tables_all_soft if t in table_set]

    if present_any:
        part = int(weight * 0.45)
        score += part
        check.evidence.append("tables: " + ", ".join(present_any))

    if tables_all_soft and len(present_all_soft) == len(tables_all_soft):
        part = int(weight * 0.20)
        score += part
        check.evidence.append("core tables present: " + ", ".join(present_all_soft))
    elif present_all_soft:
        part = int(weight * 0.10)
        score += part
        check.evidence.append("some core tables: " + ", ".join(present_all_soft))

    matched_cols = []
    for t in present_any or list(table_set):
        cols = columns(conn, t)
        for c in columns_any:
            if c in cols:
                matched_cols.append(f"{t}.{c}")
        if len(matched_cols) >= 12:
            break

    if matched_cols:
        part = int(weight * 0.25)
        score += part
        check.evidence.append("columns: " + ", ".join(matched_cols[:12]))

    row_signal = []
    for t in present_any:
        n = count_rows(conn, t)
        if n > 0:
            row_signal.append(f"{t}={n}")
    if row_signal:
        part = weight - score
        score += part
        check.evidence.append("rows: " + ", ".join(row_signal[:8]))

    check.score = min(score, weight)
    if check.score == 0:
        check.evidence.append("not found")
    return check


def inspect_db(path: Path) -> DbReport:
    rep = DbReport(path=path)
    rep.size_bytes = path.stat().st_size
    rep.mtime = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")

    try:
        conn = connect_ro(path)
        try:
            tset = tables(conn)
            rep.table_count = len(tset)

            for t in IMPORTANT_TABLES:
                if t in tset:
                    rep.row_counts[t] = count_rows(conn, t)

            for feature in FEATURES:
                check = score_feature(conn, tset, feature)
                rep.checks.append(check)
                rep.score += check.score
                rep.max_score += check.max_score

            if "audit_log" in tset and "operator_audit_log" not in tset:
                rep.warnings.append("has legacy audit_log but no operator_audit_log")

            if "operator_audit_log" in tset and count_rows(conn, "operator_audit_log") == 0:
                rep.warnings.append("operator_audit_log exists but is empty")

            if "bot_admins" not in tset:
                rep.warnings.append("no bot_admins table")

            if not any(t in tset for t in ["service_orders", "service_preorders", "service_interests"]):
                rep.warnings.append("no visible service-order/preorder tables")

            if not any(t in tset for t in ["phone_access_requests", "phone_barrier_access_requests"]):
                rep.warnings.append("no visible phone-access request tables")

            rep.ok = True
        finally:
            conn.close()
    except Exception as e:
        rep.ok = False
        rep.error = str(e)
    return rep


def discover(db_dir: Path, includes: list[str]) -> list[Path]:
    paths: list[Path] = []
    if includes:
        for pat in includes:
            paths.extend(Path(p) for p in glob.glob(pat))
    else:
        paths.append(db_dir / "osbb_test.db")
        paths.append(db_dir / "osbb.db")
        paths.extend((db_dir / "sandbox").glob("*.db"))
        paths.extend((db_dir / "sandbox" / "backups").glob("*.db"))
        paths.extend((db_dir / "backups").glob("*.db"))

    seen = set()
    out = []
    for p in paths:
        p = p.resolve()
        if p.exists() and p.suffix.lower() == ".db" and p not in seen:
            seen.add(p)
            out.append(p)
    return out


def short(path: Path, base: Path) -> str:
    try:
        return str(path.relative_to(base))
    except Exception:
        return str(path)


def render(reports: list[DbReport], db_dir: Path) -> str:
    reports_sorted = sorted(reports, key=lambda r: (r.ok, r.score, r.size_bytes), reverse=True)

    lines = []
    lines.append("# OSBB Working DB Candidate Ranking")
    lines.append("")
    lines.append(f"Generated: {datetime.now():%Y-%m-%d %H:%M:%S}")
    lines.append(f"DB root: `{db_dir}`")
    lines.append("")
    lines.append("## Ranking")
    lines.append("")
    lines.append("| Rank | Score | Tables | Size MB | Modified | DB |")
    lines.append("|---:|---:|---:|---:|---|---|")
    for i, r in enumerate(reports_sorted, 1):
        size_mb = r.size_bytes / 1024 / 1024
        score = f"{r.score}/{r.max_score}" if r.ok else "ERROR"
        lines.append(f"| {i} | {score} | {r.table_count} | {size_mb:.2f} | {r.mtime} | `{short(r.path, db_dir)}` |")
    lines.append("")

    if reports_sorted:
        best = reports_sorted[0]
        lines.append("## Current best candidate")
        lines.append("")
        lines.append(f"`{best.path}`")
        lines.append("")
        lines.append(f"Score: **{best.score}/{best.max_score}**")
        lines.append("")
        if best.warnings:
            lines.append("Warnings:")
            for w in best.warnings:
                lines.append(f"- {w}")
            lines.append("")

    lines.append("## Feature matrix")
    lines.append("")
    feature_names = [f[0] for f in FEATURES]
    header = "| DB | Total | " + " | ".join(feature_names) + " |"
    sep = "|---|---:|" + "|".join(["---:"] * len(feature_names)) + "|"
    lines.append(header)
    lines.append(sep)
    for r in reports_sorted:
        vals = []
        by_name = {c.name: c for c in r.checks}
        for name in feature_names:
            c = by_name.get(name)
            vals.append(f"{c.score}/{c.max_score}" if c else "-")
        lines.append(f"| `{short(r.path, db_dir)}` | {r.score}/{r.max_score} | " + " | ".join(vals) + " |")
    lines.append("")

    lines.append("## Detailed reports")
    lines.append("")
    for r in reports_sorted:
        lines.append(f"### `{short(r.path, db_dir)}`")
        lines.append("")
        if not r.ok:
            lines.append(f"ERROR: {r.error}")
            lines.append("")
            continue

        lines.append(f"- Score: **{r.score}/{r.max_score}**")
        lines.append(f"- Tables: {r.table_count}")
        lines.append(f"- Size: {r.size_bytes / 1024 / 1024:.2f} MB")
        lines.append(f"- Modified: {r.mtime}")
        if r.row_counts:
            lines.append("- Important row counts:")
            for t, n in sorted(r.row_counts.items()):
                lines.append(f"  - `{t}`: {n}")
        if r.warnings:
            lines.append("- Warnings:")
            for w in r.warnings:
                lines.append(f"  - {w}")
        lines.append("")
        for c in r.checks:
            if c.score == 0:
                continue
            lines.append(f"**{c.name}: {c.score}/{c.max_score}**")
            for e in c.evidence:
                lines.append(f"- {e}")
            lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db-dir", default="Data/db", help="OSBB Data/db directory")
    ap.add_argument("--include", action="append", default=[], help="DB path or glob. Can be repeated.")
    ap.add_argument("--out", default="Data/exports/db_rank/working_db_candidates.md")
    args = ap.parse_args()

    db_dir = Path(args.db_dir).resolve()
    out_path = Path(args.out)
    if not out_path.is_absolute():
        out_path = Path.cwd() / out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)

    candidates = discover(db_dir, args.include)
    reports = [inspect_db(p) for p in candidates]
    report = render(reports, db_dir)
    out_path.write_text(report, encoding="utf-8")

    print("=" * 100)
    print("OSBB working DB candidate ranking")
    print("=" * 100)
    print("Candidates:", len(candidates))
    print("Out:", out_path)
    if reports:
        best = sorted(reports, key=lambda r: (r.ok, r.score, r.size_bytes), reverse=True)[0]
        print("Best:", best.path)
        print("Score:", f"{best.score}/{best.max_score}")
    print("READ ONLY COMPLETED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
