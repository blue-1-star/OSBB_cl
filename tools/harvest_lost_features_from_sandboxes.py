#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
harvest_lost_features_from_sandboxes.py

Compares the current Golden/Working DB with sandbox DBs and reports "lost" or stronger
feature candidates: modules/data/code-era evidence that exist in sandbox DBs but are
weaker or empty in the current accepted DB.

This tool does NOT merge anything.
It only creates a report for human review.

Default:
  Base DB:
    G:\Programming\Py\OSBB\Data\db\osbb_test.db

  Candidate dirs:
    G:\Programming\Py\OSBB\Data\db
    G:\Programming\Py\OSBB\Data\db\sandbox

  Report:
    G:\Programming\Py\OSBB\Data\exports\db_rank\LOST_FEATURES_FROM_SANDBOXES.md

Examples:

  python .\OSBB\tools\harvest_lost_features_from_sandboxes.py

  python .\OSBB\tools\harvest_lost_features_from_sandboxes.py `
    --base-db "G:\Programming\Py\OSBB\Data\db\working\osbb_working_pult_order_test_2026-07-02_17-18-39.db"

  python .\OSBB\tools\harvest_lost_features_from_sandboxes.py `
    --candidate-dir "G:\Programming\Py\OSBB\Data\db\sandbox" `
    --out "G:\Programming\Py\OSBB\Data\exports\db_rank\LOST_FEATURES_FROM_SANDBOXES.md"
"""

from __future__ import annotations

import argparse
import os
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


DEFAULT_ROOT = Path(r"G:\Programming\Py\OSBB")
DEFAULT_BASE_DB = DEFAULT_ROOT / "Data" / "db" / "osbb_test.db"
DEFAULT_CANDIDATE_DIRS = [
    DEFAULT_ROOT / "Data" / "db",
    DEFAULT_ROOT / "Data" / "db" / "sandbox",
]
DEFAULT_OUT = DEFAULT_ROOT / "Data" / "exports" / "db_rank" / "LOST_FEATURES_FROM_SANDBOXES.md"


FEATURES: dict[str, dict[str, Any]] = {
    "payments_and_cashbox": {
        "title": "Payments / cashbox / payment allocations",
        "tables": [
            "payments",
            "payment_allocations",
            "cashbox_operations",
            "cashbox",
            "payment_codes",
            "payment_imports",
            "payment_reconciliation",
        ],
        "columns": {
            "payments": ["amount", "period", "apartment_id", "created_at"],
            "payment_allocations": ["payment_id", "amount", "period"],
            "cashbox_operations": ["amount", "operation_type", "created_at"],
        },
        "keywords": ["payment", "cashbox", "kassa", "касса", "оплат", "платеж"],
    },
    "debts_and_accruals": {
        "title": "Debts / accruals / empty accrual fields",
        "tables": [
            "charges",
            "accruals",
            "debtors",
            "debts",
            "parking_charges",
            "monthly_charges",
            "charge_periods",
        ],
        "columns": {
            "charges": ["amount", "period", "apartment_id"],
            "accruals": ["amount", "period", "apartment_id"],
            "parking_charges": ["amount", "period", "apartment_id"],
        },
        "keywords": ["debt", "debtor", "charge", "accrual", "начис", "борг", "долг"],
    },
    "pult_remote_orders": {
        "title": "Remote/pult orders",
        "tables": [
            "remote_requests",
            "remote_order_details",
            "remote_orders",
            "pult_requests",
            "pult_orders",
            "service_orders",
            "service_order_steps",
        ],
        "columns": {
            "remote_requests": ["status", "apartment_id", "created_at"],
            "remote_order_details": ["remote_type", "quantity", "price"],
            "service_orders": ["service_type", "status", "apartment_id", "created_at"],
        },
        "keywords": ["remote", "pult", "пульт", "перепрош", "ключ"],
    },
    "phone_access": {
        "title": "Phone barrier access",
        "tables": [
            "phone_access_requests",
            "phone_access_request_points",
            "phone_access_points",
            "phone_access_numbers",
            "barrier_phone_access",
        ],
        "columns": {
            "phone_access_requests": ["phone", "status", "apartment_id", "created_at"],
            "phone_access_points": ["name", "is_active"],
        },
        "keywords": ["phone_access", "barrier", "телефон", "шлагбаум", "ворот"],
    },
    "commercial_units_contracts": {
        "title": "Commercial / non-residential units and contracts",
        "tables": [
            "commercial_contracts",
            "non_residential_units",
            "unit_groups",
            "unit_group_members",
            "unit_aliases",
            "premises",
            "legal_entities",
            "counterparties",
        ],
        "columns": {
            "commercial_contracts": ["contract_number", "unit_id", "starts_at", "status"],
            "unit_groups": ["name", "group_type"],
            "unit_group_members": ["unit_group_id", "apartment_id"],
        },
        "keywords": ["commercial", "contract", "unit_group", "нежил", "коммер", "догов"],
    },
    "resident_identity": {
        "title": "Resident identity / binding / verification",
        "tables": [
            "resident_accounts",
            "resident_binding_requests",
            "resident_profile_schema_migrations",
            "resident_verifications",
            "resident_profiles",
        ],
        "columns": {
            "resident_accounts": ["telegram_user_id", "apartment_id", "status", "updated_at"],
            "resident_binding_requests": ["telegram_user_id", "apartment_id", "status"],
        },
        "keywords": ["resident", "binding", "verified", "мешкан", "привяз"],
    },
    "admin_audit_roles": {
        "title": "Admin roles / audit / operator log",
        "tables": [
            "bot_admins",
            "operator_audit_log",
            "audit_log",
            "admin_actions",
            "admin_sessions",
        ],
        "columns": {
            "bot_admins": ["telegram_user_id", "role", "can_read", "can_write"],
            "operator_audit_log": ["created_at", "action", "details"],
        },
        "keywords": ["admin", "operator", "audit", "админ", "оператор"],
    },
    "guard_workspace": {
        "title": "Guard workspace / parking time / access control",
        "tables": [
            "guard_shifts",
            "guard_events",
            "parking_time_sessions",
            "parking_time_tariffs",
            "access_events",
        ],
        "columns": {
            "parking_time_sessions": ["vehicle_id", "started_at", "ended_at"],
            "guard_events": ["event_type", "created_at"],
        },
        "keywords": ["guard", "parking_time", "охрана", "сторож", "время"],
    },
    "reports_and_exports": {
        "title": "Reports / exports / operational views",
        "tables": [
            "report_runs",
            "export_jobs",
            "saved_reports",
            "report_definitions",
        ],
        "columns": {},
        "keywords": ["report", "export", "xlsx", "excel", "отчет", "експорт"],
    },
}


@dataclass
class FeatureEvidence:
    feature_key: str
    title: str
    score: int = 0
    tables_present: list[str] = field(default_factory=list)
    row_counts: dict[str, int | None] = field(default_factory=dict)
    columns_present: dict[str, list[str]] = field(default_factory=dict)
    keyword_hits: list[str] = field(default_factory=list)


@dataclass
class DbReport:
    path: Path
    ok: bool
    error: str = ""
    size_bytes: int = 0
    mtime: float = 0
    tables_count: int = 0
    integrity: str = ""
    features: dict[str, FeatureEvidence] = field(default_factory=dict)


def connect_ro(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)


def table_names(conn: sqlite3.Connection) -> list[str]:
    return [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()]


def cols(conn: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]


def row_count(conn: sqlite3.Connection, table: str) -> int | None:
    try:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    except Exception:
        return None


def text_hits(conn: sqlite3.Connection, keywords: list[str], limit: int = 12) -> list[str]:
    hits = []
    try:
        rows = conn.execute("SELECT name, sql FROM sqlite_master WHERE sql IS NOT NULL").fetchall()
    except Exception:
        return hits
    for name, sql in rows:
        low = (sql or "").lower()
        for kw in keywords:
            if kw.lower() in low:
                hit = f"{name}: {kw}"
                if hit not in hits:
                    hits.append(hit)
                break
        if len(hits) >= limit:
            break
    return hits


def inspect_feature(conn: sqlite3.Connection, feature_key: str, spec: dict[str, Any], all_tables: set[str]) -> FeatureEvidence:
    ev = FeatureEvidence(feature_key=feature_key, title=spec["title"])

    for t in spec.get("tables", []):
        if t in all_tables:
            ev.tables_present.append(t)
            cnt = row_count(conn, t)
            ev.row_counts[t] = cnt
            ev.score += 5
            if cnt:
                ev.score += min(10, cnt)

    for t, wanted_cols in spec.get("columns", {}).items():
        if t in all_tables:
            existing = cols(conn, t)
            present = [c for c in wanted_cols if c in existing]
            if present:
                ev.columns_present[t] = present
                ev.score += len(present)

    hits = text_hits(conn, spec.get("keywords", []))
    ev.keyword_hits = hits
    ev.score += min(10, len(hits) * 2)

    return ev


def inspect_db(path: Path) -> DbReport:
    rep = DbReport(
        path=path,
        ok=False,
        size_bytes=path.stat().st_size if path.exists() else 0,
        mtime=path.stat().st_mtime if path.exists() else 0,
    )

    try:
        with connect_ro(path) as conn:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            rep.integrity = integrity
            if integrity != "ok":
                rep.error = f"integrity_check={integrity}"
                return rep
            tables = table_names(conn)
            all_tables = set(tables)
            rep.tables_count = len(tables)
            for key, spec in FEATURES.items():
                rep.features[key] = inspect_feature(conn, key, spec, all_tables)
            rep.ok = True
            return rep
    except Exception as e:
        rep.error = str(e)
        return rep


def discover_dbs(candidate_dirs: list[Path], base_db: Path) -> list[Path]:
    found = {}
    for d in candidate_dirs:
        if not d.exists():
            continue
        for p in d.rglob("*.db"):
            try:
                if p.resolve() == base_db.resolve():
                    continue
            except Exception:
                pass
            name = p.name.lower()
            if any(part in name for part in ["journal", "wal", "shm"]):
                continue
            found[str(p.resolve())] = p
    return sorted(found.values(), key=lambda p: p.stat().st_mtime, reverse=True)


def stronger_than(candidate: FeatureEvidence, base: FeatureEvidence) -> bool:
    if candidate.score >= base.score + 10:
        return True
    for table, cnt in candidate.row_counts.items():
        base_cnt = base.row_counts.get(table)
        if cnt and (not base_cnt or cnt > base_cnt):
            return True
    if len(candidate.tables_present) > len(base.tables_present):
        return True
    return False


def fmt_mtime(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def md_feature_ev(ev: FeatureEvidence) -> str:
    lines = []
    lines.append(f"- Score: **{ev.score}**")
    if ev.tables_present:
        lines.append(f"- Tables: `{', '.join(ev.tables_present)}`")
    useful_counts = {k: v for k, v in ev.row_counts.items() if v not in (None, 0)}
    if useful_counts:
        counts = ", ".join(f"{k}={v}" for k, v in useful_counts.items())
        lines.append(f"- Non-empty rows: `{counts}`")
    if ev.columns_present:
        chunks = []
        for t, cs in ev.columns_present.items():
            chunks.append(f"{t}({', '.join(cs)})")
        lines.append(f"- Columns: `{'; '.join(chunks)}`")
    if ev.keyword_hits:
        lines.append(f"- Keyword hits: `{', '.join(ev.keyword_hits[:8])}`")
    return "\n".join(lines)


def build_report(base: DbReport, candidates: list[DbReport], out: Path) -> str:
    lines = []
    lines.append("# OSBB Lost / Stronger Feature Harvest from Sandbox DBs")
    lines.append("")
    lines.append(f"Generated: {datetime.now():%Y-%m-%d %H:%M:%S}")
    lines.append("")
    lines.append("## Base DB")
    lines.append("")
    lines.append(f"`{base.path}`")
    lines.append("")
    lines.append(f"- OK: `{base.ok}`")
    lines.append(f"- Integrity: `{base.integrity}`")
    lines.append(f"- Tables: `{base.tables_count}`")
    lines.append(f"- Size: `{base.size_bytes / 1024 / 1024:.2f} MB`")
    lines.append("")

    lines.append("## Executive summary")
    lines.append("")
    any_findings = False
    summary_rows = []

    for feature_key, spec in FEATURES.items():
        base_ev = base.features.get(feature_key, FeatureEvidence(feature_key, spec["title"]))
        stronger = []
        for cand in candidates:
            if not cand.ok:
                continue
            cand_ev = cand.features.get(feature_key)
            if cand_ev and stronger_than(cand_ev, base_ev):
                stronger.append((cand, cand_ev))
        stronger.sort(key=lambda x: x[1].score, reverse=True)
        if stronger:
            any_findings = True
            top = stronger[0]
            summary_rows.append((spec["title"], base_ev.score, top[1].score, top[0].path.name, len(stronger)))

    if not any_findings:
        lines.append("No obviously stronger sandbox feature candidates found.")
    else:
        lines.append("| Feature | Base score | Best sandbox score | Best DB | Candidates |")
        lines.append("|---|---:|---:|---|---:|")
        for title, bscore, cscore, dbname, count in summary_rows:
            lines.append(f"| {title} | {bscore} | {cscore} | `{dbname}` | {count} |")

    lines.append("")
    lines.append("## Detailed findings")
    lines.append("")

    for feature_key, spec in FEATURES.items():
        base_ev = base.features.get(feature_key, FeatureEvidence(feature_key, spec["title"]))
        stronger = []
        for cand in candidates:
            if not cand.ok:
                continue
            cand_ev = cand.features.get(feature_key)
            if cand_ev and stronger_than(cand_ev, base_ev):
                stronger.append((cand, cand_ev))
        stronger.sort(key=lambda x: x[1].score, reverse=True)

        lines.append(f"### {spec['title']}")
        lines.append("")
        lines.append("Base evidence:")
        lines.append("")
        lines.append(md_feature_ev(base_ev) or "- No evidence")
        lines.append("")

        if not stronger:
            lines.append("No stronger sandbox candidate detected.")
            lines.append("")
            continue

        lines.append("Stronger sandbox candidates:")
        lines.append("")
        for cand, ev in stronger[:10]:
            lines.append(f"#### `{cand.path.name}`")
            lines.append("")
            lines.append(f"- Path: `{cand.path}`")
            lines.append(f"- Modified: `{fmt_mtime(cand.mtime)}`")
            lines.append(f"- Tables total: `{cand.tables_count}`")
            lines.append(f"- Size: `{cand.size_bytes / 1024 / 1024:.2f} MB`")
            lines.append(md_feature_ev(ev))
            lines.append("")

    failed = [c for c in candidates if not c.ok]
    if failed:
        lines.append("## Skipped / failed DBs")
        lines.append("")
        for c in failed[:50]:
            lines.append(f"- `{c.path}`: {c.error}")
        lines.append("")

    lines.append("## Recommended next steps")
    lines.append("")
    lines.append("1. Do not merge databases automatically.")
    lines.append("2. Pick one feature area at a time.")
    lines.append("3. Open the strongest sandbox candidate for that feature.")
    lines.append("4. Compare schema, rows, and bot code handlers.")
    lines.append("5. Decide whether to port schema, seed data, UI logic, or only documentation.")
    lines.append("6. Apply changes only through explicit migrations or patches.")
    lines.append("")

    out.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(lines)
    out.write_text(text, encoding="utf-8")
    return text


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-db", default=str(DEFAULT_BASE_DB))
    ap.add_argument("--candidate-dir", action="append", default=None)
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--max-candidates", type=int, default=200)
    args = ap.parse_args()

    base_db = Path(args.base_db)
    candidate_dirs = [Path(p) for p in args.candidate_dir] if args.candidate_dir else DEFAULT_CANDIDATE_DIRS
    out = Path(args.out)

    print("=" * 100)
    print("OSBB harvest lost/stronger features from sandboxes")
    print("=" * 100)
    print("Base DB:", base_db)
    print("Candidate dirs:")
    for d in candidate_dirs:
        print(" -", d)
    print("Report:", out)

    if not base_db.exists():
        raise SystemExit(f"Base DB not found: {base_db}")

    base = inspect_db(base_db)
    if not base.ok:
        raise SystemExit(f"Base DB is not ok: {base.error}")

    dbs = discover_dbs(candidate_dirs, base_db)[: args.max_candidates]
    print("")
    print("Candidates discovered:", len(dbs))

    reports = []
    for i, p in enumerate(dbs, 1):
        rep = inspect_db(p)
        reports.append(rep)
        print(f"[{i}/{len(dbs)}]", "OK" if rep.ok else "SKIP", p.name)

    build_report(base, reports, out)

    print("")
    print("DONE")
    print("Report:", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
