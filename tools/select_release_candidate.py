#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
select_release_candidate.py

OSBB release-candidate selector.

This tool is deeper than a simple table counter.
It tries to identify which DB is the strongest "working/training DB" candidate
by checking project "epochs" / functional blocks:

  - service orders foundation
  - simplified service catalog / preorders
  - phone barrier access
  - remote / pult requests
  - profile verification
  - parking-time profile test
  - debt policy / access gate
  - bot admins / roles / staff access
  - guard / access control
  - cashbox / kassa
  - non-residential / commercial / technical units
  - vehicles / tariffs
  - operator audit log
  - payment schema / fulfillment

Safety:
  READ ONLY. Does not modify DBs.

Typical use:

  cd G:\Programming\Py\OSBB

  python .\tools\select_release_candidate.py ^
    --db-dir "G:\Programming\Py\OSBB\Data\db" ^
    --out "G:\Programming\Py\OSBB\Data\exports\db_rank\release_candidate_selection.md"

Optional:
  --include "G:\Programming\Py\OSBB\Data\db\osbb_test.db"
  --include "G:\Programming\Py\OSBB\Data\db\sandbox\*.db"
"""

from __future__ import annotations

import argparse
import glob
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass
class Epoch:
    key: str
    title: str
    weight: int
    tables_any: list[str] = field(default_factory=list)
    tables_all: list[str] = field(default_factory=list)
    columns_any: list[str] = field(default_factory=list)
    notes: str = ""


@dataclass
class EpochResult:
    epoch: Epoch
    score: int = 0
    evidence: list[str] = field(default_factory=list)


@dataclass
class Candidate:
    path: Path
    ok: bool = False
    error: str = ""
    size_bytes: int = 0
    mtime: str = ""
    table_count: int = 0
    rows: dict[str, int] = field(default_factory=dict)
    epoch_results: list[EpochResult] = field(default_factory=list)
    score: int = 0
    max_score: int = 0
    warnings: list[str] = field(default_factory=list)


EPOCHS = [
    Epoch(
        key="service_orders",
        title="Service Orders foundation",
        weight=14,
        tables_any=["service_orders", "service_order_items", "service_order_steps", "service_fulfillment", "service_order_fulfillment"],
        tables_all=["service_orders"],
        columns_any=["service_code", "service_item_code", "status", "resident_account_id", "apartment_id", "created_at"],
        notes="Foundation for pult/phone/other service requests.",
    ),
    Epoch(
        key="simplified_services",
        title="Simplified service catalog / preorders",
        weight=12,
        tables_any=["service_catalog", "service_items", "service_preorders", "service_interests", "service_interest"],
        tables_all=["service_catalog", "service_items"],
        columns_any=["service_code", "item_code", "price", "is_active", "requires_payment"],
    ),
    Epoch(
        key="phone_access",
        title="Phone barrier access",
        weight=14,
        tables_any=["phone_access_requests", "phone_access_request_points", "phone_barrier_access_requests", "phone_barrier_access_points"],
        tables_all=["phone_access_requests"],
        columns_any=["phone", "phone_number", "access_point_code", "barrier_code", "registration_date", "subscription_status"],
    ),
    Epoch(
        key="remote_pults",
        title="Remote / pult requests",
        weight=11,
        tables_any=["remote_requests", "remote_request_items", "remote_orders", "pult_requests", "remote_reprogram_requests"],
        columns_any=["quantity", "request_status", "status", "remote", "pult", "apartment_id"],
    ),
    Epoch(
        key="profile_verification",
        title="Profile verification",
        weight=9,
        tables_any=["profile_verification", "profile_verifications", "profile_confirmation", "profile_verification_events"],
        columns_any=["status", "confirmed", "verification", "profile", "resident_account_id"],
    ),
    Epoch(
        key="parking_time_profile_test",
        title="Parking-time profile test",
        weight=8,
        tables_any=["profile_parking_time_tests", "parking_time_tests", "profile_parking_time_test"],
        columns_any=["parking_time", "tariff", "vehicle_id", "status", "profile"],
    ),
    Epoch(
        key="debt_policy",
        title="Debt policy / access gate",
        weight=13,
        tables_any=["debt_policy_rules", "service_access_policy", "service_access_policies", "debt_policy_matrix", "service_policy_rules"],
        columns_any=["service_code", "decision", "policy", "debt", "action", "rule_code"],
    ),
    Epoch(
        key="bot_admins_roles",
        title="Bot admins / roles / staff access",
        weight=13,
        tables_any=["bot_admins", "staff_access", "operator_roles", "access_control_roles", "user_roles", "role_permissions"],
        tables_all=["bot_admins"],
        columns_any=["telegram_user_id", "role", "is_active", "permissions", "operator"],
    ),
    Epoch(
        key="guard_access_control",
        title="Guard workspace / access control",
        weight=10,
        tables_any=["access_control_staff", "guard_sessions", "guard_operations", "access_control_roles", "staff_access"],
        columns_any=["guard", "staff", "role", "permission", "is_active"],
    ),
    Epoch(
        key="cashbox",
        title="Cashbox / kassa",
        weight=11,
        tables_any=["cashbox_payments", "cashier_payments", "cashier_operations", "cashbox_operations", "parking_payments", "payment_codes"],
        columns_any=["amount", "payment_date", "period", "cashier", "payment_code"],
    ),
    Epoch(
        key="non_residential",
        title="Non-residential / commercial / technical units",
        weight=12,
        tables_any=["units", "unit_registry", "commercial_units", "technical_units", "commercial_contracts", "unit_aliases"],
        columns_any=["unit_type", "unit_code", "object_type", "display_name", "record_status", "commercial", "technical"],
    ),
    Epoch(
        key="vehicles_tariffs",
        title="Vehicles / tariffs / parking_time",
        weight=10,
        tables_any=["vehicles", "cars", "parking_tariffs", "vehicle_tariffs"],
        tables_all=["vehicles"],
        columns_any=["plate", "license_plate", "car_number", "parking_time", "tariff", "brand", "apartment_id"],
    ),
    Epoch(
        key="operator_audit",
        title="Operator audit log",
        weight=9,
        tables_any=["operator_audit_log"],
        tables_all=["operator_audit_log"],
        columns_any=["operator", "event", "action", "entity", "created_at", "audit_id"],
    ),
    Epoch(
        key="payment_fulfillment",
        title="Payment schema / fulfillment compatibility",
        weight=9,
        tables_any=["payments", "payment_allocations", "service_payments", "fulfillment_events", "payment_links"],
        columns_any=["payment_id", "order_id", "amount", "paid_at", "status"],
    ),
]


IMPORTANT_TABLES = [
    "apartments", "resident_accounts", "vehicles", "operator_audit_log", "bot_admins",
    "service_orders", "service_catalog", "service_items", "service_preorders", "service_interests",
    "phone_access_requests", "phone_access_request_points", "remote_requests",
    "cashier_payments", "cashbox_payments", "debt_policy_rules", "units", "commercial_contracts",
]


def connect_ro(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)


def get_tables(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}


def get_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')}
    except Exception:
        return set()


def row_count(conn: sqlite3.Connection, table: str) -> int:
    try:
        return int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
    except Exception:
        return -1


def score_epoch(conn: sqlite3.Connection, tables: set[str], epoch: Epoch) -> EpochResult:
    res = EpochResult(epoch=epoch)

    present_any = [t for t in epoch.tables_any if t in tables]
    present_all = [t for t in epoch.tables_all if t in tables]

    if present_any:
        points = int(epoch.weight * 0.40)
        res.score += points
        res.evidence.append("tables: " + ", ".join(present_any))

    if epoch.tables_all and len(present_all) == len(epoch.tables_all):
        points = int(epoch.weight * 0.20)
        res.score += points
        res.evidence.append("required tables: " + ", ".join(present_all))

    matched_cols = []
    scan_tables = present_any if present_any else list(tables)
    for t in scan_tables:
        cols = get_columns(conn, t)
        for c in epoch.columns_any:
            if c in cols:
                matched_cols.append(f"{t}.{c}")
        if len(matched_cols) >= 10:
            break

    if matched_cols:
        points = int(epoch.weight * 0.25)
        res.score += points
        res.evidence.append("columns: " + ", ".join(matched_cols[:10]))

    row_signal = []
    for t in present_any:
        n = row_count(conn, t)
        if n > 0:
            row_signal.append(f"{t}={n}")

    if row_signal:
        res.score = epoch.weight
        res.evidence.append("rows: " + ", ".join(row_signal[:8]))

    if res.score == 0:
        res.evidence.append("not found")

    res.score = min(res.score, epoch.weight)
    return res


def inspect_candidate(path: Path) -> Candidate:
    c = Candidate(path=path)
    c.size_bytes = path.stat().st_size
    c.mtime = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")

    try:
        conn = connect_ro(path)
        try:
            tables = get_tables(conn)
            c.table_count = len(tables)

            for t in IMPORTANT_TABLES:
                if t in tables:
                    c.rows[t] = row_count(conn, t)

            for epoch in EPOCHS:
                er = score_epoch(conn, tables, epoch)
                c.epoch_results.append(er)
                c.score += er.score
                c.max_score += epoch.weight

            if "operator_audit_log" not in tables:
                c.warnings.append("missing operator_audit_log")
            elif row_count(conn, "operator_audit_log") == 0:
                c.warnings.append("operator_audit_log exists but empty")

            if "audit_log" in tables and "operator_audit_log" not in tables:
                c.warnings.append("legacy audit_log exists without operator_audit_log")

            if "bot_admins" not in tables:
                c.warnings.append("missing bot_admins")

            if not any(t in tables for t in ["service_orders", "service_preorders", "service_interests"]):
                c.warnings.append("no service-order/preorder foundation detected")

            if not any(t in tables for t in ["phone_access_requests", "phone_barrier_access_requests"]):
                c.warnings.append("no phone-access request tables detected")

            c.ok = True
        finally:
            conn.close()
    except Exception as e:
        c.error = str(e)
        c.ok = False

    return c


def discover(db_dir: Path, includes: list[str]) -> list[Path]:
    paths = []
    if includes:
        for p in includes:
            paths.extend(Path(x) for x in glob.glob(p))
    else:
        paths.append(db_dir / "osbb_test.db")
        paths.extend((db_dir / "sandbox").glob("*.db"))
        paths.extend((db_dir / "sandbox" / "backups").glob("*.db"))
        paths.extend((db_dir / "backups").glob("*.db"))

    out = []
    seen = set()
    for p in paths:
        p = p.resolve()
        if p.exists() and p.suffix.lower() == ".db" and p not in seen:
            seen.add(p)
            out.append(p)
    return out


def rel(path: Path, base: Path) -> str:
    try:
        return str(path.relative_to(base))
    except Exception:
        return str(path)


def verdict(cands: list[Candidate]) -> str:
    good = [c for c in cands if c.ok]
    if not good:
        return "No readable DB candidates."

    best = sorted(good, key=lambda x: (x.score, x.table_count, x.size_bytes), reverse=True)[0]
    pct = 100.0 * best.score / best.max_score if best.max_score else 0
    if pct >= 75:
        return "Recommended candidate is strong enough for smoke-test promotion."
    if pct >= 55:
        return "Recommended candidate is promising but needs manual review before promotion."
    return "No strong candidate found; manual migration/review needed."


def render(cands: list[Candidate], db_dir: Path) -> str:
    ordered = sorted(cands, key=lambda x: (x.ok, x.score, x.table_count, x.size_bytes), reverse=True)

    lines = []
    lines.append("# OSBB Release Candidate Selection")
    lines.append("")
    lines.append(f"Generated: {datetime.now():%Y-%m-%d %H:%M:%S}")
    lines.append(f"DB root: `{db_dir}`")
    lines.append("")
    lines.append("## Verdict")
    lines.append("")
    lines.append(verdict(ordered))
    lines.append("")

    if ordered:
        best = ordered[0]
        lines.append("## Recommended candidate")
        lines.append("")
        lines.append(f"`{best.path}`")
        lines.append("")
        lines.append(f"Score: **{best.score}/{best.max_score}**")
        lines.append(f"Tables: **{best.table_count}**")
        lines.append(f"Size: **{best.size_bytes / 1024 / 1024:.2f} MB**")
        lines.append(f"Modified: **{best.mtime}**")
        lines.append("")

    lines.append("## Ranking")
    lines.append("")
    lines.append("| Rank | Score | Tables | Size MB | Modified | DB |")
    lines.append("|---:|---:|---:|---:|---|---|")
    for i, c in enumerate(ordered, 1):
        score = f"{c.score}/{c.max_score}" if c.ok else "ERROR"
        lines.append(f"| {i} | {score} | {c.table_count} | {c.size_bytes/1024/1024:.2f} | {c.mtime} | `{rel(c.path, db_dir)}` |")
    lines.append("")

    lines.append("## Epoch matrix")
    lines.append("")
    header = "| DB | Total | " + " | ".join(e.title for e in EPOCHS) + " |"
    sep = "|---|---:|" + "|".join(["---:"] * len(EPOCHS)) + "|"
    lines.append(header)
    lines.append(sep)
    for c in ordered:
        by_key = {er.epoch.key: er for er in c.epoch_results}
        vals = []
        for e in EPOCHS:
            er = by_key.get(e.key)
            vals.append(f"{er.score}/{e.weight}" if er else "-")
        lines.append(f"| `{rel(c.path, db_dir)}` | {c.score}/{c.max_score} | " + " | ".join(vals) + " |")
    lines.append("")

    lines.append("## Details")
    lines.append("")
    for c in ordered:
        lines.append(f"### `{rel(c.path, db_dir)}`")
        lines.append("")
        if not c.ok:
            lines.append(f"ERROR: {c.error}")
            lines.append("")
            continue

        lines.append(f"- Score: **{c.score}/{c.max_score}**")
        lines.append(f"- Tables: {c.table_count}")
        lines.append(f"- Size: {c.size_bytes/1024/1024:.2f} MB")
        lines.append(f"- Modified: {c.mtime}")

        if c.rows:
            lines.append("- Important row counts:")
            for t, n in sorted(c.rows.items()):
                lines.append(f"  - `{t}`: {n}")

        if c.warnings:
            lines.append("- Warnings:")
            for w in c.warnings:
                lines.append(f"  - {w}")

        lines.append("")
        for er in c.epoch_results:
            if er.score == 0:
                continue
            lines.append(f"**{er.epoch.title}: {er.score}/{er.epoch.weight}**")
            for ev in er.evidence:
                lines.append(f"- {ev}")
            if er.epoch.notes:
                lines.append(f"- note: {er.epoch.notes}")
            lines.append("")

    lines.append("## Suggested next steps")
    lines.append("")
    lines.append("1. Review the recommended DB manually.")
    lines.append("2. Run schema snapshot/passport on the recommended DB.")
    lines.append("3. Run bot smoke tests: resident mode, admin mode, agreement, vehicles, service orders, phone access.")
    lines.append("4. If smoke tests pass, backup current osbb_test.db and promote the recommended DB as the new working training DB.")
    lines.append("")

    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db-dir", default="Data/db")
    ap.add_argument("--include", action="append", default=[])
    ap.add_argument("--out", default="Data/exports/db_rank/release_candidate_selection.md")
    args = ap.parse_args()

    db_dir = Path(args.db_dir).resolve()
    out = Path(args.out)
    if not out.is_absolute():
        out = Path.cwd() / out
    out.parent.mkdir(parents=True, exist_ok=True)

    candidates = discover(db_dir, args.include)
    reports = [inspect_candidate(p) for p in candidates]
    text = render(reports, db_dir)
    out.write_text(text, encoding="utf-8")

    print("=" * 100)
    print("OSBB release candidate selection")
    print("=" * 100)
    print("Candidates:", len(candidates))
    print("Out:", out)
    readable = [r for r in reports if r.ok]
    if readable:
        best = sorted(readable, key=lambda x: (x.score, x.table_count, x.size_bytes), reverse=True)[0]
        print("Recommended:", best.path)
        print("Score:", f"{best.score}/{best.max_score}")
    print("READ ONLY COMPLETED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
