#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
promote_peak_service_orders_workspace.py

Promote the best recovered "adult" service_orders_workspace.py into the live OSBB tree.

Idea:
- Runner/sandbox once helped the module reach an adult state.
- The project now needs that adult state as normal source code, not hidden behind a launcher.
- This tool performs the obvious step safely:
    recovered peak 27.06 workspace -> Bots/handlers/service_orders_workspace.py

Default peak candidate:
  Recovered_Releases\2026-06-27__OSBB_phone_barrier_access_v2_working_sandbox\
    phone_barrier_access_v2_payload\Bots\handlers\service_orders_workspace.py

Safety:
- dry-run by default;
- backs up current file before replacing;
- checks Python syntax with py_compile;
- writes a promotion report;
- does not touch DB;
- does not launch bot.

Usage:

  Dry run:
    python .\OSBB\tools\promote_peak_service_orders_workspace.py

  Apply:
    python .\OSBB\tools\promote_peak_service_orders_workspace.py --apply

  Apply another candidate:
    python .\OSBB\tools\promote_peak_service_orders_workspace.py `
      --source "G:\...\service_orders_workspace.py" `
      --apply
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import py_compile
import shutil
import sys
from datetime import datetime
from pathlib import Path


DEFAULT_ROOT = Path(r"G:\Programming\Py\OSBB")
DEFAULT_TARGET = DEFAULT_ROOT / "Bots" / "handlers" / "service_orders_workspace.py"
DEFAULT_SOURCE = (
    DEFAULT_ROOT
    / "Recovered_Releases"
    / "2026-06-27__OSBB_phone_barrier_access_v2_working_sandbox"
    / "phone_barrier_access_v2_payload"
    / "Bots"
    / "handlers"
    / "service_orders_workspace.py"
)

KEY_MARKERS = [
    "NEW_REMOTE_PROFILE",
    "REMOTE_NEW_PREORDER",
    "choose_quantity",
    "supplier_batches",
    "issue_new_remotes_from_batch",
    "REMOTE_BATCH_ISSUED",
    "Заявки на пульты",
    "Телефонный доступ",
    "PHONE_ACCESS_CONNECT",
    "remote_supplier_batches",
    "remote_supplier_batch_links",
    "remote_order_issued_assets",
    "service_preorders_core",
]


def now_stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d_%H-%M-%S")


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def read_text(path: Path) -> str:
    for enc in ("utf-8", "utf-8-sig", "cp1251"):
        try:
            return path.read_text(encoding=enc)
        except UnicodeDecodeError:
            pass
    return path.read_text(encoding="utf-8", errors="replace")


def sha12(path: Path) -> str:
    return hashlib.sha256(read_text(path).encode("utf-8", errors="replace")).hexdigest()[:12]


def marker_report(path: Path) -> list[tuple[str, bool]]:
    text = read_text(path)
    return [(m, m in text) for m in KEY_MARKERS]


def compile_check(path: Path) -> tuple[bool, str]:
    try:
        py_compile.compile(str(path), doraise=True)
        return True, "OK"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def write_report(
    report_path: Path,
    source: Path,
    target: Path,
    backup: Path | None,
    diff_path: Path,
    applied: bool,
    source_compile: tuple[bool, str],
    target_compile: tuple[bool, str] | None,
) -> None:
    lines: list[str] = []
    lines.append("# Promote peak service_orders_workspace")
    lines.append("")
    lines.append(f"Generated: `{now_text()}`")
    lines.append(f"Applied: `{applied}`")
    lines.append("")
    lines.append("## Source")
    lines.append("")
    lines.append(f"`{source}`")
    lines.append(f"- SHA: `{sha12(source)}`")
    lines.append(f"- Size: `{source.stat().st_size}` bytes")
    lines.append(f"- Syntax: `{source_compile[0]}` / `{source_compile[1]}`")
    lines.append("")
    lines.append("## Target")
    lines.append("")
    lines.append(f"`{target}`")
    if target.exists():
        lines.append(f"- SHA: `{sha12(target)}`")
        lines.append(f"- Size: `{target.stat().st_size}` bytes")
    lines.append("")
    if backup:
        lines.append("## Backup")
        lines.append("")
        lines.append(f"`{backup}`")
        lines.append("")
    lines.append("## Diff")
    lines.append("")
    lines.append(f"`{diff_path}`")
    lines.append("")
    lines.append("## Markers in source")
    lines.append("")
    lines.append("| Marker | Present |")
    lines.append("|---|---:|")
    for marker, present in marker_report(source):
        lines.append(f"| `{marker}` | {'yes' if present else 'no'} |")
    lines.append("")
    lines.append("## Markers in target after operation")
    lines.append("")
    lines.append("| Marker | Present |")
    lines.append("|---|---:|")
    for marker, present in marker_report(target if applied else source):
        lines.append(f"| `{marker}` | {'yes' if present else 'no'} |")
    lines.append("")
    if target_compile:
        lines.append("## Target syntax after operation")
        lines.append("")
        lines.append(f"`{target_compile[0]}` / `{target_compile[1]}`")
        lines.append("")
    lines.append("## Next checks")
    lines.append("")
    lines.append("```powershell")
    lines.append("python -m py_compile .\\OSBB\\Bots\\handlers\\service_orders_workspace.py")
    lines.append("git diff -- OSBB/Bots/handlers/service_orders_workspace.py")
    lines.append("git status --short")
    lines.append("```")
    lines.append("")
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_diff(source: Path, target: Path, diff_path: Path) -> None:
    source_lines = read_text(source).splitlines()
    target_lines = read_text(target).splitlines() if target.exists() else []
    diff = difflib.unified_diff(
        target_lines,
        source_lines,
        fromfile=str(target),
        tofile=str(source),
        lineterm="",
        n=3,
    )
    diff_path.write_text("\n".join(diff) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(DEFAULT_ROOT))
    ap.add_argument("--source", default=str(DEFAULT_SOURCE))
    ap.add_argument("--target", default=str(DEFAULT_TARGET))
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    root = Path(args.root)
    source = Path(args.source)
    target = Path(args.target)

    if not root.exists():
        raise SystemExit(f"Root not found: {root}")
    if not source.exists():
        raise SystemExit(f"Source not found: {source}")
    if not target.parent.exists():
        raise SystemExit(f"Target directory not found: {target.parent}")

    recovered = root / "Recovered"
    recovered.mkdir(parents=True, exist_ok=True)

    stamp = now_stamp()
    report_path = recovered / f"PROMOTE_PEAK_SERVICE_ORDERS_WORKSPACE_{stamp}.md"
    diff_path = recovered / f"PROMOTE_PEAK_SERVICE_ORDERS_WORKSPACE_{stamp}.patch"

    source_compile = compile_check(source)
    if not source_compile[0]:
        print("SOURCE SYNTAX FAILED:", source_compile[1])
        print("Refusing to continue.")
        return 1

    write_diff(source, target, diff_path)

    print("=" * 100)
    print("Promote peak service_orders_workspace")
    print("=" * 100)
    print("Mode:", "APPLY" if args.apply else "DRY RUN")
    print("Source:", source)
    print("Target:", target)
    print("Source SHA:", sha12(source), "size:", source.stat().st_size)
    if target.exists():
        print("Target SHA:", sha12(target), "size:", target.stat().st_size)
    print("Source syntax:", source_compile)
    print("")
    print("Markers:")
    for marker, present in marker_report(source):
        print(f"  {'OK' if present else '--'} {marker}")
    print("")
    print("Diff:", diff_path)

    backup = None
    target_compile = None

    if args.apply:
        backup_dir = root / "Data" / "backups" / "source_code" / f"before_promote_peak_workspace_{stamp}"
        backup_dir.mkdir(parents=True, exist_ok=True)

        if target.exists():
            backup = backup_dir / "Bots" / "handlers" / "service_orders_workspace.py"
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, backup)
            print("Backup:", backup)

        shutil.copy2(source, target)
        target_compile = compile_check(target)

        if not target_compile[0]:
            print("TARGET SYNTAX FAILED AFTER COPY:", target_compile[1])
            if backup and backup.exists():
                shutil.copy2(backup, target)
                print("Restored backup:", backup)
            write_report(report_path, source, target, backup, diff_path, False, source_compile, target_compile)
            print("Report:", report_path)
            return 1

        print("Copied peak workspace to target.")
        print("Target syntax:", target_compile)

    write_report(report_path, source, target, backup, diff_path, args.apply, source_compile, target_compile)

    print("")
    print("Report:", report_path)
    if not args.apply:
        print("")
        print("Dry run only. To apply:")
        print("python .\\OSBB\\tools\\promote_peak_service_orders_workspace.py --apply")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
