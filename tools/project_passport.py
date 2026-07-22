#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OSBB Project Passport - READ ONLY

Создает первичный "паспорт кода проекта":
- дерево файлов;
- реестр Python-модулей;
- функции и классы;
- импорты;
- упоминания таблиц БД;
- TODO/FIXME/HACK;
- markdown + csv + json отчеты.

Ничего не изменяет в проекте и БД.
Пишет только в OSBB/Data/exports/code_passport/<timestamp>/.
"""

from __future__ import annotations

import ast
import csv
import json
import os
import re
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Any


EXCLUDED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".idea",
    ".vscode",
    "venv",
    ".venv",
    "env",
    ".env",
    "node_modules",
    "build",
    "dist",
    ".tox",
}

EXCLUDED_FILE_SUFFIXES = {
    ".pyc",
    ".pyo",
    ".pyd",
    ".dll",
    ".exe",
    ".db",
    ".sqlite",
    ".sqlite3",
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".ico",
    ".pdf",
    ".zip",
    ".7z",
    ".rar",
    ".xlsx",
    ".xls",
    ".docx",
    ".pptx",
}

TEXT_SUFFIXES = {
    ".py",
    ".txt",
    ".md",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
    ".bat",
    ".ps1",
    ".sql",
    ".csv",
}

DB_TABLE_HINTS = {
    "apartments",
    "vehicles",
    "persons",
    "resident_accounts",
    "contact_methods",
    "charges",
    "payments",
    "payment_allocations",
    "cashboxes",
    "cashbox_operations",
    "cashier_batches",
    "cashier_batch_items",
    "cashier_receipts",
    "cashier_reconciliation_cases",
    "service_catalog",
    "service_items",
    "service_tariffs",
    "operator_audit_log",
    "audit_log",
    "parking_time_review_tasks",
    "remote_requests",
    "remote_handover_events",
    "service_orders",
    "service_order_items",
    "service_order_steps",
    "service_order_interests",
    "service_workflow_profiles",
    "service_workflow_steps",
    "remote_order_details",
    "remote_assets",
    "remote_asset_movements",
    "barrier_phone_access",
    "payment_notices",
    "bank_transactions",
    "commercial_contracts",
    "commercial_contract_items",
    "commercial_notifications",
    "unit_groups",
    "unit_group_members",
    "unit_group_aliases",
    "unit_contacts",
    "unit_aliases",
    "verification_tasks",
    "verification_candidates",
    "verification_evidence",
    "apartment_verification",
}

TODO_RE = re.compile(r"\b(TODO|FIXME|HACK|XXX|DEPRECATED|TEMP|ВРЕМЕННО|Костыль|костыль)\b", re.IGNORECASE)
SQL_TABLE_RE = re.compile(
    r"\b(?:FROM|JOIN|INTO|UPDATE|TABLE|REFERENCES)\s+[`\"']?([A-Za-z_][A-Za-z0-9_]*)[`\"']?",
    re.IGNORECASE,
)


@dataclass
class FileInfo:
    path: str
    suffix: str
    size_bytes: int
    modified_at: str
    lines: int
    kind: str


@dataclass
class ModuleInfo:
    path: str
    lines: int
    functions: int
    classes: int
    imports: int
    db_tables: str
    todos: int
    parse_error: str = ""


@dataclass
class FunctionInfo:
    path: str
    name: str
    line: int
    end_line: int
    async_func: bool
    args: str
    decorators: str
    doc: str


@dataclass
class ClassInfo:
    path: str
    name: str
    line: int
    end_line: int
    bases: str
    methods: int
    doc: str


@dataclass
class ImportInfo:
    path: str
    line: int
    kind: str
    module: str
    names: str


@dataclass
class TodoInfo:
    path: str
    line: int
    marker: str
    text: str


@dataclass
class TableRefInfo:
    path: str
    line: int
    table: str
    context: str


def project_root_from_script() -> Path:
    # Expected location: OSBB/tools/project_passport.py
    here = Path(__file__).resolve()
    if here.parent.name.lower() == "tools":
        return here.parent.parent
    # fallback: current dir if it looks like OSBB, otherwise parent
    cwd = Path.cwd().resolve()
    if (cwd / "Data").exists() or (cwd / "Bots").exists():
        return cwd
    if (cwd / "OSBB").exists():
        return cwd / "OSBB"
    return here.parent


def is_excluded(path: Path, root: Path) -> bool:
    rel_parts = path.relative_to(root).parts
    if any(part in EXCLUDED_DIRS for part in rel_parts):
        return True
    if path.is_file() and path.suffix.lower() in EXCLUDED_FILE_SUFFIXES:
        return True
    return False


def iter_files(root: Path) -> Iterable[Path]:
    for dirpath, dirnames, filenames in os.walk(root):
        dpath = Path(dirpath)
        dirnames[:] = [d for d in dirnames if d not in EXCLUDED_DIRS]
        if is_excluded(dpath, root):
            continue
        for name in filenames:
            p = dpath / name
            if not is_excluded(p, root):
                yield p


def rel(path: Path, root: Path) -> str:
    return str(path.relative_to(root)).replace("\\", "/")


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        try:
            return path.read_text(encoding="cp1251")
        except UnicodeDecodeError:
            return path.read_text(encoding="utf-8", errors="replace")


def count_lines(path: Path) -> int:
    if path.suffix.lower() not in TEXT_SUFFIXES:
        return 0
    try:
        return read_text(path).count("\n") + 1
    except Exception:
        return 0


def file_kind(path: Path) -> str:
    s = path.suffix.lower()
    if s == ".py":
        return "python"
    if s in {".md", ".txt"}:
        return "doc"
    if s in {".json", ".yaml", ".yml", ".toml", ".ini", ".cfg"}:
        return "config"
    if s in {".sql"}:
        return "sql"
    if s in {".bat", ".ps1"}:
        return "script"
    if s in {".csv"}:
        return "data_text"
    return "other"


def ast_name(node: ast.AST) -> str:
    try:
        return ast.unparse(node)
    except Exception:
        return getattr(node, "id", "") or getattr(node, "attr", "") or ""


def first_doc(node: ast.AST) -> str:
    doc = ast.get_docstring(node) or ""
    return " ".join(doc.strip().split())[:300]


def extract_imports(tree: ast.AST, path: str) -> list[ImportInfo]:
    out: list[ImportInfo] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = ", ".join(alias.name + (f" as {alias.asname}" if alias.asname else "") for alias in node.names)
            out.append(ImportInfo(path, node.lineno, "import", "", names))
        elif isinstance(node, ast.ImportFrom):
            module = "." * node.level + (node.module or "")
            names = ", ".join(alias.name + (f" as {alias.asname}" if alias.asname else "") for alias in node.names)
            out.append(ImportInfo(path, node.lineno, "from", module, names))
    return out


def extract_functions_classes(tree: ast.AST, path: str) -> tuple[list[FunctionInfo], list[ClassInfo]]:
    funcs: list[FunctionInfo] = []
    classes: list[ClassInfo] = []

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            decos = ", ".join(ast_name(d) for d in node.decorator_list)
            args = ast_name(node.args)
            funcs.append(FunctionInfo(
                path=path,
                name=node.name,
                line=node.lineno,
                end_line=getattr(node, "end_lineno", node.lineno),
                async_func=isinstance(node, ast.AsyncFunctionDef),
                args=args,
                decorators=decos,
                doc=first_doc(node),
            ))
        elif isinstance(node, ast.ClassDef):
            bases = ", ".join(ast_name(b) for b in node.bases)
            methods = sum(isinstance(x, (ast.FunctionDef, ast.AsyncFunctionDef)) for x in node.body)
            classes.append(ClassInfo(
                path=path,
                name=node.name,
                line=node.lineno,
                end_line=getattr(node, "end_lineno", node.lineno),
                bases=bases,
                methods=methods,
                doc=first_doc(node),
            ))
    return funcs, classes


def extract_todos_and_tables(path: Path, root: Path) -> tuple[list[TodoInfo], list[TableRefInfo], set[str]]:
    r = rel(path, root)
    todos: list[TodoInfo] = []
    refs: list[TableRefInfo] = []
    tables: set[str] = set()

    if path.suffix.lower() not in TEXT_SUFFIXES:
        return todos, refs, tables

    try:
        text = read_text(path)
    except Exception:
        return todos, refs, tables

    for i, line in enumerate(text.splitlines(), start=1):
        m = TODO_RE.search(line)
        if m:
            todos.append(TodoInfo(r, i, m.group(1), line.strip()[:500]))

        for tm in SQL_TABLE_RE.finditer(line):
            t = tm.group(1)
            if t:
                tables.add(t)
                refs.append(TableRefInfo(r, i, t, line.strip()[:500]))

        lower = line.lower()
        for t in DB_TABLE_HINTS:
            if t.lower() in lower:
                tables.add(t)
                if not any(x.path == r and x.line == i and x.table == t for x in refs):
                    refs.append(TableRefInfo(r, i, t, line.strip()[:500]))

    return todos, refs, tables


def write_csv(path: Path, rows: list[Any], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            if hasattr(row, "__dataclass_fields__"):
                w.writerow(asdict(row))
            else:
                w.writerow(row)


def write_md(path: Path, title: str, body: str) -> None:
    path.write_text(f"# {title}\n\n{body.rstrip()}\n", encoding="utf-8")


def main() -> int:
    root = project_root_from_script()
    now = datetime.now()
    stamp = now.strftime("%Y-%m-%d_%H-%M-%S")
    out_dir = root / "Data" / "exports" / "code_passport" / f"project_passport_{stamp}"
    inventory_dir = out_dir / "inventory"
    modules_dir = out_dir / "modules"
    out_dir.mkdir(parents=True, exist_ok=True)
    inventory_dir.mkdir(parents=True, exist_ok=True)
    modules_dir.mkdir(parents=True, exist_ok=True)

    files: list[FileInfo] = []
    modules: list[ModuleInfo] = []
    funcs_all: list[FunctionInfo] = []
    classes_all: list[ClassInfo] = []
    imports_all: list[ImportInfo] = []
    todos_all: list[TodoInfo] = []
    table_refs_all: list[TableRefInfo] = []

    all_paths = sorted(iter_files(root), key=lambda p: rel(p, root).lower())

    for p in all_paths:
        try:
            st = p.stat()
        except OSError:
            continue

        lines = count_lines(p)
        files.append(FileInfo(
            path=rel(p, root),
            suffix=p.suffix.lower(),
            size_bytes=st.st_size,
            modified_at=datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
            lines=lines,
            kind=file_kind(p),
        ))

        todos, table_refs, tables = extract_todos_and_tables(p, root)
        todos_all.extend(todos)
        table_refs_all.extend(table_refs)

        if p.suffix.lower() == ".py":
            parse_error = ""
            funcs: list[FunctionInfo] = []
            classes: list[ClassInfo] = []
            imports: list[ImportInfo] = []
            try:
                source = read_text(p)
                tree = ast.parse(source, filename=str(p))
                funcs, classes = extract_functions_classes(tree, rel(p, root))
                imports = extract_imports(tree, rel(p, root))
            except Exception as exc:
                parse_error = f"{type(exc).__name__}: {exc}"

            funcs_all.extend(funcs)
            classes_all.extend(classes)
            imports_all.extend(imports)
            modules.append(ModuleInfo(
                path=rel(p, root),
                lines=lines,
                functions=len(funcs),
                classes=len(classes),
                imports=len(imports),
                db_tables=", ".join(sorted(tables)),
                todos=len(todos),
                parse_error=parse_error,
            ))

    # CSV inventory
    write_csv(inventory_dir / "files.csv", files, list(FileInfo.__dataclass_fields__.keys()))
    write_csv(inventory_dir / "modules.csv", modules, list(ModuleInfo.__dataclass_fields__.keys()))
    write_csv(inventory_dir / "functions.csv", funcs_all, list(FunctionInfo.__dataclass_fields__.keys()))
    write_csv(inventory_dir / "classes.csv", classes_all, list(ClassInfo.__dataclass_fields__.keys()))
    write_csv(inventory_dir / "imports.csv", imports_all, list(ImportInfo.__dataclass_fields__.keys()))
    write_csv(inventory_dir / "todos.csv", todos_all, list(TodoInfo.__dataclass_fields__.keys()))
    write_csv(inventory_dir / "db_table_refs.csv", table_refs_all, list(TableRefInfo.__dataclass_fields__.keys()))

    # JSON
    summary = {
        "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "root": str(root),
        "out_dir": str(out_dir),
        "files_total": len(files),
        "python_modules": len(modules),
        "functions": len(funcs_all),
        "classes": len(classes_all),
        "imports": len(imports_all),
        "todos": len(todos_all),
        "db_table_refs": len(table_refs_all),
        "lines_total": sum(f.lines for f in files),
        "python_lines_total": sum(m.lines for m in modules),
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    # Markdown main
    largest_modules = sorted(modules, key=lambda x: x.lines, reverse=True)[:25]
    most_functions = sorted(modules, key=lambda x: x.functions, reverse=True)[:25]
    parse_errors = [m for m in modules if m.parse_error]

    body = []
    body.append("## Summary\n")
    body.append(f"- Generated: {summary['generated_at']}")
    body.append(f"- Root: `{summary['root']}`")
    body.append(f"- Files: **{summary['files_total']}**")
    body.append(f"- Python modules: **{summary['python_modules']}**")
    body.append(f"- Total lines: **{summary['lines_total']}**")
    body.append(f"- Python lines: **{summary['python_lines_total']}**")
    body.append(f"- Functions: **{summary['functions']}**")
    body.append(f"- Classes: **{summary['classes']}**")
    body.append(f"- Imports: **{summary['imports']}**")
    body.append(f"- TODO/FIXME markers: **{summary['todos']}**")
    body.append(f"- DB table references: **{summary['db_table_refs']}**")
    body.append("")
    body.append("## Output files\n")
    body.append("- `inventory/files.csv`")
    body.append("- `inventory/modules.csv`")
    body.append("- `inventory/functions.csv`")
    body.append("- `inventory/classes.csv`")
    body.append("- `inventory/imports.csv`")
    body.append("- `inventory/todos.csv`")
    body.append("- `inventory/db_table_refs.csv`")
    body.append("- `01_File_System.md`")
    body.append("- `02_Modules.md`")
    body.append("- `03_Technical_Debt.md`")
    body.append("- `04_DB_Table_References.md`")
    body.append("")
    body.append("## Largest Python modules\n")
    for m in largest_modules:
        body.append(f"- `{m.path}` — {m.lines} lines, {m.functions} functions, {m.classes} classes")
    body.append("")
    body.append("## Modules with most functions\n")
    for m in most_functions:
        body.append(f"- `{m.path}` — {m.functions} functions, {m.lines} lines")
    body.append("")
    body.append("## Parse errors\n")
    if parse_errors:
        for m in parse_errors:
            body.append(f"- `{m.path}` — {m.parse_error}")
    else:
        body.append("(none)")
    write_md(out_dir / "00_Project.md", "OSBB Project Passport", "\n".join(body))

    # File system md
    by_dir: dict[str, list[FileInfo]] = {}
    for f in files:
        d = str(Path(f.path).parent).replace("\\", "/")
        by_dir.setdefault(d, []).append(f)
    fs_lines = []
    for d in sorted(by_dir):
        fs_lines.append(f"## `{d}`\n")
        fs_lines.append(f"Files: {len(by_dir[d])}\n")
        for f in sorted(by_dir[d], key=lambda x: x.path.lower())[:200]:
            fs_lines.append(f"- `{Path(f.path).name}` — {f.kind}, {f.lines} lines, {f.size_bytes} bytes")
        fs_lines.append("")
    write_md(out_dir / "01_File_System.md", "File System Inventory", "\n".join(fs_lines))

    # Modules md
    mod_lines = []
    for m in sorted(modules, key=lambda x: x.path.lower()):
        mod_lines.append(f"## `{m.path}`")
        mod_lines.append("")
        mod_lines.append(f"- Lines: {m.lines}")
        mod_lines.append(f"- Functions: {m.functions}")
        mod_lines.append(f"- Classes: {m.classes}")
        mod_lines.append(f"- Imports: {m.imports}")
        mod_lines.append(f"- DB tables: {m.db_tables or '-'}")
        mod_lines.append(f"- TODO/FIXME: {m.todos}")
        if m.parse_error:
            mod_lines.append(f"- Parse error: {m.parse_error}")
        mod_lines.append("")
    write_md(out_dir / "02_Modules.md", "Python Modules", "\n".join(mod_lines))

    # Technical debt md
    debt_lines = []
    if todos_all:
        for t in todos_all:
            debt_lines.append(f"- `{t.path}:{t.line}` **{t.marker}** — {t.text}")
    else:
        debt_lines.append("(none)")
    write_md(out_dir / "03_Technical_Debt.md", "Technical Debt Markers", "\n".join(debt_lines))

    # DB table refs md
    refs_by_table: dict[str, list[TableRefInfo]] = {}
    for r in table_refs_all:
        refs_by_table.setdefault(r.table, []).append(r)
    ref_lines = []
    for table in sorted(refs_by_table, key=str.lower):
        ref_lines.append(f"## `{table}`")
        for r in refs_by_table[table][:100]:
            ref_lines.append(f"- `{r.path}:{r.line}` — {r.context}")
        if len(refs_by_table[table]) > 100:
            ref_lines.append(f"- ... and {len(refs_by_table[table]) - 100} more")
        ref_lines.append("")
    write_md(out_dir / "04_DB_Table_References.md", "Database Table References", "\n".join(ref_lines))

    print("OSBB Project Passport - READ ONLY")
    print(f"Root: {root}")
    print(f"Output: {out_dir}")
    print(f"Files: {summary['files_total']}")
    print(f"Python modules: {summary['python_modules']}")
    print(f"Functions: {summary['functions']}")
    print(f"Classes: {summary['classes']}")
    print(f"TODO/FIXME: {summary['todos']}")
    print(f"DB table refs: {summary['db_table_refs']}")
    print("READ ONLY COMPLETED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
