#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
materialize_recovered_releases.py

Creates a clean catalog of recovered OSBB release bundles from Project Archaeologist INDEX.json.
It does NOT write into live project code.

Output:
  G:\Programming\Py\OSBB\Recovered_Releases\
    2026-06-26__OSBB_Live_Service_UI_v1\
      ... exact original ZIP member tree ...

Reports:
  RELEASE_CATALOG.md
  RELEASE_CATALOG.tsv
  RELEASE_CATALOG.json

Default is DRY RUN.
"""

from __future__ import annotations

import argparse
import json
import shutil
from collections import defaultdict
from datetime import datetime
from pathlib import Path


DEFAULT_ROOT = Path(r"G:\Programming\Py\OSBB")
DEFAULT_OUT = DEFAULT_ROOT / "Recovered_Releases"

SKIP_PARTS = {"__pycache__", ".git", ".venv", "venv", "node_modules", ".mypy_cache", ".pytest_cache"}


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def safe_name(text: str) -> str:
    bad = '<>:"/\\|?*'
    out = "".join("_" if ch in bad else ch for ch in text)
    out = out.strip(" ._")
    return out or "release"


def safe_member_path(member: str) -> Path:
    parts = []
    for part in member.replace("\\", "/").split("/"):
        part = part.strip()
        if not part or part in (".", "..") or part in SKIP_PARTS:
            continue
        parts.append(part)
    return Path(*parts)


def load_index(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def bundle_stem(item: dict) -> str:
    return Path(item.get("origin_path", "")).stem


def bundle_date(item: dict) -> str:
    modified = item.get("origin_modified", "")
    return modified[:10] if len(modified) >= 10 else "unknown-date"


def release_dir_name(items: list[dict]) -> str:
    first = items[0]
    return safe_name(f"{bundle_date(first)}__{bundle_stem(first)}")


def group_by_bundle(items: list[dict]) -> dict[str, list[dict]]:
    groups = defaultdict(list)
    for item in items:
        groups[item.get("origin_path", "")].append(item)
    return dict(groups)


def build_plan(items: list[dict], out: Path, overwrite: bool):
    groups = group_by_bundle(items)
    plans = []
    skipped = {"no_source": 0, "missing_source_file": 0, "empty_member": 0, "existing": 0}
    release_infos = []

    for origin, group in groups.items():
        group.sort(key=lambda x: (x.get("member_path", ""), x.get("basename", "")))
        rel_dir = out / release_dir_name(group)
        release_infos.append((origin, rel_dir, group))

        for item in group:
            src_text = item.get("extracted_to") or ""
            if not src_text:
                skipped["no_source"] += 1
                continue
            src = Path(src_text)
            if not src.exists():
                skipped["missing_source_file"] += 1
                continue
            member = item.get("member_path") or item.get("basename") or src.name
            rel = safe_member_path(member)
            if not rel.parts:
                skipped["empty_member"] += 1
                continue
            target = rel_dir / rel
            if target.exists() and not overwrite:
                skipped["existing"] += 1
                continue
            plans.append((src, target, item, rel_dir))

    return plans, skipped, release_infos


def write_catalog(out: Path, release_infos, plans, skipped, applied: bool, index: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    by_release = defaultdict(list)
    for src, target, item, rel_dir in plans:
        by_release[str(rel_dir)].append((src, target, item))

    catalog_json = []
    for origin, rel_dir, group in release_infos:
        modules = sorted({x.get("logical_name", "") for x in group})
        catalog_json.append({
            "release_dir": str(rel_dir),
            "bundle": Path(origin).name,
            "origin_path": origin,
            "origin_modified": group[0].get("origin_modified", ""),
            "artifacts": len(group),
            "modules": modules,
        })

    (out / "RELEASE_CATALOG.json").write_text(json.dumps(catalog_json, ensure_ascii=False, indent=2), encoding="utf-8")

    tsv = ["release_dir\tbundle\tmodified\tartifacts\tmodules"]
    for row in catalog_json:
        tsv.append(f"{row['release_dir']}\t{row['bundle']}\t{row['origin_modified']}\t{row['artifacts']}\t{', '.join(row['modules'])}")
    (out / "RELEASE_CATALOG.tsv").write_text("\n".join(tsv), encoding="utf-8")

    md = []
    md.append("# OSBB Recovered Releases Catalog")
    md.append("")
    md.append(f"Generated: {now_text()}")
    md.append(f"Applied: `{applied}`")
    md.append(f"Index: `{index}`")
    md.append(f"Output: `{out}`")
    md.append("")
    md.append(f"- Planned/restored files: `{len(plans)}`")
    md.append(f"- Skipped no source in index: `{skipped['no_source']}`")
    md.append(f"- Skipped missing recovered source file: `{skipped['missing_source_file']}`")
    md.append(f"- Skipped empty member: `{skipped['empty_member']}`")
    md.append(f"- Skipped existing target: `{skipped['existing']}`")
    md.append("")
    md.append("## Releases")
    md.append("")
    md.append("| Release folder | Bundle | Modified | Artifacts | Modules |")
    md.append("|---|---|---|---:|---:|")
    for row in sorted(catalog_json, key=lambda r: (r["origin_modified"], r["bundle"])):
        md.append(f"| `{Path(row['release_dir']).name}` | `{row['bundle']}` | {row['origin_modified']} | {row['artifacts']} | {len(row['modules'])} |")

    md.append("")
    md.append("## Rule")
    md.append("")
    md.append("Recovered_Releases is a historical catalog. Do not edit files here manually.")
    md.append("Use it as a source for comparison and explicit restoration patches.")
    md.append("")

    (out / "RELEASE_CATALOG.md").write_text("\n".join(md), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--index", required=True)
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--overwrite", action="store_true")
    args = ap.parse_args()

    index = Path(args.index)
    out = Path(args.out)
    if not index.exists():
        raise SystemExit(f"Index not found: {index}")

    items = load_index(index)
    plans, skipped, release_infos = build_plan(items, out, overwrite=args.overwrite)

    print("=" * 100)
    print("OSBB materialize recovered releases")
    print("=" * 100)
    print("Mode:", "APPLY" if args.apply else "DRY RUN")
    print("Index:", index)
    print("Output:", out)
    print("Releases:", len(release_infos))
    print("Files planned:", len(plans))
    print("Skipped:", skipped)
    print("")

    for origin, rel_dir, group in sorted(release_infos, key=lambda x: (x[2][0].get("origin_modified", ""), Path(x[0]).name)):
        print(f"RELEASE {Path(rel_dir).name} | bundle={Path(origin).name} | artifacts={len(group)}")

    out.mkdir(parents=True, exist_ok=True)
    if not args.apply:
        write_catalog(out, release_infos, plans, skipped, applied=False, index=index)
        print("")
        print("DRY RUN COMPLETED. Re-run with --apply to materialize release folders.")
        print("Catalog:", out / "RELEASE_CATALOG.md")
        return 0

    for src, target, item, rel_dir in plans:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)

    write_catalog(out, release_infos, plans, skipped, applied=True, index=index)
    print("")
    print("APPLY COMPLETED")
    print("Catalog:", out / "RELEASE_CATALOG.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
