#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
project_archaeologist.py

OSBB project archaeologist.

Scans ZIP archives, recovered bundles, and optionally the current project tree.
Builds an inventory of historical source files and extracts candidates into a
quarantine archive without touching the live project.

Main goals:
- recover source files that were moved out of project folders;
- index ZIP bundles from the peak development period;
- group file versions by logical module name;
- identify missing live sources when only __pycache__ remains;
- generate reports for later human review and controlled restoration.

Default scan inputs:
  C:\Users\<user>\Downloads
  G:\Programming\Py\OSBB\Recovered

Default output:
  G:\Programming\Py\OSBB\Recovered\project_archaeology_<timestamp>

Examples:

  Dry run:
    python .\OSBB\tools\project_archaeologist.py scan

  Extract and write full archive:
    python .\OSBB\tools\project_archaeologist.py scan --apply

  Search for one module after harvest:
    python .\OSBB\tools\project_archaeologist.py history --module service_preorders_core --index "G:\Programming\Py\OSBB\Recovered\project_archaeology_...\INDEX.json"

  Use custom downloads folder:
    python .\OSBB\tools\project_archaeologist.py scan --downloads "D:\Downloads" --apply

Notes:
- This tool never overwrites project code.
- It extracts to Recovered/project_archaeology_* only.
- Restoration into live OSBB must be done by a separate explicit patch.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import shutil
import zipfile
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Iterable


DEFAULT_ROOT = Path(r"G:\Programming\Py\OSBB")
DEFAULT_DOWNLOADS = Path.home() / "Downloads"
DEFAULT_RECOVERED = DEFAULT_ROOT / "Recovered"
DEFAULT_PROJECT = DEFAULT_ROOT

SOURCE_EXTS = {
    ".py", ".bat", ".cmd", ".ps1", ".sql", ".md", ".txt",
    ".json", ".yaml", ".yml", ".ini", ".cfg",
}

PY_SOURCE_EXTS = {".py"}

IMPORTANT_NAMES = {
    "service_preorders_core.py",
    "service_orders_core.py",
    "phone_barrier_access_service.py",
    "cashier_v2_core.py",
    "cashier_operator_v2.py",
    "cashier_operator.py",
    "service_orders_workspace.py",
    "client_portal.py",
    "guard_workspace.py",
    "commercial_contract_editor.py",
    "vehicle_card_editor.py",
    "unit_registry_editor.py",
    "run_bot_guard_sandbox_v3.py",
    "run_bot_live_services_sandbox.py",
    "MIGRATE_simplified_services_sandbox.py",
    "RUN_MIGRATE_simplified_services_sandbox.bat",
    "Start_OSBB_Live_Services_Sandbox_Bot_v1.bat",
    "Start_OSBB_Guard_Sandbox_Bot_v2.bat",
}

IMPORTANT_PATTERNS = [
    "service_preorder",
    "service_order",
    "phone_barrier",
    "phone_access",
    "cashier",
    "payment",
    "cashbox",
    "remote",
    "pult",
    "supplier",
    "live_services",
    "guard_sandbox",
    "guard",
    "commercial_contract",
    "unit_group",
    "resident",
    "client_portal",
    "operator",
    "migration",
    "migrate",
    "sandbox",
    "parking_time",
    "profile_verification",
]

SKIP_DIRS = {
    "__pycache__",
    ".git",
    ".venv",
    "venv",
    "node_modules",
    ".mypy_cache",
    ".pytest_cache",
}


@dataclass
class Artifact:
    origin_type: str
    origin_path: str
    origin_modified: str
    member_path: str
    basename: str
    logical_name: str
    ext: str
    size: int
    sha256: str
    reason: str
    extracted_to: str = ""
    py_functions: list[str] | None = None
    py_classes: list[str] | None = None
    py_imports: list[str] | None = None
    py_doc_first: str = ""


def now_stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d_%H-%M-%S")


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def mtime_text(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def safe_name(s: str) -> str:
    s = s.replace("\\", "/")
    s = re.sub(r"[^A-Za-z0-9А-Яа-яЁё._ -]+", "_", s)
    s = s.strip(" ._")
    return s or "item"


def logical_name_for(path_text: str) -> str:
    base = Path(path_text.replace("\\", "/")).name
    stem = Path(base).stem

    # Normalize common backup/version suffixes.
    stem = re.sub(r" - Copy$", "", stem, flags=re.IGNORECASE)
    stem = re.sub(r"_copy$", "", stem, flags=re.IGNORECASE)
    stem = re.sub(r"_v\d+$", "", stem, flags=re.IGNORECASE)
    stem = re.sub(r"_before_.+$", "", stem, flags=re.IGNORECASE)
    stem = re.sub(r"\.before_.+$", "", stem, flags=re.IGNORECASE)
    stem = re.sub(r"_\d{4}-\d{2}-\d{2}.*$", "", stem)
    return stem


def should_skip_path(path_text: str) -> bool:
    parts = [p.lower() for p in path_text.replace("\\", "/").split("/")]
    return any(p in SKIP_DIRS for p in parts)


def classify(path_text: str) -> tuple[bool, str]:
    if should_skip_path(path_text):
        return False, "skip cache/internal"

    base = Path(path_text.replace("\\", "/")).name
    ext = Path(base).suffix.lower()
    low = path_text.lower()

    if ext not in SOURCE_EXTS:
        return False, "extension not tracked"

    if base in IMPORTANT_NAMES:
        return True, "important exact filename"

    hits = [p for p in IMPORTANT_PATTERNS if p in low]
    if hits:
        return True, "keyword: " + ", ".join(hits[:6])

    if ext == ".py" and ("osbb" in low or "bot" in low or "handler" in low):
        return True, "generic OSBB python"

    return False, "not selected"


def py_summary(data: bytes) -> tuple[list[str], list[str], list[str], str]:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("cp1251", errors="replace")

    try:
        tree = ast.parse(text)
    except Exception:
        return [], [], [], ""

    funcs: list[str] = []
    classes: list[str] = []
    imports: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            funcs.append(node.name)
        elif isinstance(node, ast.ClassDef):
            classes.append(node.name)
        elif isinstance(node, ast.Import):
            imports.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.append(node.module or "")

    doc = ast.get_docstring(tree) or ""
    first = doc.strip().splitlines()[0] if doc.strip() else ""
    return funcs[:80], classes[:40], imports[:80], first[:200]


def discover_zip_files(paths: Iterable[Path], name_filter: str | None = None) -> list[Path]:
    found: dict[str, Path] = {}
    for root in paths:
        if not root.exists():
            continue
        for p in root.rglob("*.zip"):
            if name_filter and name_filter.lower() not in p.name.lower():
                continue
            found[str(p.resolve())] = p
    return sorted(found.values(), key=lambda p: p.stat().st_mtime, reverse=True)


def discover_plain_files(paths: Iterable[Path]) -> list[Path]:
    found: dict[str, Path] = {}
    for root in paths:
        if not root.exists():
            continue
        for p in root.rglob("*"):
            if not p.is_file():
                continue
            ok, _ = classify(str(p))
            if ok:
                found[str(p.resolve())] = p
    return sorted(found.values(), key=lambda p: p.stat().st_mtime, reverse=True)


def add_artifact(
    artifacts: list[Artifact],
    data: bytes,
    origin_type: str,
    origin_path: Path,
    origin_modified: str,
    member_path: str,
    reason: str,
    out_dir: Path | None,
) -> None:
    base = Path(member_path.replace("\\", "/")).name
    ext = Path(base).suffix.lower()
    digest = sha256(data)
    extracted_to = ""

    if out_dir is not None:
        logical = logical_name_for(member_path)
        module_dir = out_dir / "files_by_module" / safe_name(logical)
        module_dir.mkdir(parents=True, exist_ok=True)
        out_name = f"{safe_name(Path(base).stem)}__{digest[:12]}{ext}"
        out = module_dir / out_name
        if not out.exists():
            out.write_bytes(data)
        extracted_to = str(out)

    funcs = classes = imports = None
    doc_first = ""
    if ext in PY_SOURCE_EXTS:
        funcs, classes, imports, doc_first = py_summary(data)

    artifacts.append(
        Artifact(
            origin_type=origin_type,
            origin_path=str(origin_path),
            origin_modified=origin_modified,
            member_path=member_path,
            basename=base,
            logical_name=logical_name_for(member_path),
            ext=ext,
            size=len(data),
            sha256=digest,
            reason=reason,
            extracted_to=extracted_to,
            py_functions=funcs,
            py_classes=classes,
            py_imports=imports,
            py_doc_first=doc_first,
        )
    )


def scan_zips(zips: list[Path], artifacts: list[Artifact], out_dir: Path | None) -> None:
    for i, zp in enumerate(zips, 1):
        print(f"[zip {i}/{len(zips)}] {zp.name}")
        try:
            with zipfile.ZipFile(zp) as zf:
                for info in zf.infolist():
                    if info.is_dir():
                        continue
                    ok, reason = classify(info.filename)
                    if not ok:
                        continue
                    try:
                        data = zf.read(info)
                    except Exception as exc:
                        print("  member read error:", info.filename, exc)
                        continue
                    add_artifact(
                        artifacts=artifacts,
                        data=data,
                        origin_type="zip",
                        origin_path=zp,
                        origin_modified=mtime_text(zp),
                        member_path=info.filename,
                        reason=reason,
                        out_dir=out_dir,
                    )
        except zipfile.BadZipFile:
            print("  bad zip:", zp)
        except Exception as exc:
            print("  zip error:", zp, exc)


def scan_plain(files: list[Path], artifacts: list[Artifact], out_dir: Path | None, origin_type: str) -> None:
    for i, p in enumerate(files, 1):
        print(f"[{origin_type} {i}/{len(files)}] {p.name}")
        ok, reason = classify(str(p))
        if not ok:
            continue
        try:
            data = p.read_bytes()
        except Exception as exc:
            print("  read error:", p, exc)
            continue
        add_artifact(
            artifacts=artifacts,
            data=data,
            origin_type=origin_type,
            origin_path=p,
            origin_modified=mtime_text(p),
            member_path=str(p),
            reason=reason,
            out_dir=out_dir,
        )


def group_by_logical(artifacts: list[Artifact]) -> dict[str, list[Artifact]]:
    groups: dict[str, list[Artifact]] = {}
    for a in artifacts:
        groups.setdefault(a.logical_name, []).append(a)
    for items in groups.values():
        items.sort(key=lambda x: (x.origin_modified, x.size), reverse=True)
    return dict(sorted(groups.items()))


def detect_live_missing_sources(project_root: Path) -> list[str]:
    missing = []
    pycache = project_root / "__pycache__"
    if not pycache.exists():
        return missing
    for pyc in pycache.glob("*.pyc"):
        m = re.match(r"(.+)\.cpython-\d+\.pyc$", pyc.name)
        if not m:
            continue
        source = project_root / f"{m.group(1)}.py"
        if not source.exists():
            missing.append(f"{source.name} missing, but {pyc.name} exists")
    return missing


def write_reports(out_dir: Path, artifacts: list[Artifact], project_root: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    index = [asdict(a) for a in artifacts]
    (out_dir / "INDEX.json").write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")

    groups = group_by_logical(artifacts)

    lines: list[str] = []
    lines.append("# OSBB Project Archaeology Report")
    lines.append("")
    lines.append(f"Generated: {now_text()}")
    lines.append("")
    lines.append(f"Artifacts found: **{len(artifacts)}**")
    lines.append(f"Logical modules: **{len(groups)}**")
    lines.append("")

    missing = detect_live_missing_sources(project_root)
    lines.append("## Missing live sources suggested by __pycache__")
    lines.append("")
    if missing:
        for x in missing:
            lines.append(f"- {x}")
    else:
        lines.append("No missing source hints detected in root __pycache__.")
    lines.append("")

    priority_names = {logical_name_for(n) for n in IMPORTANT_NAMES}
    priority_groups = {k: v for k, v in groups.items() if k in priority_names or any(p in k.lower() for p in IMPORTANT_PATTERNS)}

    lines.append("## Priority modules")
    lines.append("")
    lines.append("| Module | Versions | Best/latest candidate | Reason | Extracted |")
    lines.append("|---|---:|---|---|---|")
    for name, items in priority_groups.items():
        best = items[0]
        unique_versions = len({a.sha256 for a in items})
        lines.append(
            f"| `{name}` | {unique_versions} | `{best.basename}` from `{Path(best.origin_path).name}` | {best.reason} | `{best.extracted_to}` |"
        )
    lines.append("")

    lines.append("## Full module history")
    lines.append("")
    for name, items in groups.items():
        lines.append(f"### `{name}`")
        lines.append("")
        lines.append(f"- Unique SHA256 versions: `{len({a.sha256 for a in items})}`")
        lines.append(f"- Occurrences: `{len(items)}`")
        lines.append("")
        lines.append("| Modified | File | Origin | Size | SHA256 | Doc / reason |")
        lines.append("|---|---|---|---:|---|---|")
        for a in items[:40]:
            note = a.py_doc_first or a.reason
            lines.append(
                f"| {a.origin_modified} | `{a.member_path}` | `{Path(a.origin_path).name}` | {a.size} | `{a.sha256[:16]}` | {note} |"
            )
        lines.append("")

    lines.append("## Restoration rule")
    lines.append("")
    lines.append("Do not copy recovered files into live project directly.")
    lines.append("First inspect the module history, choose one candidate, then restore via explicit patch.")
    lines.append("")

    (out_dir / "PROJECT_ARCHAEOLOGY_REPORT.md").write_text("\n".join(lines), encoding="utf-8")

    # Module history compact TSV for quick filtering in editor/spreadsheet.
    tsv = ["logical_name\tmodified\tbasename\torigin\tsize\tsha256\textracted_to"]
    for name, items in groups.items():
        for a in items:
            tsv.append(
                f"{name}\t{a.origin_modified}\t{a.basename}\t{a.origin_path}\t{a.size}\t{a.sha256}\t{a.extracted_to}"
            )
    (out_dir / "FILE_HISTORY.tsv").write_text("\n".join(tsv), encoding="utf-8")


def command_scan(args: argparse.Namespace) -> int:
    downloads = Path(args.downloads)
    recovered = Path(args.recovered)
    project = Path(args.project)
    out_dir = Path(args.out) if args.out else recovered / f"project_archaeology_{now_stamp()}"

    print("=" * 100)
    print("OSBB Project Archaeologist")
    print("=" * 100)
    print("Mode:", "APPLY" if args.apply else "DRY RUN")
    print("Downloads:", downloads)
    print("Recovered:", recovered)
    print("Project:", project)
    print("Output:", out_dir)
    print("Name filter:", args.name_filter or "<none>")

    zip_roots = [downloads, recovered]
    zips = discover_zip_files(zip_roots, args.name_filter)[: args.max_zips]
    print("ZIP archives:", len(zips))

    artifacts: list[Artifact] = []
    extraction_dir = out_dir if args.apply else None

    scan_zips(zips, artifacts, extraction_dir)

    if args.include_project:
        files = discover_plain_files([project])
        print("Project plain files:", len(files))
        scan_plain(files, artifacts, extraction_dir, "project")

    print("")
    print("Artifacts selected:", len(artifacts))
    print("Logical modules:", len(group_by_logical(artifacts)))

    if not args.apply:
        print("")
        print("DRY RUN COMPLETED. Re-run with --apply to extract and write reports.")
        return 0

    write_reports(out_dir, artifacts, project)

    print("")
    print("APPLY COMPLETED")
    print("Report:", out_dir / "PROJECT_ARCHAEOLOGY_REPORT.md")
    print("Index:", out_dir / "INDEX.json")
    print("History:", out_dir / "FILE_HISTORY.tsv")
    return 0


def command_history(args: argparse.Namespace) -> int:
    index_path = Path(args.index)
    if not index_path.exists():
        raise SystemExit(f"Index not found: {index_path}")
    data = json.loads(index_path.read_text(encoding="utf-8"))
    artifacts = [Artifact(**x) for x in data]
    wanted = logical_name_for(args.module)

    items = [a for a in artifacts if a.logical_name == wanted or wanted.lower() in a.logical_name.lower()]
    items.sort(key=lambda x: (x.origin_modified, x.size), reverse=True)

    print("=" * 100)
    print("OSBB Project Archaeologist: module history")
    print("=" * 100)
    print("Module:", args.module)
    print("Matches:", len(items))
    print("")

    for a in items[: args.limit]:
        print(f"{a.origin_modified} | {a.basename} | {a.size} bytes | {a.sha256[:16]}")
        print("  origin:", a.origin_path)
        print("  member:", a.member_path)
        print("  extracted:", a.extracted_to)
        if a.py_doc_first:
            print("  doc:", a.py_doc_first)
        print("")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("scan")
    p.add_argument("--downloads", default=str(DEFAULT_DOWNLOADS))
    p.add_argument("--recovered", default=str(DEFAULT_RECOVERED))
    p.add_argument("--project", default=str(DEFAULT_PROJECT))
    p.add_argument("--out", default=None)
    p.add_argument("--name-filter", default=None)
    p.add_argument("--max-zips", type=int, default=1000)
    p.add_argument("--include-project", action="store_true")
    p.add_argument("--apply", action="store_true")
    p.set_defaults(func=command_scan)

    p = sub.add_parser("history")
    p.add_argument("--index", required=True)
    p.add_argument("--module", required=True)
    p.add_argument("--limit", type=int, default=50)
    p.set_defaults(func=command_history)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
