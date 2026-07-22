#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
restore_release_tree_to_project.py

Restores recovered ZIP bundle contents from Project Archaeologist INDEX.json.

Supports BOTH cases:

1) skeleton mode:
   restore into OSBB\<zip_stem>\...

2) overlay mode:
   restore into OSBB\... using original member paths,
   for bundles meant to be unpacked over project root.

Default: dry-run, no overwrites.

Examples:

  python .\OSBB\tools\restore_release_tree_to_project.py --index "...\INDEX.json" --list-bundles

  python .\OSBB\tools\restore_release_tree_to_project.py --index "...\INDEX.json" --bundle OSBB_Live_Service_UI_v1.zip --mode overlay

  python .\OSBB\tools\restore_release_tree_to_project.py --index "...\INDEX.json" --bundle OSBB_Live_Service_UI_v1.zip --mode overlay --apply

  python .\OSBB\tools\restore_release_tree_to_project.py --index "...\INDEX.json" --bundle OSBB_simplified_services_preorders_bundle.zip --mode skeleton --apply
"""

from __future__ import annotations

import argparse
import json
import shutil
from collections import defaultdict
from datetime import datetime
from pathlib import Path


DEFAULT_ROOT = Path(r"G:\Programming\Py\OSBB")

SKIP_PARTS = {"__pycache__", ".git", ".venv", "venv", "node_modules", ".mypy_cache", ".pytest_cache"}
PROJECT_OVERLAY_HINTS = {"Bots", "Data", "Docs", "docs", "tools", "migrations", "tests"}


def now_stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d_%H-%M-%S")


def load_index(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def bundle_name(item: dict) -> str:
    return Path(item.get("origin_path", "")).name


def bundle_stem(item: dict) -> str:
    return Path(item.get("origin_path", "")).stem


def safe_member_path(member: str) -> Path:
    parts = []
    for part in member.replace("\\", "/").split("/"):
        part = part.strip()
        if not part or part in (".", "..") or part in SKIP_PARTS:
            continue
        parts.append(part)
    return Path(*parts)


def has_project_overlay_shape(items: list[dict]) -> bool:
    for item in items:
        member = item.get("member_path", "")
        parts = [p for p in member.replace("\\", "/").split("/") if p]
        if not parts:
            continue
        if parts[0] in PROJECT_OVERLAY_HINTS:
            return True
        name = parts[-1].lower()
        if name.startswith(("run_", "start_", "install_", "migrate_", "check_", "patch_", "prepare_")):
            return True
    return False


def find_skeleton_folder(root: Path, stem: str) -> Path | None:
    direct = root / stem
    if direct.is_dir():
        return direct
    target = stem.lower()
    for p in root.iterdir():
        if p.is_dir() and p.name.lower() == target:
            return p
    return None


def choose_target_base(root: Path, stem: str, items: list[dict], mode: str) -> tuple[Path, str]:
    if mode == "overlay":
        return root, "overlay"
    if mode == "skeleton":
        return (find_skeleton_folder(root, stem) or (root / stem)), "skeleton"
    folder = find_skeleton_folder(root, stem)
    if folder is not None:
        return folder, "skeleton"
    if has_project_overlay_shape(items):
        return root, "overlay"
    return root / stem, "skeleton-created"


def list_bundles(items: list[dict]) -> None:
    groups = defaultdict(list)
    for item in items:
        groups[bundle_name(item)].append(item)
    rows = []
    for name, group in groups.items():
        rows.append((name, len(group), len({x.get("logical_name") for x in group}), has_project_overlay_shape(group), bundle_stem(group[0])))
    rows.sort()
    print("Bundles:")
    for name, count, modules, overlay, stem in rows:
        print(f"- {name} | artifacts={count} | modules={modules} | overlay_shape={overlay} | stem={stem}")


def build_plan(root: Path, items: list[dict], wanted_bundle: str, mode: str, overwrite: bool):
    selected = [
        x for x in items
        if bundle_name(x).lower() == wanted_bundle.lower()
        or bundle_stem(x).lower() == wanted_bundle.lower()
    ]
    if not selected:
        names = sorted({bundle_name(x) for x in items})
        raise SystemExit("Bundle not found: " + wanted_bundle + "\nKnown bundles:\n  " + "\n  ".join(names))

    stem = bundle_stem(selected[0])
    target_base, resolved_mode = choose_target_base(root, stem, selected, mode)
    plans = []
    skipped = {"no_source": 0, "existing": 0, "empty_member": 0}

    for item in selected:
        src_text = item.get("extracted_to") or ""
        if not src_text:
            skipped["no_source"] += 1
            continue
        src = Path(src_text)
        if not src.exists():
            skipped["no_source"] += 1
            continue
        member = item.get("member_path") or item.get("basename") or src.name
        rel = safe_member_path(member)
        if not rel.parts:
            skipped["empty_member"] += 1
            continue
        target = target_base / rel
        if target.exists() and not overwrite:
            skipped["existing"] += 1
            continue
        plans.append((src, target, member, item))
    return selected, target_base, resolved_mode, plans, skipped


def write_report(root: Path, index: Path, bundle: str, resolved_mode: str, target_base: Path, plans, skipped, applied: bool) -> Path:
    report_dir = root / "Recovered"
    report_dir.mkdir(parents=True, exist_ok=True)
    report = report_dir / f"RESTORE_RELEASE_TREE_{now_stamp()}.md"
    lines = [
        "# Restore release tree to project",
        "",
        f"Generated: {datetime.now():%Y-%m-%d %H:%M:%S}",
        f"Applied: `{applied}`",
        f"Bundle: `{bundle}`",
        f"Mode: `{resolved_mode}`",
        f"Root: `{root}`",
        f"Target base: `{target_base}`",
        f"Index: `{index}`",
        "",
        f"- Plans: `{len(plans)}`",
        f"- Skipped no source: `{skipped['no_source']}`",
        f"- Skipped existing: `{skipped['existing']}`",
        f"- Skipped empty member: `{skipped['empty_member']}`",
        "",
        "| Member | Source | Target | SHA256 |",
        "|---|---|---|---|",
    ]
    for src, target, member, item in plans:
        lines.append(f"| `{member}` | `{src}` | `{target}` | `{str(item.get('sha256', ''))[:16]}` |")
    lines += ["", "## Review commands", "", "```powershell", "git status", "git diff --stat", "```", ""]
    report.write_text("\n".join(lines), encoding="utf-8")
    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(DEFAULT_ROOT))
    ap.add_argument("--index", required=True)
    ap.add_argument("--bundle", default=None)
    ap.add_argument("--mode", choices=["auto", "overlay", "skeleton"], default="auto")
    ap.add_argument("--list-bundles", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--overwrite", action="store_true")
    args = ap.parse_args()

    root = Path(args.root)
    index = Path(args.index)
    if not root.exists():
        raise SystemExit(f"Root not found: {root}")
    if not index.exists():
        raise SystemExit(f"Index not found: {index}")

    items = load_index(index)
    if args.list_bundles:
        list_bundles(items)
        return 0
    if not args.bundle:
        raise SystemExit("--bundle is required unless --list-bundles is used")

    selected, target_base, resolved_mode, plans, skipped = build_plan(root, items, args.bundle, args.mode, args.overwrite)

    print("=" * 100)
    print("OSBB restore release tree to project")
    print("=" * 100)
    print("Mode:", "APPLY" if args.apply else "DRY RUN")
    print("Bundle:", args.bundle)
    print("Resolved mode:", resolved_mode)
    print("Target base:", target_base)
    print("Artifacts in bundle:", len(selected))
    print("Plans:", len(plans))
    print("Skipped:", skipped)
    print("")

    for src, target, member, item in plans[:300]:
        print("RESTORE")
        print("  member:", member)
        print("  from:  ", src)
        print("  to:    ", target)
    if len(plans) > 300:
        print(f"... and {len(plans) - 300} more")

    report = write_report(root, index, args.bundle, resolved_mode, target_base, plans, skipped, applied=args.apply)

    if not args.apply:
        print("")
        print("DRY RUN COMPLETED. Re-run with --apply to restore.")
        print("Report:", report)
        return 0

    for src, target, member, item in plans:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)

    print("")
    print("APPLY COMPLETED")
    print("Report:", report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
