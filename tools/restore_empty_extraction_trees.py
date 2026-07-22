#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
restore_empty_extraction_trees.py

Restores files from Project Archaeologist harvest into existing empty extraction
folders inside the OSBB project.

Purpose:
- many historical ZIP bundles were extracted into project subfolders;
- source files were then moved out, leaving empty folder skeletons;
- Project Archaeologist can recover the source files into files_by_module;
- this tool can repopulate matching empty folders from the archaeology INDEX.

Safety:
- DRY RUN by default;
- never overwrites existing non-empty files unless --overwrite is passed;
- only restores into existing directories;
- writes RESTORE_EMPTY_TREES_REPORT.md;
- intended as a bridge before normal Git review.

Typical workflow:

1) Run archaeology:
   python .\OSBB\tools\project_archaeologist.py scan --name-filter OSBB --apply

2) Dry-run restore:
   python .\OSBB\tools\restore_empty_extraction_trees.py `
     --index "G:\Programming\Py\OSBB\Recovered\project_archaeology_...\INDEX.json"

3) Apply:
   python .\OSBB\tools\restore_empty_extraction_trees.py `
     --index "G:\Programming\Py\OSBB\Recovered\project_archaeology_...\INDEX.json" `
     --apply

4) Review:
   git status
   git diff --stat
"""

from __future__ import annotations

import argparse
import json
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


DEFAULT_ROOT = Path(r"G:\Programming\Py\OSBB")

RESTORABLE_EXTS = {
    ".py", ".bat", ".cmd", ".ps1", ".sql", ".md", ".txt",
    ".json", ".yaml", ".yml", ".ini", ".cfg",
}

SKIP_DIR_NAMES = {
    ".git", "__pycache__", ".venv", "venv", "node_modules",
    ".mypy_cache", ".pytest_cache",
}

IMPORTANT_MODULES = {
    "service_preorders_core",
    "service_orders_core",
    "phone_barrier_access_service",
    "cashier_v2_core",
    "cashier_operator_v2",
    "cashier_operator",
    "service_orders_workspace",
    "client_portal",
    "guard_workspace",
    "commercial_contract_editor",
    "vehicle_card_editor",
    "unit_registry_editor",
    "run_bot_guard_sandbox_v3",
    "run_bot_live_services_sandbox",
}


@dataclass
class RestorePlan:
    source: Path
    target: Path
    reason: str
    module: str
    sha256: str


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def is_emptyish_dir(path: Path) -> bool:
    if not path.is_dir():
        return False
    for p in path.iterdir():
        if p.name in SKIP_DIR_NAMES:
            continue
        if p.is_file():
            return False
        if p.is_dir() and not is_emptyish_dir(p):
            return False
    return True


def discover_empty_dirs(root: Path) -> list[Path]:
    out = []
    for p in root.rglob("*"):
        if not p.is_dir():
            continue
        if any(part in SKIP_DIR_NAMES for part in p.parts):
            continue
        if is_emptyish_dir(p):
            out.append(p)
    return sorted(out, key=lambda x: len(x.parts), reverse=True)


def load_index(index: Path) -> list[dict]:
    return json.loads(index.read_text(encoding="utf-8"))


def best_artifacts_by_module(items: list[dict]) -> dict[str, dict]:
    best: dict[str, dict] = {}
    for x in items:
        module = x.get("logical_name") or Path(x.get("basename", "")).stem
        extracted = x.get("extracted_to") or ""
        if not extracted or not Path(extracted).exists():
            continue
        if Path(x.get("basename", "")).suffix.lower() not in RESTORABLE_EXTS:
            continue

        prev = best.get(module)
        if prev is None:
            best[module] = x
            continue

        # Prefer larger/newer artifacts; this is conservative but useful.
        key = (x.get("origin_modified", ""), int(x.get("size") or 0))
        prev_key = (prev.get("origin_modified", ""), int(prev.get("size") or 0))
        if key > prev_key:
            best[module] = x
    return best


def dir_hint(path: Path) -> str:
    text = str(path).lower().replace("\\", "/")
    name = path.name.lower()
    if "service" in text or "remote" in text or "pult" in text:
        return "service"
    if "cash" in text or "kassa" in text or "payment" in text:
        return "cashier"
    if "phone" in text or "barrier" in text:
        return "phone"
    if "guard" in text:
        return "guard"
    if "client" in text or "resident" in text:
        return "client"
    if "commercial" in text or "contract" in text:
        return "commercial"
    return name


def artifact_matches_dir(module: str, target_dir: Path) -> bool:
    hint = dir_hint(target_dir)
    m = module.lower()
    t = str(target_dir).lower().replace("\\", "/")

    if module in IMPORTANT_MODULES:
        # Important core modules may live at project root or in service-like restored dirs.
        if target_dir == DEFAULT_ROOT:
            return True

    if hint in m:
        return True
    if any(part in t for part in m.split("_") if len(part) >= 5):
        return True

    # Conservative grouped matches.
    if hint == "service" and any(k in m for k in ["service", "remote", "pult", "supplier"]):
        return True
    if hint == "cashier" and any(k in m for k in ["cashier", "cashbox", "payment"]):
        return True
    if hint == "phone" and any(k in m for k in ["phone", "barrier"]):
        return True
    if hint == "client" and any(k in m for k in ["client", "resident", "profile"]):
        return True
    if hint == "commercial" and any(k in m for k in ["commercial", "contract", "unit"]):
        return True
    return False


def build_plan(root: Path, index_items: list[dict], only_important: bool) -> list[RestorePlan]:
    best = best_artifacts_by_module(index_items)
    empty_dirs = discover_empty_dirs(root)
    plans: list[RestorePlan] = []

    for module, art in best.items():
        if only_important and module not in IMPORTANT_MODULES:
            continue
        src = Path(art["extracted_to"])
        basename = art.get("basename") or src.name

        # First target preference: project root for root-style core files.
        candidates = []
        if module in IMPORTANT_MODULES and module.endswith("_core"):
            candidates.append(root)

        # Then matching empty folders.
        candidates.extend([d for d in empty_dirs if artifact_matches_dir(module, d)])

        # If no folder matches, skip; this tool only fills existing skeleton.
        if not candidates:
            continue

        target_dir = candidates[0]
        target = target_dir / basename
        plans.append(
            RestorePlan(
                source=src,
                target=target,
                reason=art.get("reason", ""),
                module=module,
                sha256=art.get("sha256", ""),
            )
        )

    # Deduplicate by target.
    dedup: dict[str, RestorePlan] = {}
    for p in plans:
        dedup[str(p.target)] = p
    return list(dedup.values())


def write_report(root: Path, report_path: Path, plans: list[RestorePlan], applied: bool) -> None:
    lines = []
    lines.append("# OSBB Restore Empty Extraction Trees Report")
    lines.append("")
    lines.append(f"Generated: {now_text()}")
    lines.append(f"Applied: `{applied}`")
    lines.append(f"Root: `{root}`")
    lines.append("")
    lines.append("| Module | Source | Target | Reason | SHA256 |")
    lines.append("|---|---|---|---|---|")
    for p in plans:
        lines.append(
            f"| `{p.module}` | `{p.source}` | `{p.target}` | {p.reason} | `{p.sha256[:16]}` |"
        )
    lines.append("")
    lines.append("## Review commands")
    lines.append("")
    lines.append("```powershell")
    lines.append("git status")
    lines.append("git diff --stat")
    lines.append("```")
    report_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(DEFAULT_ROOT))
    ap.add_argument("--index", required=True)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--overwrite", action="store_true")
    ap.add_argument("--all", action="store_true", help="restore non-important modules too")
    args = ap.parse_args()

    root = Path(args.root)
    index = Path(args.index)

    if not root.exists():
        raise SystemExit(f"Root not found: {root}")
    if not index.exists():
        raise SystemExit(f"Index not found: {index}")

    items = load_index(index)
    plans = build_plan(root, items, only_important=not args.all)

    print("=" * 100)
    print("OSBB restore empty extraction trees")
    print("=" * 100)
    print("Mode:", "APPLY" if args.apply else "DRY RUN")
    print("Root:", root)
    print("Index:", index)
    print("Plans:", len(plans))

    final_plans: list[RestorePlan] = []
    for p in plans:
        if p.target.exists() and not args.overwrite:
            print("SKIP existing:", p.target)
            continue
        print("RESTORE", p.module)
        print("  from:", p.source)
        print("  to:  ", p.target)
        final_plans.append(p)

    report = root / "Recovered" / f"RESTORE_EMPTY_TREES_REPORT_{datetime.now():%Y-%m-%d_%H-%M-%S}.md"

    if not args.apply:
        print("")
        print("DRY RUN COMPLETED. Re-run with --apply to restore.")
        write_report(root, report, final_plans, applied=False)
        print("Report:", report)
        return 0

    for p in final_plans:
        p.target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p.source, p.target)

    write_report(root, report, final_plans, applied=True)

    print("")
    print("APPLY COMPLETED")
    print("Report:", report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
