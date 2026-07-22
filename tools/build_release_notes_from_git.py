#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
build_release_notes_from_git.py

Read-only tool that builds RELEASE_NOTES draft from git history.

Examples:
  cd G:\Programming\Py\OSBB

  python .\tools\build_release_notes_from_git.py --since-ref 5bf92ff --out Data\exports\git\RELEASE_NOTES_since_osbb_test_db.md

  python .\tools\build_release_notes_from_git.py --since-ref HEAD~30 --out Data\exports\git\RELEASE_NOTES_last30.md --include-name-status
"""

from __future__ import annotations

import argparse
import collections
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass
class Commit:
    sha: str
    date: str
    subject: str
    files: list[tuple[str, str]] = field(default_factory=list)


CATEGORY_RULES = [
    ("DATABASE / MIGRATIONS", ["migrations", "migration", "migrate", "Data/db", ".db", "schema", "database", "passport"]),
    ("BOT / MENUS / HANDLERS", ["Bots/", "parking_bot.py", "bot_", "keyboard", "menu", "handler", "agreement"]),
    ("SERVICES / BUSINESS LOGIC", ["service_", "phone_", "barrier", "remote", "pult", "cash", "kassa", "payment", "tariff", "charge", "debt", "access_policy", "premises", "apartments", "vehicles"]),
    ("TOOLS / INSPECTION / ACCEPTANCE", ["tools/", "inspect_", "trace_", "test_", "acceptance", "audit_", "patch_", "promote_", "rank_"]),
    ("DOCS / EXPORTS", ["Docs/", "docs/", ".md", ".txt", "README", "ROADMAP", "RELEASE", "GUIDE", "CHECKLIST"]),
]

THEMES = [
    ("phone access", ["phone_access", "phone barrier", "barrier_phone", "телефон", "phone"]),
    ("remote / pult orders", ["remote", "pult", "пульт", "перепрош", "reprogram"]),
    ("cashbox / kassa", ["cashbox", "kassa", "касса", "cashier"]),
    ("service orders", ["service_order", "service_orders", "service_interest", "workflow"]),
    ("access policy / debt gate", ["access_policy", "debt", "задолж", "борг", "policy"]),
    ("premises / non-residential", ["premises", "commercial", "technical", "unit_type", "нежил"]),
    ("agreement / verification", ["agreement", "соглас", "verification", "verify"]),
    ("vehicles / tariffs", ["vehicle", "vehicles", "tariff", "parking_time", "auto", "car"]),
    ("bot admins / roles", ["bot_admin", "admin", "role", "permission", "operator"]),
]


def run_git(repo: Path, args: list[str]) -> str:
    p = subprocess.run(["git"] + args, cwd=str(repo), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8", errors="replace")
    if p.returncode != 0:
        raise RuntimeError("git " + " ".join(args) + "\n" + p.stderr)
    return p.stdout


def category_for(path: str) -> str:
    p = path.replace("\\", "/")
    low = p.lower()
    for cat, words in CATEGORY_RULES:
        for w in words:
            if w.lower() in low:
                return cat
    return "OTHER"


def themes_for(text: str) -> set[str]:
    low = text.lower()
    out = set()
    for name, words in THEMES:
        if any(w.lower() in low for w in words):
            out.add(name)
    return out


def parse_log(raw: str) -> list[Commit]:
    commits = []
    cur = None
    for line in raw.splitlines():
        if line.startswith("OSBB_COMMIT|"):
            _, sha, date, subject = (line.split("|", 3) + ["", "", ""])[:4]
            cur = Commit(sha=sha, date=date, subject=subject)
            commits.append(cur)
            continue
        if cur is None or not line.strip():
            continue
        m = re.match(r"^([AMDRCTUXB])\s+(.+)$", line)
        if m:
            cur.files.append((m.group(1), m.group(2).strip()))
            continue
        m = re.match(r"^R\d+\s+(.+)\s+(.+)$", line)
        if m:
            cur.files.append(("R", m.group(2).strip()))
    return commits


def build(repo: Path, since_ref: str, until_ref: str, include_name_status: bool) -> str:
    rng = f"{since_ref}..{until_ref}"
    log_raw = run_git(repo, ["log", "--name-status", "--reverse", "--date=short", "--pretty=format:OSBB_COMMIT|%h|%ad|%s", rng])
    stat_raw = run_git(repo, ["diff", "--stat", rng])
    ns_raw = run_git(repo, ["diff", "--name-status", rng])

    commits = parse_log(log_raw)
    files = []
    for line in ns_raw.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            files.append((parts[0], parts[-1]))

    files_by_cat = collections.defaultdict(list)
    for st, path in files:
        files_by_cat[category_for(path)].append((st, path))

    all_themes = set()
    commit_themes = {}
    for c in commits:
        t = themes_for(c.subject + " " + " ".join(p for _, p in c.files))
        commit_themes[c.sha] = t
        all_themes |= t

    out = []
    out.append("# OSBB Release Notes Draft")
    out.append("")
    out.append(f"Generated: {datetime.now():%Y-%m-%d %H:%M:%S}")
    out.append(f"Git range: `{rng}`")
    out.append("")
    out.append("## Summary")
    out.append("")
    out.append(f"- Commits: **{len(commits)}**")
    out.append(f"- Changed files: **{len(files)}**")
    out.append("- Detected themes: " + (", ".join(sorted(all_themes)) if all_themes else "none"))
    out.append("")
    out.append("## Candidate release checklist")
    out.append("")
    if all_themes:
        for t in sorted(all_themes):
            out.append(f"- [ ] {t}")
    else:
        out.append("- [ ] Manual review required")
    out.append("")
    out.append("## Commit timeline")
    out.append("")
    for c in commits:
        tag = f" ({', '.join(sorted(commit_themes.get(c.sha, [])))})" if commit_themes.get(c.sha) else ""
        out.append(f"### {c.date} `{c.sha}` — {c.subject}{tag}")
        grouped = collections.defaultdict(list)
        for st, path in c.files:
            grouped[category_for(path)].append((st, path))
        for cat in sorted(grouped):
            out.append(f"**{cat}**")
            for st, path in grouped[cat][:60]:
                out.append(f"- `{st}` {path}")
            if len(grouped[cat]) > 60:
                out.append(f"- ... {len(grouped[cat]) - 60} more")
        out.append("")
    out.append("## Changed files by category")
    out.append("")
    for cat in ["DATABASE / MIGRATIONS", "BOT / MENUS / HANDLERS", "SERVICES / BUSINESS LOGIC", "TOOLS / INSPECTION / ACCEPTANCE", "DOCS / EXPORTS", "OTHER"]:
        items = files_by_cat.get(cat, [])
        if not items:
            continue
        out.append(f"### {cat}")
        for st, path in items:
            out.append(f"- `{st}` {path}")
        out.append("")
    out.append("## Diff stat")
    out.append("")
    out.append("```")
    out.append(stat_raw.rstrip())
    out.append("```")
    out.append("")
    if include_name_status:
        out.append("## Full name-status diff")
        out.append("")
        out.append("```")
        out.append(ns_raw.rstrip())
        out.append("```")
        out.append("")
    out.append("## Manual review questions")
    out.append("")
    out.append("1. Which sandbox DB is strongest now?")
    out.append("2. What schema/features exist in sandbox but not in current osbb_test.db?")
    out.append("3. Are there any important records in osbb_test.db that must be preserved?")
    out.append("4. Which smoke tests must pass before promoting a sandbox DB?")
    out.append("")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=".")
    ap.add_argument("--since-ref", required=True)
    ap.add_argument("--until-ref", default="HEAD")
    ap.add_argument("--out", default="")
    ap.add_argument("--include-name-status", action="store_true")
    args = ap.parse_args()

    repo = Path(args.repo).resolve()
    if not (repo / ".git").exists():
        raise SystemExit(f"Not a git repo: {repo}")

    if args.out:
        out_path = Path(args.out)
        if not out_path.is_absolute():
            out_path = repo / out_path
    else:
        safe = (args.since_ref + "_to_" + args.until_ref).replace("/", "_").replace("\\", "_").replace("~", "tilde")
        out_path = repo / "Data" / "exports" / "git" / f"RELEASE_NOTES_{safe}.md"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(build(repo, args.since_ref, args.until_ref, args.include_name_status), encoding="utf-8")

    print("=" * 100)
    print("OSBB git release notes draft")
    print("=" * 100)
    print("Repo:", repo)
    print("Range:", f"{args.since_ref}..{args.until_ref}")
    print("Out :", out_path)
    print("READ ONLY COMPLETED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
