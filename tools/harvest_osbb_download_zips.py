#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
harvest_osbb_download_zips.py

Scans ZIP archives from Downloads, extracts OSBB-related source files into Recovered,
and writes HARVEST_REPORT.md plus harvest_inventory.json.

It does not overwrite live project files.

Examples:
  python .\OSBB\tools\harvest_osbb_download_zips.py
  python .\OSBB\tools\harvest_osbb_download_zips.py --apply
  python .\OSBB\tools\harvest_osbb_download_zips.py --downloads "C:\Users\first\Downloads" --apply
  python .\OSBB\tools\harvest_osbb_download_zips.py --name-filter OSBB --apply
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import zipfile
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path


DEFAULT_ROOT = Path(r"G:\Programming\Py\OSBB")
DEFAULT_DOWNLOADS = Path.home() / "Downloads"
DEFAULT_RECOVERED = DEFAULT_ROOT / "Recovered"

IMPORTANT_NAMES = {
    "service_preorders_core.py",
    "service_orders_core.py",
    "phone_barrier_access_service.py",
    "cashier_v2_core.py",
    "cashier_operator_v2.py",
    "cashier_operator.py",
    "service_orders_workspace.py",
    "commercial_contract_editor.py",
    "client_portal.py",
    "run_bot_guard_sandbox_v3.py",
    "run_bot_live_services_sandbox.py",
    "MIGRATE_simplified_services_sandbox.py",
    "RUN_MIGRATE_simplified_services_sandbox.bat",
    "Start_OSBB_Live_Services_Sandbox_Bot_v1.bat",
    "Start_OSBB_Guard_Sandbox_Bot_v2.bat",
}

IMPORTANT_PATTERNS = [
    "service_preorder", "service_order", "phone_barrier", "phone_access",
    "cashier", "pult", "remote", "supplier", "live_services",
    "guard_sandbox", "commercial_contract", "unit_group", "resident",
    "client_portal", "operator", "migration", "migrate", "sandbox",
]

VALUABLE_EXTS = {".py", ".bat", ".cmd", ".ps1", ".sql", ".md", ".txt", ".json", ".yaml", ".yml"}
SKIP_PARTS = {"__pycache__", ".git", ".venv", "venv", "node_modules"}


@dataclass
class Hit:
    zip_path: str
    zip_modified: str
    zip_size: int
    member: str
    basename: str
    ext: str
    reason: str
    size: int
    sha256: str
    extracted_to: str = ""


def now_stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d_%H-%M-%S")


def modified_text(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def safe_part(s: str) -> str:
    s = s.replace("\\", "/")
    s = re.sub(r"[^A-Za-z0-9А-Яа-яЁё._ -]+", "_", s)
    s = s.strip(" ._")
    return s or "file"


def should_skip_member(member: str) -> bool:
    parts = [p.lower() for p in member.replace("\\", "/").split("/")]
    return any(p in SKIP_PARTS for p in parts)


def classify_member(member: str) -> tuple[bool, str]:
    if should_skip_member(member):
        return False, "skip cache/internal"

    p = Path(member)
    base = p.name
    ext = p.suffix.lower()
    low = member.lower()

    if ext not in VALUABLE_EXTS:
        return False, "extension not tracked"

    if base in IMPORTANT_NAMES:
        return True, "important exact filename"

    hits = [pat for pat in IMPORTANT_PATTERNS if pat in low]
    if hits:
        return True, "keyword: " + ", ".join(hits[:5])

    if ext in {".py", ".sql"} and ("osbb" in low or "bot" in low or "handler" in low):
        return True, "generic OSBB/code hint"

    return False, "not OSBB-relevant enough"


def discover_zips(downloads: Path, name_filter: str | None) -> list[Path]:
    if not downloads.exists():
        raise SystemExit(f"Downloads folder not found: {downloads}")

    zips = []
    for p in downloads.rglob("*.zip"):
        if name_filter and name_filter.lower() not in p.name.lower():
            continue
        zips.append(p)
    return sorted(zips, key=lambda x: x.stat().st_mtime, reverse=True)


def build_reports(hits: list[Hit], out_dir: Path, scanned_zips: list[Path]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "harvest_inventory.json").write_text(
        json.dumps([asdict(h) for h in hits], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    md = []
    md.append("# OSBB Downloads ZIP Harvest")
    md.append("")
    md.append(f"Generated: {datetime.now():%Y-%m-%d %H:%M:%S}")
    md.append("")
    md.append(f"Scanned ZIP files: **{len(scanned_zips)}**")
    md.append(f"Extracted / candidate hits: **{len(hits)}**")
    md.append("")
    md.append("## Highest priority files")
    md.append("")

    priority = [
        h for h in hits
        if h.basename in IMPORTANT_NAMES
        or any(k in h.member.lower() for k in ["service_preorder", "service_order", "phone_barrier", "cashier_v2"])
    ]

    if not priority:
        md.append("No high-priority hits found.")
    else:
        md.append("| File | Reason | ZIP | Extracted to |")
        md.append("|---|---|---|---|")
        for h in priority[:300]:
            md.append(f"| `{h.member}` | {h.reason} | `{Path(h.zip_path).name}` | `{h.extracted_to}` |")

    md.append("")
    md.append("## All hits by ZIP")
    md.append("")

    by_zip = {}
    for h in hits:
        by_zip.setdefault(h.zip_path, []).append(h)

    for zip_path, items in by_zip.items():
        md.append(f"### `{Path(zip_path).name}`")
        md.append("")
        md.append(f"- Path: `{zip_path}`")
        md.append(f"- Modified: `{items[0].zip_modified}`")
        md.append(f"- Hits: `{len(items)}`")
        md.append("")
        md.append("| Member | Reason | Size | SHA256 | Extracted |")
        md.append("|---|---|---:|---|---|")
        for h in items:
            md.append(f"| `{h.member}` | {h.reason} | {h.size} | `{h.sha256[:16]}` | `{h.extracted_to}` |")
        md.append("")

    md.append("## Scanned ZIP list")
    md.append("")
    for z in scanned_zips:
        md.append(f"- `{z}` | {modified_text(z)} | {z.stat().st_size} bytes")

    (out_dir / "HARVEST_REPORT.md").write_text("\n".join(md), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--downloads", default=str(DEFAULT_DOWNLOADS))
    ap.add_argument("--out-root", default=str(DEFAULT_RECOVERED))
    ap.add_argument("--name-filter", default=None)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--max-zips", type=int, default=500)
    args = ap.parse_args()

    downloads = Path(args.downloads)
    session_dir = Path(args.out_root) / f"download_zips_harvest_{now_stamp()}"

    print("=" * 100)
    print("OSBB Downloads ZIP harvest")
    print("=" * 100)
    print("Mode:", "APPLY" if args.apply else "DRY RUN")
    print("Downloads:", downloads)
    print("Output:", session_dir)
    print("Name filter:", args.name_filter or "<none>")

    zips = discover_zips(downloads, args.name_filter)[:args.max_zips]
    print("ZIP files discovered:", len(zips))

    hits: list[Hit] = []

    for i, zip_path in enumerate(zips, 1):
        print(f"[{i}/{len(zips)}] {zip_path.name}")
        try:
            with zipfile.ZipFile(zip_path) as zf:
                for info in zf.infolist():
                    if info.is_dir():
                        continue
                    ok, reason = classify_member(info.filename)
                    if not ok:
                        continue
                    data = zf.read(info)
                    digest = sha256_bytes(data)
                    extracted = ""

                    if args.apply:
                        zip_folder = safe_part(zip_path.stem)
                        member_name = safe_part(info.filename.replace("\\", "/").replace("/", "__"))
                        out_dir = session_dir / zip_folder
                        out_dir.mkdir(parents=True, exist_ok=True)
                        out = out_dir / member_name
                        if out.exists():
                            out = out.with_name(f"{out.stem}_{digest[:8]}{out.suffix}")
                        out.write_bytes(data)
                        extracted = str(out)

                    hits.append(Hit(
                        zip_path=str(zip_path),
                        zip_modified=modified_text(zip_path),
                        zip_size=zip_path.stat().st_size,
                        member=info.filename,
                        basename=Path(info.filename).name,
                        ext=Path(info.filename).suffix.lower(),
                        reason=reason,
                        size=info.file_size,
                        sha256=digest,
                        extracted_to=extracted,
                    ))
        except zipfile.BadZipFile:
            print("  SKIP: bad zip")
        except Exception as e:
            print("  ERROR:", e)

    print("")
    print("Hits:", len(hits))

    if not args.apply:
        print("")
        print("DRY RUN COMPLETED. Re-run with --apply to extract and write report.")
        return 0

    build_reports(hits, session_dir, zips)
    print("")
    print("APPLY COMPLETED")
    print("Report:", session_dir / "HARVEST_REPORT.md")
    print("Inventory:", session_dir / "harvest_inventory.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
