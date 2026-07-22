#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
module_registry_manager.py

Maintains MODULE.md passports for OSBB bot handlers.

Purpose:
- treat every Bots/handlers/*.py as a business module;
- create/update a companion *.MODULE.md file;
- keep a central registry in docs/modules/HANDLERS_MODULE_REGISTRY.md;
- record quick human notes before they are forgotten.

Default:
  G:\Programming\Py\OSBB\Bots\handlers
  G:\Programming\Py\OSBB\docs\modules\HANDLERS_MODULE_REGISTRY.md

Examples:
  python .\OSBB\tools\module_registry_manager.py scan
  python .\OSBB\tools\module_registry_manager.py scan --apply
  python .\OSBB\tools\module_registry_manager.py registry --apply
  python .\OSBB\tools\module_registry_manager.py note --module client_portal --text "Resident identity flow is legacy." --apply
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import re
from datetime import datetime
from pathlib import Path


DEFAULT_ROOT = Path(r"G:\Programming\Py\OSBB")
DEFAULT_HANDLERS_DIR = DEFAULT_ROOT / "Bots" / "handlers"
DEFAULT_DOCS_MODULES_DIR = DEFAULT_ROOT / "docs" / "modules"
DEFAULT_REGISTRY = DEFAULT_DOCS_MODULES_DIR / "HANDLERS_MODULE_REGISTRY.md"

SKIP_NAMES = {"__init__.py"}
SKIP_PATTERNS = [" - Copy.py", "_v2.py", "_v3.py", ".before_", "_before_"]


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def is_primary_handler(path: Path) -> bool:
    if path.name in SKIP_NAMES:
        return False
    if path.suffix != ".py":
        return False
    return not any(pat in path.name for pat in SKIP_PATTERNS)


def read_text_safe(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="cp1251", errors="replace")


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def module_md_path(handler: Path) -> Path:
    return handler.with_suffix(".MODULE.md")


def format_list(items: list[str]) -> str:
    return "\n".join(f"- `{x}`" for x in items)


def extract_python_summary(path: Path) -> dict:
    text = read_text_safe(path)
    result = {
        "docstring": "",
        "functions": [],
        "classes": [],
        "imports": [],
        "buttons": [],
        "tables_keywords": [],
    }

    try:
        tree = ast.parse(text)
        result["docstring"] = ast.get_docstring(tree) or ""
        for node in tree.body:
            if isinstance(node, ast.Import):
                result["imports"].extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                result["imports"].append(node.module or "")
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                result["functions"].append(node.name)
            elif isinstance(node, ast.ClassDef):
                result["classes"].append(node.name)
    except Exception:
        pass

    button_hits = []
    for m in re.finditer(r"['\"]([^'\"]{2,80})['\"]", text):
        s = m.group(1)
        if any(ch in s for ch in "🏠🚗🔑☎💰📋👤🧪✅❌") or any(
            word in s.lower()
            for word in [
                "квартира", "авто", "пульт", "телефон", "платеж", "оплат",
                "мешкан", "админ", "оператор", "догов", "parking", "remote",
                "phone", "cash", "payment", "contract",
            ]
        ):
            button_hits.append(s)
    result["buttons"] = sorted(set(button_hits))[:40]

    for kw in [
        "resident_accounts", "vehicles", "remote_requests", "service_orders",
        "phone_access_requests", "payments", "payment_allocations",
        "cashbox_operations", "commercial_contracts", "unit_groups",
        "operator_audit_log", "audit_log", "apartments", "charges",
    ]:
        if kw in text:
            result["tables_keywords"].append(kw)

    return result


def make_module_template(handler: Path) -> str:
    summary = extract_python_summary(handler)
    name = handler.stem
    doc = summary["docstring"].strip().splitlines()
    first_doc = doc[0] if doc else "TODO: describe module purpose."
    funcs = summary["functions"][:30]
    classes = summary["classes"][:20]
    tables = summary["tables_keywords"]
    buttons = summary["buttons"][:20]

    return f"""# MODULE: {name}

Status: draft
Handler: `{handler.name}`
Created: {now_text()}
Last reviewed: TODO
Code hash at creation: `{file_hash(handler)}`

## Purpose

{first_doc}

## Business meaning

TODO: describe what real OSBB process this module supports.

Examples:

- resident self-service;
- operator workspace;
- payments/cashbox;
- remote/pult requests;
- phone access;
- vehicle registry;
- commercial contracts;
- guard workspace.

## Current implementation evidence

### Functions

{format_list(funcs) if funcs else "- TODO: no top-level functions detected or parser could not read them."}

### Classes

{format_list(classes) if classes else "- None detected."}

### Database / schema keywords found

{format_list(tables) if tables else "- TODO: no known DB table keywords detected."}

### User-facing text / buttons found

{format_list(buttons) if buttons else "- TODO: no obvious buttons/text detected."}

## Dependencies

TODO: list related handlers, services, tables, migrations.

## Entry points

TODO: list where this module is called from.

## Known legacy / risks

TODO: record confusing old flows, duplicates, copy-files, partial implementations.

## Acceptance tests

- [ ] Opens without error.
- [ ] Main menu path is known.
- [ ] Creates expected DB records.
- [ ] Operator/admin can see created records.
- [ ] Status change works.
- [ ] Audit trail is written where needed.
- [ ] Tested on Working DB, not Golden Master.

## Current notes

<!-- MODULE_NOTES:BEGIN -->
<!-- MODULE_NOTES:END -->

## Change log

<!-- MODULE_CHANGELOG:BEGIN -->
- {now_text()} - MODULE.md created by module_registry_manager.py.
<!-- MODULE_CHANGELOG:END -->
"""


def append_between_markers(text: str, begin: str, end: str, addition: str) -> str:
    if begin not in text or end not in text:
        return text.rstrip() + f"\n\n{begin}\n{addition}\n{end}\n"
    start = text.index(begin) + len(begin)
    finish = text.index(end)
    middle = text[start:finish].strip()
    if middle:
        middle = middle + "\n" + addition
    else:
        middle = addition
    return text[:start] + "\n" + middle + "\n" + text[finish:]


def scan(args) -> int:
    handlers_dir = Path(args.handlers_dir)
    files = sorted(p for p in handlers_dir.glob("*.py") if is_primary_handler(p))
    print("=" * 100)
    print("OSBB handler MODULE.md scan")
    print("=" * 100)
    print("Mode:", "APPLY" if args.apply else "DRY RUN")
    print("Handlers:", handlers_dir)
    print("Primary handlers:", len(files))

    planned = []
    for handler in files:
        md = module_md_path(handler)
        planned.append((handler, md, "exists" if md.exists() else "create"))

    for handler, md, action in planned:
        print(f"{action.upper():8} {handler.name} -> {md.name}")

    if not args.apply:
        print("\nDRY RUN COMPLETED. Re-run with --apply to write missing MODULE.md files.")
        return 0

    for handler, md, action in planned:
        if action == "create":
            md.write_text(make_module_template(handler), encoding="utf-8")
    print("\nAPPLY COMPLETED")
    return 0


def add_note(args) -> int:
    handlers_dir = Path(args.handlers_dir)
    mod = args.module[:-3] if args.module.endswith(".py") else args.module
    handler = handlers_dir / f"{mod}.py"
    md = handlers_dir / f"{mod}.MODULE.md"

    if not handler.exists():
        raise SystemExit(f"Handler not found: {handler}")

    text = md.read_text(encoding="utf-8") if md.exists() else make_module_template(handler)
    note = f"- {now_text()} - {args.text}"
    text = append_between_markers(text, "<!-- MODULE_NOTES:BEGIN -->", "<!-- MODULE_NOTES:END -->", note)
    text = append_between_markers(text, "<!-- MODULE_CHANGELOG:BEGIN -->", "<!-- MODULE_CHANGELOG:END -->", f"- {now_text()} - Note added.")

    print("=" * 100)
    print("OSBB module note")
    print("=" * 100)
    print("Mode:", "APPLY" if args.apply else "DRY RUN")
    print("Module:", mod)
    print("MODULE.md:", md)
    print("Text:", args.text)

    if not args.apply:
        print("\nDRY RUN COMPLETED. Re-run with --apply to write.")
        return 0

    md.write_text(text, encoding="utf-8")
    print("\nAPPLY COMPLETED")
    return 0


def rebuild_registry(args) -> int:
    handlers_dir = Path(args.handlers_dir)
    out = Path(args.registry)
    files = sorted(p for p in handlers_dir.glob("*.py") if is_primary_handler(p))
    lines = [
        "# OSBB Handlers Module Registry",
        "",
        f"Generated: {now_text()}",
        "",
        f"Handlers directory: `{handlers_dir}`",
        "",
        "| Module | Handler | MODULE.md | Status | Size KB | Modified | Functions | DB keywords |",
        "|---|---|---|---|---:|---|---:|---|",
    ]

    for handler in files:
        md = module_md_path(handler)
        summary = extract_python_summary(handler)
        modified = datetime.fromtimestamp(handler.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        lines.append(
            f"| `{handler.stem}` | `{handler.name}` | `{md.name if md.exists() else ''}` | "
            f"{'documented' if md.exists() else 'missing MODULE.md'} | "
            f"{handler.stat().st_size // 1024} | {modified} | {len(summary['functions'])} | "
            f"{', '.join(summary['tables_keywords'][:6])} |"
        )

    lines += [
        "",
        "## Operating rule",
        "",
        "Every meaningful handler should have a companion `*.MODULE.md` file.",
        "",
        "When a decision, defect, test result, or important observation appears during work, add it immediately:",
        "",
        "```powershell",
        'python .\OSBB\tools\module_registry_manager.py note --module client_portal --text "..." --apply',
        "```",
        "",
    ]

    print("=" * 100)
    print("OSBB handlers registry")
    print("=" * 100)
    print("Mode:", "APPLY" if args.apply else "DRY RUN")
    print("Registry:", out)
    print("Modules:", len(files))

    if not args.apply:
        print("\nDRY RUN COMPLETED. Re-run with --apply to write registry.")
        return 0

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    print("\nAPPLY COMPLETED")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("scan")
    p.add_argument("--handlers-dir", default=str(DEFAULT_HANDLERS_DIR))
    p.add_argument("--apply", action="store_true")
    p.set_defaults(func=scan)

    p = sub.add_parser("note")
    p.add_argument("--handlers-dir", default=str(DEFAULT_HANDLERS_DIR))
    p.add_argument("--module", required=True)
    p.add_argument("--text", required=True)
    p.add_argument("--apply", action="store_true")
    p.set_defaults(func=add_note)

    p = sub.add_parser("registry")
    p.add_argument("--handlers-dir", default=str(DEFAULT_HANDLERS_DIR))
    p.add_argument("--registry", default=str(DEFAULT_REGISTRY))
    p.add_argument("--apply", action="store_true")
    p.set_defaults(func=rebuild_registry)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
