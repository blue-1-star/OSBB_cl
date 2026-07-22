#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
detect_project_epoch.py

Deep OSBB DB archaeology tool.

Goal:
  Not just "count tables", but reconstruct the development epoch of each DB:
  - what migrations/scripts likely touched it;
  - what feature eras are present;
  - what timestamps and audit trails say;
  - what schema_info / migration / audit / logs-like tables contain;
  - which DB looks like the latest evolutionary branch.

Safety:
  READ ONLY. Does not modify DB files.

Typical use:

  cd G:\Programming\Py\OSBB

  python .\tools\detect_project_epoch.py ^
    --db-dir "G:\Programming\Py\OSBB\Data\db" ^
    --repo "G:\Programming\Py" ^
    --out "G:\Programming\Py\OSBB\Data\exports\db_rank\project_epoch_archaeology.md"

Optional:
  --include "G:\Programming\Py\OSBB\Data\db\sandbox\*.db"
  --top 8
"""

from __future__ import annotations

import argparse
import glob
import json
import re
import sqlite3
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass
class Evidence:
    kind: str
    value: str
    weight: int = 1


@dataclass
class EpochDefinition:
    key: str
    title: str
    weight: int
    table_patterns: list[str] = field(default_factory=list)
    column_patterns: list[str] = field(default_factory=list)
    text_patterns: list[str] = field(default_factory=list)
    file_patterns: list[str] = field(default_factory=list)


@dataclass
class EpochHit:
    definition: EpochDefinition
    score: int = 0
    evidence: list[Evidence] = field(default_factory=list)


@dataclass
class Candidate:
    path: Path
    ok: bool = False
    error: str = ""
    size_bytes: int = 0
    mtime: str = ""
    tables: list[str] = field(default_factory=list)
    table_count: int = 0
    row_counts: dict[str, int] = field(default_factory=dict)
    schema_text_hits: list[str] = field(default_factory=list)
    metadata_hits: list[str] = field(default_factory=list)
    audit_hits: list[str] = field(default_factory=list)
    timestamp_hits: list[str] = field(default_factory=list)
    epoch_hits: list[EpochHit] = field(default_factory=list)
    score: int = 0
    max_score: int = 0
    warnings: list[str] = field(default_factory=list)


EPOCHS = [
    EpochDefinition(
        key="cashier_v2",
        title="Cashier / Kassa V2",
        weight=12,
        table_patterns=[r"cash", r"cashier", r"payment", r"allocation", r"reconciliation"],
        column_patterns=[r"cashier", r"payment_code", r"amount", r"period"],
        text_patterns=[r"cashier", r"kassa", r"касса"],
        file_patterns=[r"cashier_v2", r"cashbox", r"kassa"],
    ),
    EpochDefinition(
        key="service_orders",
        title="Service Orders Foundation",
        weight=14,
        table_patterns=[r"service_orders", r"service_order_steps", r"service_fulfillment", r"service_order_interests"],
        column_patterns=[r"service_code", r"service_item_code", r"fulfillment", r"workflow"],
        text_patterns=[r"service_orders", r"service order", r"fulfillment"],
        file_patterns=[r"service_orders", r"fulfillment"],
    ),
    EpochDefinition(
        key="service_catalog_v2",
        title="Service Catalog V2 / simplified services",
        weight=13,
        table_patterns=[r"service_catalog", r"service_items", r"service_preorders", r"service_interests", r"service_item_workflows"],
        column_patterns=[r"service_code", r"item_code", r"requires_payment", r"price", r"workflow"],
        text_patterns=[r"service_catalog", r"simplified_services", r"preorders"],
        file_patterns=[r"service_catalog", r"simplified_services", r"preorders"],
    ),
    EpochDefinition(
        key="phone_access",
        title="Phone barrier access",
        weight=14,
        table_patterns=[r"phone_access", r"phone_barrier", r"barrier_phone", r"access_point"],
        column_patterns=[r"phone_number", r"access_point_code", r"barrier_code", r"subscription_status"],
        text_patterns=[r"phone", r"barrier", r"телефон"],
        file_patterns=[r"phone_barrier_access", r"phone_access"],
    ),
    EpochDefinition(
        key="remote_pults",
        title="Remote / pult requests",
        weight=12,
        table_patterns=[r"remote_requests", r"pult", r"remote_order", r"remote_reprogram"],
        column_patterns=[r"remote", r"pult", r"quantity", r"request_status"],
        text_patterns=[r"remote", r"pult", r"пульт", r"reprogram", r"перепрош"],
        file_patterns=[r"remote", r"pult"],
    ),
    EpochDefinition(
        key="profile_verification",
        title="Profile verification / agreement",
        weight=12,
        table_patterns=[r"profile_verification", r"resident_profile_verifications", r"verification", r"agreement"],
        column_patterns=[r"verification", r"confirmed", r"profile", r"resident_account_id"],
        text_patterns=[r"profile_verification", r"verification", r"соглас", r"agreement"],
        file_patterns=[r"profile_verification", r"agreement"],
    ),
    EpochDefinition(
        key="parking_time_review",
        title="Parking time review / profile test",
        weight=11,
        table_patterns=[r"parking_time", r"profile_parking_time", r"parking_time_review"],
        column_patterns=[r"parking_time", r"vehicle_id", r"tariff"],
        text_patterns=[r"parking_time", r"parking time"],
        file_patterns=[r"parking_time"],
    ),
    EpochDefinition(
        key="debt_gate",
        title="Debt policy / service access gate",
        weight=13,
        table_patterns=[r"debt", r"policy", r"service_access", r"access_policy"],
        column_patterns=[r"debt", r"decision", r"policy", r"rule_code", r"service_code"],
        text_patterns=[r"debt", r"борг", r"задолж", r"policy"],
        file_patterns=[r"debt_policy", r"remote_debt_gate", r"access_policy"],
    ),
    EpochDefinition(
        key="bot_admins",
        title="Bot admins / roles / operators",
        weight=12,
        table_patterns=[r"bot_admins", r"operator", r"role_permissions", r"user_roles", r"staff_access"],
        column_patterns=[r"telegram_user_id", r"role", r"permission", r"is_active", r"operator"],
        text_patterns=[r"bot_admin", r"operator", r"chairman", r"role"],
        file_patterns=[r"manage_staff_access", r"bot_admin", r"operator"],
    ),
    EpochDefinition(
        key="access_control_guard",
        title="Guard workspace / access control",
        weight=11,
        table_patterns=[r"access_control", r"access_roles", r"access_user_permissions", r"guard", r"access_points"],
        column_patterns=[r"access", r"permission", r"guard", r"role"],
        text_patterns=[r"guard", r"access_control", r"охрана"],
        file_patterns=[r"guard_workspace", r"access_control"],
    ),
    EpochDefinition(
        key="non_residential",
        title="Non-residential / commercial / technical units",
        weight=12,
        table_patterns=[r"unit", r"commercial", r"technical", r"premises", r"contract"],
        column_patterns=[r"unit_type", r"unit_code", r"commercial", r"technical", r"display_name", r"object_type"],
        text_patterns=[r"commercial", r"technical", r"non_residential", r"нежил"],
        file_patterns=[r"commercial", r"unit_registry", r"non_residential"],
    ),
    EpochDefinition(
        key="audit_history",
        title="Operator audit / history",
        weight=9,
        table_patterns=[r"operator_audit_log", r"audit"],
        column_patterns=[r"created_at", r"action", r"event", r"entity", r"operator"],
        text_patterns=[r"operator_audit", r"audit"],
        file_patterns=[r"audit", r"diagnose_osbb_audit"],
    ),
    EpochDefinition(
        key="project_passports",
        title="DB/code passports and runtime schema audit",
        weight=10,
        table_patterns=[r"schema_info", r"migration", r"passport"],
        column_patterns=[r"schema", r"migration", r"version"],
        text_patterns=[r"project_passport", r"db_passport", r"runtime_schema"],
        file_patterns=[r"project_passport", r"db_table_passport", r"schema_snapshot", r"schema_compare"],
    ),
]


TEXT_TABLE_CANDIDATES = [
    "schema_info", "migration_history", "migrations", "operator_audit_log",
    "access_audit_log", "audit_log", "service_catalog", "service_items",
    "bot_admins", "payment_notices", "service_orders", "remote_requests",
]


TIMESTAMP_COLUMNS = ["created_at", "updated_at", "modified_at", "applied_at", "event_at", "timestamp"]


def run_git_files(repo: Path) -> list[str]:
    if not repo or not (repo / ".git").exists():
        return []
    try:
        p = subprocess.run(
            ["git", "ls-files", "OSBB"],
            cwd=str(repo),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            encoding="utf-8",
            errors="replace",
        )
        if p.returncode != 0:
            return []
        return [x.strip() for x in p.stdout.splitlines() if x.strip()]
    except Exception:
        return []


def connect_ro(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)


def tables(conn: sqlite3.Connection) -> list[str]:
    return sorted(r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"))


def columns(conn: sqlite3.Connection, table: str) -> list[tuple[str, str]]:
    try:
        return [(r[1], r[2] or "") for r in conn.execute(f'PRAGMA table_info("{table}")')]
    except Exception:
        return []


def row_count(conn: sqlite3.Connection, table: str) -> int:
    try:
        return int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
    except Exception:
        return -1


def sample_text(conn: sqlite3.Connection, table: str, limit: int = 20) -> list[str]:
    cols = columns(conn, table)
    text_cols = [name for name, typ in cols if any(x in (typ or "").upper() for x in ["TEXT", "CHAR", "VARCHAR"]) or name.lower() in ["event", "action", "notes", "description", "message", "service_code", "status", "role"]]
    if not text_cols:
        return []
    selected = text_cols[:6]
    try:
        sql = "SELECT " + ", ".join(f'"{c}"' for c in selected) + f' FROM "{table}" LIMIT {limit}'
        rows = conn.execute(sql).fetchall()
        out = []
        for row in rows:
            out.append(" | ".join("" if v is None else str(v) for v in row))
        return out
    except Exception:
        return []


def latest_timestamps(conn: sqlite3.Connection, table: str) -> list[str]:
    cols = [c for c, _ in columns(conn, table)]
    out = []
    for c in TIMESTAMP_COLUMNS:
        if c in cols:
            try:
                row = conn.execute(f'SELECT MAX("{c}") FROM "{table}"').fetchone()
                if row and row[0]:
                    out.append(f"{table}.{c} max={row[0]}")
            except Exception:
                pass
    return out


def regex_any(patterns: list[str], text: str) -> list[str]:
    low = text.lower()
    hits = []
    for p in patterns:
        try:
            if re.search(p, low, re.I):
                hits.append(p)
        except re.error:
            if p.lower() in low:
                hits.append(p)
    return hits


def inspect_epoch(conn: sqlite3.Connection, table_list: list[str], all_text: str, file_text: str, epoch: EpochDefinition) -> EpochHit:
    hit = EpochHit(definition=epoch)
    table_set = set(table_list)

    table_hits = []
    for t in table_list:
        if regex_any(epoch.table_patterns, t):
            table_hits.append(t)
    if table_hits:
        pts = min(int(epoch.weight * 0.35) + min(len(table_hits), 5), int(epoch.weight * 0.50))
        hit.score += pts
        hit.evidence.append(Evidence("tables", ", ".join(table_hits[:12]), pts))

    col_hits = []
    for t in table_hits or table_list:
        for c, _typ in columns(conn, t):
            if regex_any(epoch.column_patterns, c):
                col_hits.append(f"{t}.{c}")
        if len(col_hits) >= 16:
            break
    if col_hits:
        pts = int(epoch.weight * 0.20)
        hit.score += pts
        hit.evidence.append(Evidence("columns", ", ".join(col_hits[:16]), pts))

    text_hits = regex_any(epoch.text_patterns, all_text)
    if text_hits:
        pts = int(epoch.weight * 0.20)
        hit.score += pts
        hit.evidence.append(Evidence("db text", ", ".join(text_hits[:8]), pts))

    file_hits = regex_any(epoch.file_patterns, file_text)
    if file_hits:
        pts = int(epoch.weight * 0.15)
        hit.score += pts
        hit.evidence.append(Evidence("git files", ", ".join(file_hits[:8]), pts))

    row_hits = []
    for t in table_hits:
        n = row_count(conn, t)
        if n > 0:
            row_hits.append(f"{t}={n}")
    if row_hits:
        add = epoch.weight - hit.score
        hit.score = epoch.weight
        hit.evidence.append(Evidence("rows", ", ".join(row_hits[:10]), add))

    hit.score = min(hit.score, epoch.weight)
    if hit.score == 0:
        hit.evidence.append(Evidence("none", "not detected", 0))
    return hit


def collect_text(conn: sqlite3.Connection, table_list: list[str]) -> tuple[str, list[str], list[str], list[str]]:
    chunks = []
    metadata_hits = []
    audit_hits = []
    timestamp_hits = []

    for t in table_list:
        for ts in latest_timestamps(conn, t):
            timestamp_hits.append(ts)

    for t in TEXT_TABLE_CANDIDATES:
        if t not in table_list:
            continue
        n = row_count(conn, t)
        metadata_hits.append(f"{t}: rows={n}")
        samples = sample_text(conn, t, limit=30)
        for s in samples:
            if s.strip():
                chunks.append(f"{t}: {s}")
                if "audit" in t or "operator" in t or "access_audit" in t:
                    audit_hits.append(f"{t}: {s[:200]}")

    return "\n".join(chunks), metadata_hits, audit_hits, timestamp_hits


def inspect_db(path: Path, git_file_text: str) -> Candidate:
    c = Candidate(path=path)
    c.size_bytes = path.stat().st_size
    c.mtime = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")

    try:
        conn = connect_ro(path)
        try:
            c.tables = tables(conn)
            c.table_count = len(c.tables)
            for t in c.tables:
                if t in [
                    "apartments", "resident_accounts", "vehicles", "operator_audit_log",
                    "bot_admins", "service_orders", "service_order_steps", "service_catalog",
                    "service_items", "phone_access_requests", "phone_access_request_points",
                    "remote_requests", "cashbox_operations", "payments", "payment_allocations",
                    "commercial_contracts", "unit_aliases", "debt_policy_rules",
                ]:
                    c.row_counts[t] = row_count(conn, t)

            db_text, meta, audit, ts = collect_text(conn, c.tables)
            c.metadata_hits = meta[:80]
            c.audit_hits = audit[:80]
            c.timestamp_hits = sorted(ts, reverse=True)[:40]

            schema_like = [t for t in c.tables if any(x in t.lower() for x in ["schema", "migration", "version", "passport"])]
            c.schema_text_hits = schema_like

            for e in EPOCHS:
                eh = inspect_epoch(conn, c.tables, db_text, git_file_text, e)
                c.epoch_hits.append(eh)
                c.score += eh.score
                c.max_score += e.weight

            if "operator_audit_log" not in c.tables:
                c.warnings.append("missing operator_audit_log")
            if "bot_admins" not in c.tables:
                c.warnings.append("missing bot_admins")
            if not any("phone_access" in t for t in c.tables):
                c.warnings.append("no explicit phone_access tables")
            if "service_orders" not in c.tables:
                c.warnings.append("missing service_orders")
            if len(c.tables) < 80:
                c.warnings.append("schema is smaller than live-services branch candidates")

            c.ok = True
        finally:
            conn.close()
    except Exception as e:
        c.ok = False
        c.error = str(e)
    return c


def discover(db_dir: Path, includes: list[str]) -> list[Path]:
    paths = []
    if includes:
        for pat in includes:
            paths.extend(Path(p) for p in glob.glob(pat))
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
            out.append(p)
            seen.add(p)
    return out


def rel(path: Path, base: Path) -> str:
    try:
        return str(path.relative_to(base))
    except Exception:
        return str(path)


def render(cands: list[Candidate], db_dir: Path, top: int) -> str:
    ordered = sorted(cands, key=lambda x: (x.ok, x.score, x.table_count, x.size_bytes), reverse=True)
    lines = []
    lines.append("# OSBB Deep Project Epoch Archaeology")
    lines.append("")
    lines.append(f"Generated: {datetime.now():%Y-%m-%d %H:%M:%S}")
    lines.append(f"DB root: `{db_dir}`")
    lines.append("")
    lines.append("## Executive verdict")
    lines.append("")
    if ordered and ordered[0].ok:
        best = ordered[0]
        pct = best.score / best.max_score * 100 if best.max_score else 0
        lines.append(f"Recommended evolutionary branch: `{best.path}`")
        lines.append("")
        lines.append(f"Epoch score: **{best.score}/{best.max_score}** ({pct:.1f}%)")
        lines.append(f"Tables: **{best.table_count}**, size: **{best.size_bytes/1024/1024:.2f} MB**, modified: **{best.mtime}**")
        lines.append("")
        lines.append("Interpretation: this DB appears to be the most advanced live-services sandbox branch. It should be smoke-tested before promotion.")
    else:
        lines.append("No readable candidates.")
    lines.append("")

    lines.append("## Ranking")
    lines.append("")
    lines.append("| Rank | Epoch score | Tables | Size MB | Modified | DB |")
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
    for c in ordered[:top]:
        by_key = {h.definition.key: h for h in c.epoch_hits}
        vals = []
        for e in EPOCHS:
            h = by_key.get(e.key)
            vals.append(f"{h.score}/{e.weight}" if h else "-")
        lines.append(f"| `{rel(c.path, db_dir)}` | {c.score}/{c.max_score} | " + " | ".join(vals) + " |")
    lines.append("")

    lines.append(f"## Deep details for top {min(top, len(ordered))}")
    lines.append("")
    for c in ordered[:top]:
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

        if c.row_counts:
            lines.append("- Key row counts:")
            for k, v in sorted(c.row_counts.items()):
                lines.append(f"  - `{k}`: {v}")

        if c.warnings:
            lines.append("- Warnings:")
            for w in c.warnings:
                lines.append(f"  - {w}")

        if c.schema_text_hits:
            lines.append("- Schema/migration-like tables:")
            for x in c.schema_text_hits[:20]:
                lines.append(f"  - `{x}`")

        if c.timestamp_hits:
            lines.append("- Latest timestamp signals:")
            for x in c.timestamp_hits[:12]:
                lines.append(f"  - {x}")

        lines.append("")
        lines.append("#### Epoch evidence")
        lines.append("")
        for h in c.epoch_hits:
            if h.score == 0:
                continue
            lines.append(f"**{h.definition.title}: {h.score}/{h.definition.weight}**")
            for ev in h.evidence[:8]:
                if ev.kind == "none":
                    continue
                lines.append(f"- {ev.kind}: {ev.value}")
            lines.append("")

        if c.metadata_hits:
            lines.append("#### Metadata table samples")
            lines.append("")
            for x in c.metadata_hits[:20]:
                lines.append(f"- {x}")
            lines.append("")

        if c.audit_hits:
            lines.append("#### Audit text samples")
            lines.append("")
            for x in c.audit_hits[:20]:
                lines.append(f"- {x}")
            lines.append("")

    lines.append("## Promotion checklist")
    lines.append("")
    lines.append("- [ ] Backup current `osbb_test.db`.")
    lines.append("- [ ] Copy recommended sandbox DB to a temporary promotion candidate, not directly over `osbb_test.db`.")
    lines.append("- [ ] Run DB passport / schema snapshot on the candidate.")
    lines.append("- [ ] Run bot smoke tests: resident mode, admin mode, agreement, vehicles, service orders, phone access.")
    lines.append("- [ ] Verify second admin sees Admin mode.")
    lines.append("- [ ] Only then promote candidate as new working training DB.")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db-dir", default="Data/db")
    ap.add_argument("--repo", default="")
    ap.add_argument("--include", action="append", default=[])
    ap.add_argument("--out", default="Data/exports/db_rank/project_epoch_archaeology.md")
    ap.add_argument("--top", type=int, default=8)
    args = ap.parse_args()

    db_dir = Path(args.db_dir).resolve()
    repo = Path(args.repo).resolve() if args.repo else Path.cwd()
    git_files = run_git_files(repo)
    git_text = "\n".join(git_files)

    candidates = discover(db_dir, args.include)
    reports = [inspect_db(p, git_text) for p in candidates]

    out = Path(args.out)
    if not out.is_absolute():
        out = Path.cwd() / out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(reports, db_dir, args.top), encoding="utf-8")

    readable = [r for r in reports if r.ok]
    best = sorted(readable, key=lambda x: (x.score, x.table_count, x.size_bytes), reverse=True)[0] if readable else None

    print("=" * 100)
    print("OSBB deep project epoch archaeology")
    print("=" * 100)
    print("Candidates:", len(candidates))
    print("Git files seen:", len(git_files))
    print("Out:", out)
    if best:
        print("Recommended:", best.path)
        print("Epoch score:", f"{best.score}/{best.max_score}")
        print("Tables:", best.table_count)
    print("READ ONLY COMPLETED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
