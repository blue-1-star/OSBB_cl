#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
restore_zip_named_skeletons.py

Restores recovered files into existing OSBB folders whose names match ZIP bundle names.

Example skeleton folders:
  OSBB_cashier_route_fix
  OSBB_service_code_compatibility_repair_phone_v2
  OSBB_simplified_services_preorders_bundle

Source:
  Recovered\project_archaeology_...\INDEX.json

Dry run:
  python .\OSBB\tools\restore_zip_named_skeletons.py --index "G:\Programming\Py\OSBB\Recovered\project_archaeology_2026-07-02_22-59-35\INDEX.json"

Apply:
  python .\OSBB\tools\restore_zip_named_skeletons.py --index "G:\Programming\Py\OSBB\Recovered\project_archaeology_2026-07-02_22-59-35\INDEX.json" --apply
"""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime
from pathlib import Path


DEFAULT_ROOT = Path(r"G:\Programming\Py\OSBB")


def now_stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d_%H-%M-%S")


def safe_member_path(member: str) -> Path:
    parts = []
    for part in member.replace("\\", "/").split("/"):
        part = part.strip()
        if not part or part in (".", ".."):
            continue
        parts.append(part)
    return Path(*parts)


def bundle_folder_name(origin_path: str) -> str:
    return Path(origin_path).stem


def load_index(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def find_existing_folder(root: Path, folder_name: str) -> Path | None:
    direct = root / folder_name
    if direct.is_dir():
        return direct

    target = folder_name.lower()
    for p in root.iterdir():
        if p.is_dir() and p.name.lower() == target:
            return p
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(DEFAULT_ROOT))
    ap.add_argument("--index", required=True)
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

    plans = []
    skipped_no_folder = 0
    skipped_no_source = 0
    skipped_existing = 0

    for item in items:
        origin = item.get("origin_path", "")
        folder_name = bundle_folder_name(origin)
        target_root = find_existing_folder(root, folder_name)

        if target_root is None:
            skipped_no_folder += 1
            continue

        src_text = item.get("extracted_to", "")
        if not src_text:
            skipped_no_source += 1
            continue

        src = Path(src_text)
        if not src.exists():
            skipped_no_source += 1
            continue

        member = item.get("member_path", item.get("basename", src.name))
        target = target_root / safe_member_path(member)

        if target.exists() and not args.overwrite:
            skipped_existing += 1
            continue

        plans.append((src, target, folder_name, member))

    print("=" * 100)
    print("OSBB restore ZIP-named skeleton folders")
    print("=" * 100)
    print("Mode:", "APPLY" if args.apply else "DRY RUN")
    print("Root:", root)
    print("Index:", index)
    print("Plans:", len(plans))
    print("Skipped no matching folder:", skipped_no_folder)
    print("Skipped no recovered source:", skipped_no_source)
    print("Skipped existing:", skipped_existing)
    print("")

    for src, target, folder_name, member in plans[:300]:
        print("RESTORE")
        print("  bundle:", folder_name)
        print("  member:", member)
        print("  from:  ", src)
        print("  to:    ", target)

    if len(plans) > 300:
        print(f"... and {len(plans) - 300} more")

    report_dir = root / "Recovered"
    report_dir.mkdir(parents=True, exist_ok=True)
    report = report_dir / f"RESTORE_ZIP_NAMED_SKELETONS_{now_stamp()}.md"

    lines = [
        "# Restore ZIP-named skeleton folders",
        "",
        f"Generated: {datetime.now():%Y-%m-%d %H:%M:%S}",
        f"Applied: `{args.apply}`",
        f"Root: `{root}`",
        f"Index: `{index}`",
        "",
        f"- Plans: `{len(plans)}`",
        f"- Skipped no matching folder: `{skipped_no_folder}`",
        f"- Skipped no recovered source: `{skipped_no_source}`",
        f"- Skipped existing: `{skipped_existing}`",
        "",
        "| Bundle folder | Member | Source | Target |",
        "|---|---|---|---|",
    ]

    for src, target, folder_name, member in plans:
        lines.append(f"| `{folder_name}` | `{member}` | `{src}` | `{target}` |")

    report.write_text("\n".join(lines), encoding="utf-8")

    if not args.apply:
        print("")
        print("DRY RUN COMPLETED. Re-run with --apply.")
        print("Report:", report)
        return 0

    for src, target, folder_name, member in plans:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)

    print("")
    print("APPLY COMPLETED")
    print("Report:", report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
