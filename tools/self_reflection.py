#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
self_reflection.py

OSBB Self Reflection Engine

Purpose:
  Build a concise, human-readable "project self-analysis" report from:
    - Git history
    - current repository structure
    - OSBB DB candidates / sandbox evolution
    - optional archaeology reports already generated

This is NOT a migration tool.
It is a read-only reflection/reporting tool.

Typical use:

  cd G:\Programming\Py\OSBB

  python .\tools\self_reflection.py ^
    --repo "G:\Programming\Py" ^
    --osbb-root "G:\Programming\Py\OSBB" ^
    --db-dir "G:\Programming\Py\OSBB\Data\db" ^
    --since-ref "5bf92ff" ^
    --out "G:\Programming\Py\OSBB\Data\exports\reflection\OSBB_SELF_REFLECTION_2026_H1.md"

Or for last 30 commits:

  python .\tools\self_reflection.py ^
    --repo "G:\Programming\Py" ^
    --osbb-root "G:\Programming\Py\OSBB" ^
    --db-dir "G:\Programming\Py\OSBB\Data\db" ^
    --since-ref "HEAD~30" ^
    --out "G:\Programming\Py\OSBB\Data\exports\reflection\OSBB_SELF_REFLECTION_last30.md"

Safety:
  READ ONLY.
  Does not modify databases or source files.
  Writes only the requested markdown report.

Design idea:
  This module turns project traces into project memory:
    - what exists;
    - what changed;
    - which DB branch is strongest;
    - which features are mature;
    - which risks remain;
    - what should happen next.
"""

from __future__ import annotations

import argparse
import collections
import glob
import re
import sqlite3
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


# --------------------------------------------------------------------------------------
# Data models
# --------------------------------------------------------------------------------------

@dataclass
class GitSummary:
    ok: bool = False
    error: str = ""
    range_text: str = ""
    commits: list[tuple[str, str, str]] = field(default_factory=list)
    changed_files: list[tuple[str, str]] = field(default_factory=list)
    files_by_area: dict[str, list[str]] = field(default_factory=dict)
    themes: dict[str, int] = field(default_factory=dict)


@dataclass
class DbCandidate:
    path: Path
    ok: bool = False
    error: str = ""
    score: int = 0
    max_score: int = 0
    table_count: int = 0
    size_bytes: int = 0
    mtime: str = ""
    rows: dict[str, int] = field(default_factory=dict)
    features: dict[str, bool] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


@dataclass
class RepoSummary:
    file_counts: dict[str, int] = field(default_factory=dict)
    tools_count: int = 0
    bot_files_count: int = 0
    docs_count: int = 0
    db_files_count: int = 0
    notable_tools: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------------------
# Feature definitions
# --------------------------------------------------------------------------------------

FEATURES = {
    "Service Orders": {
        "weight": 14,
        "tables": ["service_orders", "service_order_steps", "service_order_interests"],
        "patterns": ["service_order", "fulfillment", "workflow"],
    },
    "Service Catalog V2": {
        "weight": 12,
        "tables": ["service_catalog", "service_items", "service_item_workflows", "service_interests"],
        "patterns": ["service_catalog", "service_items", "simplified_services"],
    },
    "Phone Barrier Access": {
        "weight": 14,
        "tables": ["phone_access_requests", "phone_access_request_points", "phone_barrier_access_points", "phone_access_subscriptions", "barrier_phone_access"],
        "patterns": ["phone_access", "phone_barrier", "barrier_phone"],
    },
    "Remote / Pult Requests": {
        "weight": 10,
        "tables": ["remote_requests", "remote_order_details", "remote_order_issued_assets"],
        "patterns": ["remote", "pult"],
    },
    "Cashbox / Kassa": {
        "weight": 12,
        "tables": ["cashbox_operations", "cashier_batches", "cashier_receipts", "payments", "payment_allocations"],
        "patterns": ["cashbox", "cashier", "kassa", "payment"],
    },
    "Debt Policy / Access Gate": {
        "weight": 12,
        "tables": ["access_debt_warnings", "access_policy_values", "access_policy_versions", "service_access_credentials"],
        "patterns": ["debt", "access_policy", "policy"],
    },
    "Roles / Operators": {
        "weight": 12,
        "tables": ["bot_admins", "access_roles", "access_user_roles", "access_role_permissions", "operator_audit_log"],
        "patterns": ["bot_admin", "operator", "role", "permission"],
    },
    "Guard / Access Control": {
        "weight": 10,
        "tables": ["access_points", "access_roles", "access_user_permissions", "access_audit_log"],
        "patterns": ["access_control", "guard", "access_points"],
    },
    "Non-residential Units": {
        "weight": 10,
        "tables": ["unit_groups", "unit_group_members", "unit_group_aliases", "unit_aliases", "commercial_contracts"],
        "patterns": ["commercial", "technical", "unit_registry", "non_residential"],
    },
    "Parking Time Review": {
        "weight": 10,
        "tables": ["parking_time_review_tasks", "profile_parking_time_test_sessions", "profile_parking_time_test_events"],
        "patterns": ["parking_time"],
    },
    "Profile Verification / Agreement": {
        "weight": 10,
        "tables": ["apartment_verification", "resident_profile_verifications", "verification_tasks", "verification_evidence", "verification_candidates"],
        "patterns": ["verification", "agreement", "profile_verification"],
    },
    "Audit / History": {
        "weight": 9,
        "tables": ["operator_audit_log", "access_audit_log", "audit_log"],
        "patterns": ["audit", "operator_audit"],
    },
    "Schema Passports / Migrations": {
        "weight": 8,
        "tables": ["schema_info", "access_schema_migrations", "resident_profile_schema_migrations", "profile_parking_time_test_schema_migrations"],
        "patterns": ["schema", "migration", "passport"],
    },
}


THEME_PATTERNS = {
    "service orders": ["service_order", "fulfillment", "workflow"],
    "phone access": ["phone_access", "phone_barrier", "barrier_phone"],
    "remote / pult": ["remote", "pult", "пульт", "перепрош"],
    "cashbox / kassa": ["cashbox", "cashier", "kassa", "касса", "payment"],
    "debt policy": ["debt", "access_policy", "policy", "задолж", "борг"],
    "roles / admins": ["bot_admin", "role", "permission", "operator", "admin"],
    "non-residential": ["commercial", "technical", "unit_group", "unit_registry", "non_residential"],
    "parking time": ["parking_time"],
    "verification": ["verification", "agreement", "соглас"],
    "audit": ["audit", "operator_audit"],
    "migration / schema": ["migration", "schema", "passport"],
}


AREA_RULES = [
    ("database", ["Data/db", ".db", "migration", "schema"]),
    ("bot", ["Bots/", "parking_bot.py", "bot_", "handler", "menu", "keyboard"]),
    ("tools", ["tools/", "inspect_", "patch_", "rank_", "detect_", "select_", "promote_", "build_"]),
    ("docs", [".md", ".txt", "README", "ROADMAP", "RELEASE", "CHECKLIST", "GUIDE"]),
    ("services", ["service_", "phone_", "remote", "pult", "cashbox", "cashier", "payment", "access_policy"]),
]


# --------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------

def run_cmd(cmd: list[str], cwd: Path) -> tuple[bool, str, str]:
    try:
        p = subprocess.run(
            cmd,
            cwd=str(cwd),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8",
            errors="replace",
        )
        return p.returncode == 0, p.stdout, p.stderr
    except Exception as e:
        return False, "", str(e)


def is_git_repo(path: Path) -> bool:
    return (path / ".git").exists()


def classify_area(path: str) -> str:
    low = path.replace("\\", "/").lower()
    for area, words in AREA_RULES:
        if any(w.lower() in low for w in words):
            return area
    return "other"


def detect_themes(text: str) -> collections.Counter:
    low = text.lower()
    c = collections.Counter()
    for theme, pats in THEME_PATTERNS.items():
        if any(p.lower() in low for p in pats):
            c[theme] += 1
    return c


def connect_ro(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)


def table_names(conn: sqlite3.Connection) -> set[str]:
    return {
        r[0]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
    }


def row_count(conn: sqlite3.Connection, table: str) -> int:
    try:
        return int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
    except Exception:
        return -1


def rel(path: Path, base: Path) -> str:
    try:
        return str(path.relative_to(base))
    except Exception:
        return str(path)


def star_rating(value: float) -> str:
    # value from 0..1
    n = max(0, min(5, round(value * 5)))
    return "★" * n + "☆" * (5 - n)


# --------------------------------------------------------------------------------------
# Git / repo scanning
# --------------------------------------------------------------------------------------

def scan_git(repo: Path, since_ref: str, until_ref: str) -> GitSummary:
    gs = GitSummary(range_text=f"{since_ref}..{until_ref}")

    if not is_git_repo(repo):
        gs.error = f"Not a git repo: {repo}"
        return gs

    ok, out, err = run_cmd(
        ["git", "log", "--reverse", "--date=short", "--pretty=format:OSBB_COMMIT|%h|%ad|%s", gs.range_text],
        repo,
    )
    if not ok:
        gs.error = err
        return gs

    for line in out.splitlines():
        if line.startswith("OSBB_COMMIT|"):
            parts = line.split("|", 3)
            if len(parts) == 4:
                gs.commits.append((parts[1], parts[2], parts[3]))

    ok, out, err = run_cmd(["git", "diff", "--name-status", gs.range_text], repo)
    if not ok:
        gs.error = err
        return gs

    for line in out.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) >= 2:
            status = parts[0].strip()
            path = parts[-1].strip()
            gs.changed_files.append((status, path))

    by_area: dict[str, list[str]] = collections.defaultdict(list)
    theme_counter = collections.Counter()

    for st, path in gs.changed_files:
        by_area[classify_area(path)].append(path)
        theme_counter.update(detect_themes(path))

    for _, _, subject in gs.commits:
        theme_counter.update(detect_themes(subject))

    gs.files_by_area = dict(by_area)
    gs.themes = dict(theme_counter)
    gs.ok = True
    return gs


def scan_repo(osbb_root: Path) -> RepoSummary:
    rs = RepoSummary()

    if not osbb_root.exists():
        return rs

    suffix_counts = collections.Counter()
    notable_tools = []

    for p in osbb_root.rglob("*"):
        if not p.is_file():
            continue
        if any(part in {".git", "__pycache__", ".venv", "venv"} for part in p.parts):
            continue

        suffix = p.suffix.lower() or "(no suffix)"
        suffix_counts[suffix] += 1

        r = p.relative_to(osbb_root).as_posix()
        if r.startswith("tools/"):
            rs.tools_count += 1
            if p.suffix.lower() == ".py" and any(x in p.name for x in ["inspect", "audit", "rank", "detect", "select", "patch", "promote", "build", "passport"]):
                notable_tools.append(r)
        if r.startswith("Bots/") or "bot" in p.name.lower():
            rs.bot_files_count += 1
        if p.suffix.lower() in [".md", ".txt"]:
            rs.docs_count += 1
        if p.suffix.lower() == ".db":
            rs.db_files_count += 1

    rs.file_counts = dict(suffix_counts.most_common(20))
    rs.notable_tools = sorted(notable_tools)[:80]
    return rs


# --------------------------------------------------------------------------------------
# DB scanning
# --------------------------------------------------------------------------------------

def discover_dbs(db_dir: Path) -> list[Path]:
    candidates = []
    candidates.append(db_dir / "osbb_test.db")
    candidates.append(db_dir / "osbb.db")
    candidates.extend((db_dir / "sandbox").glob("*.db"))
    candidates.extend((db_dir / "sandbox" / "backups").glob("*.db"))
    candidates.extend((db_dir / "backups").glob("*.db"))

    out = []
    seen = set()
    for p in candidates:
        p = p.resolve()
        if p.exists() and p.suffix.lower() == ".db" and p not in seen:
            out.append(p)
            seen.add(p)
    return out


def inspect_db(path: Path) -> DbCandidate:
    c = DbCandidate(path=path)
    c.size_bytes = path.stat().st_size
    c.mtime = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
    c.max_score = sum(int(v["weight"]) for v in FEATURES.values())

    try:
        conn = connect_ro(path)
        try:
            tables = table_names(conn)
            c.table_count = len(tables)

            important = [
                "apartments", "resident_accounts", "vehicles", "bot_admins",
                "operator_audit_log", "service_orders", "service_order_steps",
                "service_catalog", "service_items", "phone_access_requests",
                "phone_access_request_points", "remote_requests", "remote_order_details",
                "cashbox_operations", "payments", "payment_allocations",
                "unit_groups", "unit_group_members", "unit_group_aliases",
                "access_policy_versions", "access_policy_values",
            ]
            for t in important:
                if t in tables:
                    c.rows[t] = row_count(conn, t)

            for feature, spec in FEATURES.items():
                feature_tables = [t for t in spec["tables"] if t in tables]
                present = bool(feature_tables)
                c.features[feature] = present
                if present:
                    # Full score when at least one feature table has rows or multiple tables are present.
                    row_signal = any(row_count(conn, t) > 0 for t in feature_tables)
                    if row_signal or len(feature_tables) >= 2:
                        c.score += int(spec["weight"])
                    else:
                        c.score += int(spec["weight"]) // 2

            if "operator_audit_log" not in tables:
                c.warnings.append("missing operator_audit_log")
            if "bot_admins" not in tables:
                c.warnings.append("missing bot_admins")
            if "service_orders" not in tables:
                c.warnings.append("missing service_orders")
            if not any(t.startswith("phone_access") or "phone_barrier" in t for t in tables):
                c.warnings.append("no explicit phone access branch tables")
            if c.table_count < 80:
                c.warnings.append("smaller schema than live-services branch")

            c.ok = True
        finally:
            conn.close()
    except Exception as e:
        c.error = str(e)
        c.ok = False

    return c


def scan_dbs(db_dir: Path) -> list[DbCandidate]:
    return [inspect_db(p) for p in discover_dbs(db_dir)]


# --------------------------------------------------------------------------------------
# Reflection logic
# --------------------------------------------------------------------------------------

def maturity_scores(best: DbCandidate, gs: GitSummary, rs: RepoSummary) -> dict[str, float]:
    db_maturity = best.score / best.max_score if best and best.max_score else 0.0

    tooling = min(1.0, rs.tools_count / 60.0)
    docs = min(1.0, rs.docs_count / 40.0)

    git_memory = 0.0
    if gs.ok:
        git_memory = min(1.0, (len(gs.commits) / 20.0) * 0.4 + (len(gs.changed_files) / 300.0) * 0.6)

    risk_control = 0.75
    if best and best.warnings:
        risk_control -= min(0.35, len(best.warnings) * 0.07)
    if gs.ok and len(gs.changed_files) > 500:
        risk_control -= 0.10
    risk_control = max(0.0, min(1.0, risk_control))

    return {
        "Architectural maturity": db_maturity,
        "Tooling maturity": tooling,
        "Documentation / memory": max(docs, git_memory * 0.8),
        "Release readiness": (db_maturity * 0.55 + risk_control * 0.45),
        "Knowledge-loss resistance": min(1.0, (tooling * 0.35 + docs * 0.30 + git_memory * 0.35)),
    }


def build_narrative(best: DbCandidate, gs: GitSummary) -> list[str]:
    lines = []
    lines.append("Project OSBB has evolved beyond the original parking-bot idea into a broader building-management information system.")
    if best and best.ok:
        lines.append(
            f"The strongest detected branch is `{best.path.name}` with {best.table_count} tables and "
            f"a feature score of {best.score}/{best.max_score}."
        )
    if gs.ok:
        lines.append(
            f"The selected Git range `{gs.range_text}` contains {len(gs.commits)} commits and "
            f"{len(gs.changed_files)} changed files, which makes Git a useful development diary for release reconstruction."
        )
    lines.append("The most important immediate task is not to add another feature, but to consolidate the live-services sandbox branch into the working training database after smoke tests.")
    return lines


def top_features(best: DbCandidate) -> list[str]:
    if not best:
        return []
    return [k for k, v in best.features.items() if v]


def missing_or_weak(best: DbCandidate) -> list[str]:
    if not best:
        return []
    missing = [k for k, v in best.features.items() if not v]
    return missing + best.warnings


# --------------------------------------------------------------------------------------
# Report rendering
# --------------------------------------------------------------------------------------

def render_report(
    repo: Path,
    osbb_root: Path,
    db_dir: Path,
    gs: GitSummary,
    rs: RepoSummary,
    dbs: list[DbCandidate],
) -> str:
    readable = [d for d in dbs if d.ok]
    ordered = sorted(readable, key=lambda d: (d.score, d.table_count, d.size_bytes), reverse=True)
    best = ordered[0] if ordered else None
    scores = maturity_scores(best, gs, rs)

    lines = []
    lines.append("# OSBB Self Reflection Report")
    lines.append("")
    lines.append(f"Generated: {datetime.now():%Y-%m-%d %H:%M:%S}")
    lines.append(f"Repository root: `{repo}`")
    lines.append(f"OSBB root: `{osbb_root}`")
    lines.append(f"DB root: `{db_dir}`")
    lines.append("")

    lines.append("## Executive self-summary")
    lines.append("")
    for sentence in build_narrative(best, gs):
        lines.append(f"- {sentence}")
    lines.append("")

    lines.append("## Self-assessment")
    lines.append("")
    lines.append("| Dimension | Rating | Score |")
    lines.append("|---|---:|---:|")
    for name, value in scores.items():
        lines.append(f"| {name} | {star_rating(value)} | {value:.2f} |")
    lines.append("")

    lines.append("## Recommended release candidate")
    lines.append("")
    if best:
        lines.append(f"`{best.path}`")
        lines.append("")
        lines.append(f"- Score: **{best.score}/{best.max_score}**")
        lines.append(f"- Tables: **{best.table_count}**")
        lines.append(f"- Size: **{best.size_bytes / 1024 / 1024:.2f} MB**")
        lines.append(f"- Modified: **{best.mtime}**")
        if best.rows:
            lines.append("- Key row counts:")
            for k, v in sorted(best.rows.items()):
                lines.append(f"  - `{k}`: {v}")
        if best.warnings:
            lines.append("- Warnings:")
            for w in best.warnings:
                lines.append(f"  - {w}")
    else:
        lines.append("No readable DB candidate found.")
    lines.append("")

    lines.append("## Mature feature blocks detected")
    lines.append("")
    if best:
        for f in top_features(best):
            lines.append(f"- [x] {f}")
    else:
        lines.append("- No DB candidate available.")
    lines.append("")

    lines.append("## Weak spots / risks")
    lines.append("")
    weak = missing_or_weak(best) if best else []
    if weak:
        for w in weak:
            lines.append(f"- [ ] {w}")
    else:
        lines.append("- No major missing feature blocks detected by the current heuristics.")
    lines.append("")

    lines.append("## Git memory")
    lines.append("")
    if gs.ok:
        lines.append(f"- Range: `{gs.range_text}`")
        lines.append(f"- Commits: **{len(gs.commits)}**")
        lines.append(f"- Changed files: **{len(gs.changed_files)}**")
        if gs.themes:
            lines.append("- Detected themes:")
            for theme, count in sorted(gs.themes.items(), key=lambda x: (-x[1], x[0])):
                lines.append(f"  - {theme}: {count}")
        lines.append("")
        lines.append("### Commit timeline")
        lines.append("")
        for sha, date, subject in gs.commits[-30:]:
            lines.append(f"- `{date}` `{sha}` — {subject}")
    else:
        lines.append(f"Git scan failed: `{gs.error}`")
    lines.append("")

    lines.append("## Repository structure")
    lines.append("")
    lines.append(f"- Tools files: **{rs.tools_count}**")
    lines.append(f"- Bot-related files: **{rs.bot_files_count}**")
    lines.append(f"- Docs/text files: **{rs.docs_count}**")
    lines.append(f"- DB files: **{rs.db_files_count}**")
    if rs.file_counts:
        lines.append("- File suffix counts:")
        for suffix, count in rs.file_counts.items():
            lines.append(f"  - `{suffix}`: {count}")
    lines.append("")

    if rs.notable_tools:
        lines.append("### Notable tools")
        lines.append("")
        for t in rs.notable_tools[:60]:
            lines.append(f"- `{t}`")
        lines.append("")

    lines.append("## DB candidate ranking")
    lines.append("")
    lines.append("| Rank | Score | Tables | Size MB | Modified | DB |")
    lines.append("|---:|---:|---:|---:|---|---|")
    for i, d in enumerate(ordered, 1):
        lines.append(
            f"| {i} | {d.score}/{d.max_score} | {d.table_count} | "
            f"{d.size_bytes/1024/1024:.2f} | {d.mtime} | `{rel(d.path, db_dir)}` |"
        )
    lines.append("")

    lines.append("## Project reflection")
    lines.append("")
    lines.append("The project now has enough internal traces to explain itself: Git history, sandbox DB branches, operator audit logs, schema tables, migration traces, and documentation files.")
    lines.append("")
    lines.append("The main architectural decision is clear: the live-services sandbox branch should be treated as the new candidate baseline, while the older working DB should be considered a lagging training copy until promoted.")
    lines.append("")
    lines.append("The self-reflection module should become a regular end-of-day or pre-release ritual: run it, read the recommendations, then decide whether to promote, archive, document, or continue development.")
    lines.append("")

    lines.append("## Recommended next actions")
    lines.append("")
    lines.append("- [ ] Backup current `osbb_test.db`.")
    lines.append("- [ ] Copy the recommended sandbox DB into a temporary promotion candidate.")
    lines.append("- [ ] Run DB passport / schema snapshot on the candidate.")
    lines.append("- [ ] Run bot smoke tests: resident mode, admin mode, agreement, vehicles, service orders, phone access.")
    lines.append("- [ ] Verify that the second admin sees Admin mode.")
    lines.append("- [ ] Promote only after the smoke test is clean.")
    lines.append("- [ ] Keep this reflection report in `Data/exports/reflection/` as project memory.")
    lines.append("")

    lines.append("## Closing note")
    lines.append("")
    lines.append("This report is intentionally not only technical. Its purpose is to preserve project memory: what was built, what is mature, what is risky, and what should happen next.")
    lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default="", help="Git repository root. Default: parent of OSBB root or current directory.")
    ap.add_argument("--osbb-root", default=".", help="OSBB project root.")
    ap.add_argument("--db-dir", default="Data/db", help="OSBB database directory.")
    ap.add_argument("--since-ref", default="HEAD~30", help="Git baseline ref.")
    ap.add_argument("--until-ref", default="HEAD", help="Git end ref.")
    ap.add_argument("--out", default="Data/exports/reflection/OSBB_SELF_REFLECTION.md", help="Output markdown report.")
    args = ap.parse_args()

    osbb_root = Path(args.osbb_root).resolve()
    repo = Path(args.repo).resolve() if args.repo else osbb_root.parent.resolve()
    db_dir = Path(args.db_dir).resolve()
    out = Path(args.out)
    if not out.is_absolute():
        out = osbb_root / out

    out.parent.mkdir(parents=True, exist_ok=True)

    gs = scan_git(repo, args.since_ref, args.until_ref)
    rs = scan_repo(osbb_root)
    dbs = scan_dbs(db_dir)

    report = render_report(repo, osbb_root, db_dir, gs, rs, dbs)
    out.write_text(report, encoding="utf-8")

    readable = [d for d in dbs if d.ok]
    best = sorted(readable, key=lambda d: (d.score, d.table_count, d.size_bytes), reverse=True)[0] if readable else None

    print("=" * 100)
    print("OSBB Self Reflection Engine")
    print("=" * 100)
    print("Out:", out)
    print("Git:", "OK" if gs.ok else f"FAILED: {gs.error}")
    print("DB candidates:", len(dbs))
    if best:
        print("Recommended DB:", best.path)
        print("Score:", f"{best.score}/{best.max_score}")
        print("Tables:", best.table_count)
    print("READ ONLY COMPLETED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
