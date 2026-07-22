#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OSBB Project Passport v4 - Runtime + Schema Audit
READ ONLY

Назначение:
- сузить "паспорт проекта" от общего списка файлов к реальной рабочей цепочке;
- найти строгие точки входа;
- построить примерную import-цепочку от run_bot_guard_sandbox_v3.py;
- сравнить таблицы, упоминаемые в коде, с фактической TEST/active DB из config.py;
- отдельно подсветить модули пультов / доступа / service_orders;
- найти кандидаты мест, где нужна проверка задолженности перед заказом/выдачей пульта.

Скрипт ничего не меняет в БД и исходниках.
Пишет только отчеты в:
OSBB/Data/exports/code_passport/project_passport_v4_<timestamp>/
"""

from __future__ import annotations

import ast
import csv
import json
import os
import re
import sqlite3
import sys
import warnings
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Any


EXCLUDED_DIRS = {
    ".git", ".hg", ".svn", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".idea", ".vscode", "venv", ".venv", "env", ".env", "node_modules", "build", "dist",
    ".tox", "exports", "logs", "db", "backups", "raw", "typed", "sandbox",
}

EXCLUDED_REL_PREFIXES = {
    "Data/exports", "Data/logs", "Data/db", "Data/raw", "Data/backups", "Data/typed",
}

EXCLUDED_FILE_SUFFIXES = {
    ".pyc", ".pyo", ".pyd", ".dll", ".exe", ".db", ".sqlite", ".sqlite3",
    ".jpg", ".jpeg", ".png", ".gif", ".ico", ".pdf", ".zip", ".7z", ".rar",
    ".xlsx", ".xls", ".docx", ".pptx",
}

TEXT_SUFFIXES = {
    ".py", ".txt", ".md", ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg",
    ".bat", ".ps1", ".sql", ".csv",
}

DB_TABLE_HINTS = {
    "apartments", "vehicles", "persons", "resident_accounts", "contact_methods",
    "charges", "payments", "payment_allocations", "cashboxes", "cashbox_operations",
    "cashier_batches", "cashier_batch_items", "cashier_receipts",
    "cashier_reconciliation_cases", "service_catalog", "service_items",
    "service_tariffs", "operator_audit_log", "audit_log",
    "parking_time_review_tasks", "remote_requests", "remote_handover_events",
    "service_orders", "service_order_items", "service_order_steps",
    "service_order_interests", "service_workflow_profiles", "service_workflow_steps",
    "service_supplier_batches", "service_supplier_batch_items",
    "remote_order_details", "remote_assets", "remote_asset_movements",
    "barrier_phone_access", "payment_notices", "bank_transactions",
    "commercial_contracts", "commercial_contract_items", "commercial_notifications",
    "unit_groups", "unit_group_members", "unit_group_aliases", "unit_contacts",
    "unit_aliases", "verification_tasks", "verification_candidates",
    "verification_evidence", "apartment_verification",
}

SQL_TABLE_RE = re.compile(
    r"\b(?:FROM|JOIN|INTO|UPDATE|TABLE|REFERENCES)\s+[`\"']?([A-Za-z_][A-Za-z0-9_]*)[`\"']?",
    re.IGNORECASE,
)

STRICT_ENTRY_RE = re.compile(
    r"if\s+__name__\s*==\s*['\"]__main__['\"]|Application\.builder|run_polling",
    re.IGNORECASE,
)

REMOTE_FILE_RE = re.compile(
    r"remote|пульт|пульти|пультів|service_order|service_orders|handover|access|barrier|шлагбаум|phone",
    re.IGNORECASE,
)

DEBT_RE = re.compile(
    r"debt|задолж|борг|outstanding|due|balance|остат|rest|unpaid|partial|"
    r"charges|payment_allocations|payment_notices|allocated",
    re.IGNORECASE,
)

REMOTE_CREATION_RE = re.compile(
    r"create_service_interest|create_paid|create_order|create_remote|"
    r"INSERT\s+INTO\s+remote_requests|remote_requests|service_orders|"
    r"NEW_REMOTE_PROFILE|PAYMENT_NOTICE|AWAITING_PAYMENT|READY_FOR_ISSUE|"
    r"issue_new_remotes_from_batch|REMOTE_BATCH_ISSUED|remote_saved|"
    r"service_order_interests|remote_order_details",
    re.IGNORECASE,
)

LEGACY_RE = re.compile(
    r"(\bcopy\b| - copy|_copy|копия|before_|_before_|backup|bak|20\d{2}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}|foundation_v)",
    re.IGNORECASE,
)


@dataclass
class StrictEntrypoint:
    path: str
    lines: int
    kind: str
    reasons: str


@dataclass
class RuntimeNode:
    path: str
    depth: int
    lines: int
    imports_count: int
    local_imports: str


@dataclass
class TableReference:
    table: str
    path: str
    line: int
    context: str
    exists_in_db: bool


@dataclass
class MissingTable:
    table: str
    refs: int
    files: str
    sample_context: str


@dataclass
class RemoteAccessModule:
    path: str
    lines: int
    category: str
    has_debt_words: bool
    has_creation_words: bool
    debt_lines: int
    creation_lines: int


@dataclass
class DebtGateCandidate:
    path: str
    line: int
    kind: str
    context: str


@dataclass
class ImportEdge:
    importer_path: str
    imported_module: str
    imported_name: str
    line: int
    resolved_path: str


def project_root_from_script() -> Path:
    here = Path(__file__).resolve()
    if here.parent.name.lower() == "tools":
        return here.parent.parent
    cwd = Path.cwd().resolve()
    if (cwd / "Data").exists() or (cwd / "Bots").exists():
        return cwd
    if (cwd / "OSBB").exists():
        return cwd / "OSBB"
    return here.parent


def rel(path: Path, root: Path) -> str:
    return str(path.relative_to(root)).replace("\\", "/")


def is_excluded(path: Path, root: Path) -> bool:
    try:
        r = rel(path, root)
    except ValueError:
        return True

    for prefix in EXCLUDED_REL_PREFIXES:
        if r == prefix or r.startswith(prefix + "/"):
            return True

    try:
        parts = path.relative_to(root).parts
    except ValueError:
        return True

    if any(part in EXCLUDED_DIRS for part in parts):
        return True

    if path.is_file():
        lower_name = path.name.lower()
        if path.suffix.lower() in EXCLUDED_FILE_SUFFIXES:
            return True
        if lower_name.endswith(".db-wal") or lower_name.endswith(".db-shm"):
            return True

    return False


def iter_files(root: Path) -> Iterable[Path]:
    for dirpath, dirnames, filenames in os.walk(root):
        dpath = Path(dirpath)
        dirnames[:] = [d for d in dirnames if not is_excluded(dpath / d, root)]
        if is_excluded(dpath, root):
            continue
        for name in filenames:
            p = dpath / name
            if not is_excluded(p, root):
                yield p


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


def write_csv(path: Path, rows: list[Any], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow(asdict(row) if hasattr(row, "__dataclass_fields__") else row)


def write_md(path: Path, title: str, lines: list[str]) -> None:
    path.write_text("# " + title + "\n\n" + "\n".join(lines).rstrip() + "\n", encoding="utf-8")


def module_name_from_rel(path: str) -> str:
    p = Path(path)
    if p.name == "__init__.py":
        parts = p.parent.parts
    else:
        parts = p.with_suffix("").parts
    return ".".join(parts).replace("/", ".")


def parse_imports(path: Path, root: Path) -> tuple[list[tuple[str, str, int]], str]:
    out: list[tuple[str, str, int]] = []
    try:
        source = read_text(path)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", SyntaxWarning)
            tree = ast.parse(source, filename=str(path))
    except Exception as exc:
        return out, f"{type(exc).__name__}: {exc}"

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                out.append((alias.name, alias.asname or "", node.lineno))
        elif isinstance(node, ast.ImportFrom):
            mod = "." * node.level + (node.module or "")
            for alias in node.names:
                out.append((mod, alias.name + (f" as {alias.asname}" if alias.asname else ""), node.lineno))
    return out, ""


def build_local_module_map(py_files: list[Path], root: Path) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for p in py_files:
        r = rel(p, root)
        mapping[module_name_from_rel(r)] = r
        stem = Path(r).with_suffix("").name
        mapping.setdefault(stem, r)
        # Common local package style: Bots.handlers.name
        parts = Path(r).with_suffix("").parts
        if len(parts) >= 2:
            mapping.setdefault(".".join(parts[-2:]), r)
    return mapping


def resolve_import(imported_module: str, imported_name: str, local_map: dict[str, str]) -> str:
    mod = imported_module.lstrip(".")
    name = imported_name.split(" as ")[0].strip()

    candidates = []
    if mod:
        candidates.append(mod)
        if name:
            candidates.append(mod + "." + name)
    if name:
        candidates.append(name)

    for c in candidates:
        if c in local_map:
            return local_map[c]

    # Suffix fallback.
    for c in candidates:
        for local_mod, path in local_map.items():
            if local_mod.endswith("." + c) or local_mod.split(".")[-1] == c:
                return path

    return ""


def load_config_db(root: Path) -> tuple[str, str, set[str], str]:
    py_root = root.parent
    if str(py_root) not in sys.path:
        sys.path.insert(0, str(py_root))

    try:
        import config  # type: ignore
        use_test = bool(getattr(config, "USE_TEST_DB", False))
        paths = getattr(config, "paths")
        db_path = Path(paths.OSBB_TEST_DB_FILE if use_test else paths.OSBB_DB_FILE)
        mode = "test" if use_test else "prod"
    except Exception as exc:
        return "unknown", "", set(), f"Cannot import config.py: {type(exc).__name__}: {exc}"

    try:
        # Read-only URI.
        uri = db_path.resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        try:
            cur = conn.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
            tables = {row[0] for row in cur.fetchall()}
        finally:
            conn.close()
        return mode, str(db_path), tables, ""
    except Exception as exc:
        return mode, str(db_path), set(), f"Cannot read DB schema: {type(exc).__name__}: {exc}"


def scan_table_refs(path: Path, root: Path, db_tables: set[str]) -> list[TableReference]:
    refs: list[TableReference] = []
    if path.suffix.lower() not in TEXT_SUFFIXES:
        return refs

    try:
        text = read_text(path)
    except Exception:
        return refs

    r = rel(path, root)
    seen: set[tuple[str, int]] = set()

    for i, line in enumerate(text.splitlines(), start=1):
        tables_here = set()

        for m in SQL_TABLE_RE.finditer(line):
            tables_here.add(m.group(1))

        lower = line.lower()
        for t in DB_TABLE_HINTS:
            if t.lower() in lower:
                tables_here.add(t)

        for table in sorted(tables_here):
            key = (table, i)
            if key in seen:
                continue
            seen.add(key)
            refs.append(TableReference(
                table=table,
                path=r,
                line=i,
                context=line.strip()[:500],
                exists_in_db=table in db_tables if db_tables else False,
            ))

    return refs


def strict_entrypoints(py_files: list[Path], root: Path) -> list[StrictEntrypoint]:
    result: list[StrictEntrypoint] = []

    for p in py_files:
        r = rel(p, root)
        if LEGACY_RE.search(r):
            continue

        text = ""
        try:
            text = read_text(p)
        except Exception:
            pass

        reasons = []
        ep_kind = ""

        if p.name.startswith("run_"):
            ep_kind = "run_script"
            reasons.append("filename starts with run_")
        if p.name in {"main.py", "app.py", "bot.py"}:
            ep_kind = ep_kind or "main_like"
            reasons.append("main/app/bot filename")
        if "if __name__" in text and "__main__" in text:
            ep_kind = ep_kind or "cli_main_guard"
            reasons.append("has __main__ guard")
        if "Application.builder" in text or "run_polling" in text:
            ep_kind = "telegram_runtime"
            reasons.append("has telegram Application/run_polling")

        if reasons:
            result.append(StrictEntrypoint(
                path=r,
                lines=count_lines(p),
                kind=ep_kind or "entrypoint_candidate",
                reasons="; ".join(reasons),
            ))

    return result


def runtime_import_closure(target_rel: str, root: Path, py_files: list[Path], local_map: dict[str, str]) -> tuple[list[RuntimeNode], list[ImportEdge], str]:
    path_by_rel = {rel(p, root): p for p in py_files}
    if target_rel not in path_by_rel:
        return [], [], f"Target not found: {target_rel}"

    nodes: list[RuntimeNode] = []
    edges: list[ImportEdge] = []
    visited: set[str] = set()
    queue: list[tuple[str, int]] = [(target_rel, 0)]

    while queue:
        current, depth = queue.pop(0)
        if current in visited:
            continue
        visited.add(current)

        p = path_by_rel[current]
        imports, _err = parse_imports(p, root)

        local_import_paths = []
        for mod, name, line in imports:
            resolved = resolve_import(mod, name, local_map)
            if resolved:
                local_import_paths.append(resolved)
                edges.append(ImportEdge(
                    importer_path=current,
                    imported_module=mod,
                    imported_name=name,
                    line=line,
                    resolved_path=resolved,
                ))
                if resolved not in visited and resolved in path_by_rel:
                    queue.append((resolved, depth + 1))

        nodes.append(RuntimeNode(
            path=current,
            depth=depth,
            lines=count_lines(p),
            imports_count=len(imports),
            local_imports=", ".join(sorted(set(local_import_paths))),
        ))

    return nodes, edges, ""


def remote_access_scan(py_files: list[Path], root: Path) -> tuple[list[RemoteAccessModule], list[DebtGateCandidate]]:
    modules: list[RemoteAccessModule] = []
    candidates: list[DebtGateCandidate] = []

    for p in py_files:
        r = rel(p, root)
        if LEGACY_RE.search(r):
            category = "legacy"
        elif r.startswith("Bots/handlers/"):
            category = "handler"
        elif r.startswith("tools/"):
            category = "tool"
        elif p.name.startswith("run_"):
            category = "entrypoint"
        else:
            category = "module"

        try:
            text = read_text(p)
        except Exception:
            continue

        if not REMOTE_FILE_RE.search(r + "\n" + text[:300000]):
            continue

        debt_count = 0
        creation_count = 0
        for i, line in enumerate(text.splitlines(), start=1):
            if DEBT_RE.search(line):
                debt_count += 1
                candidates.append(DebtGateCandidate(r, i, "debt_or_payment_reference", line.strip()[:500]))
            if REMOTE_CREATION_RE.search(line):
                creation_count += 1
                candidates.append(DebtGateCandidate(r, i, "remote_order_flow_reference", line.strip()[:500]))

        modules.append(RemoteAccessModule(
            path=r,
            lines=count_lines(p),
            category=category,
            has_debt_words=debt_count > 0,
            has_creation_words=creation_count > 0,
            debt_lines=debt_count,
            creation_lines=creation_count,
        ))

    return modules, candidates


def main() -> int:
    warnings.simplefilter("ignore", SyntaxWarning)

    root = project_root_from_script()
    now = datetime.now()
    stamp = now.strftime("%Y-%m-%d_%H-%M-%S")
    out_dir = root / "Data" / "exports" / "code_passport" / f"project_passport_v4_{stamp}"
    inv = out_dir / "inventory"
    out_dir.mkdir(parents=True, exist_ok=True)
    inv.mkdir(parents=True, exist_ok=True)

    all_files = sorted(iter_files(root), key=lambda p: rel(p, root).lower())
    py_files = [p for p in all_files if p.suffix.lower() == ".py"]
    local_map = build_local_module_map(py_files, root)

    mode, db_path, db_tables, db_error = load_config_db(root)

    entries = strict_entrypoints(py_files, root)

    default_target = "run_bot_guard_sandbox_v3.py"
    if not (root / default_target).exists():
        # fallback to the first run_*.py
        run_files = sorted(p for p in py_files if p.name.startswith("run_"))
        default_target = rel(run_files[0], root) if run_files else ""

    runtime_nodes: list[RuntimeNode] = []
    runtime_edges: list[ImportEdge] = []
    runtime_error = ""
    if default_target:
        runtime_nodes, runtime_edges, runtime_error = runtime_import_closure(default_target, root, py_files, local_map)
    else:
        runtime_error = "No runtime target found."

    table_refs: list[TableReference] = []
    for p in py_files:
        table_refs.extend(scan_table_refs(p, root, db_tables))

    refs_by_missing: dict[str, list[TableReference]] = {}
    if db_tables:
        for r in table_refs:
            if not r.exists_in_db:
                refs_by_missing.setdefault(r.table, []).append(r)

    missing_tables: list[MissingTable] = []
    for table, refs in refs_by_missing.items():
        files = sorted({r.path for r in refs})
        sample = refs[0].context if refs else ""
        missing_tables.append(MissingTable(
            table=table,
            refs=len(refs),
            files=", ".join(files[:20]) + (f" ... +{len(files)-20}" if len(files) > 20 else ""),
            sample_context=sample,
        ))
    missing_tables.sort(key=lambda x: (-x.refs, x.table.lower()))

    remote_modules, debt_candidates = remote_access_scan(py_files, root)

    write_csv(inv / "strict_entrypoints.csv", entries, list(StrictEntrypoint.__dataclass_fields__.keys()))
    write_csv(inv / "runtime_import_nodes.csv", runtime_nodes, list(RuntimeNode.__dataclass_fields__.keys()))
    write_csv(inv / "runtime_import_edges.csv", runtime_edges, list(ImportEdge.__dataclass_fields__.keys()))
    write_csv(inv / "db_table_refs.csv", table_refs, list(TableReference.__dataclass_fields__.keys()))
    write_csv(inv / "missing_tables_from_code.csv", missing_tables, list(MissingTable.__dataclass_fields__.keys()))
    write_csv(inv / "remote_access_modules.csv", remote_modules, list(RemoteAccessModule.__dataclass_fields__.keys()))
    write_csv(inv / "debt_gate_candidates.csv", debt_candidates, list(DebtGateCandidate.__dataclass_fields__.keys()))

    summary = {
        "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "root": str(root),
        "out_dir": str(out_dir),
        "db_mode": mode,
        "db_path": db_path,
        "db_error": db_error,
        "db_tables": len(db_tables),
        "python_files": len(py_files),
        "strict_entrypoints": len(entries),
        "runtime_target": default_target,
        "runtime_nodes": len(runtime_nodes),
        "runtime_edges": len(runtime_edges),
        "runtime_error": runtime_error,
        "table_refs": len(table_refs),
        "missing_tables": len(missing_tables),
        "remote_access_modules": len(remote_modules),
        "debt_gate_candidates": len(debt_candidates),
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "## Summary",
        "",
        f"- Generated: `{summary['generated_at']}`",
        f"- Root: `{summary['root']}`",
        f"- DB mode from config.py: **{mode}**",
        f"- DB path: `{db_path}`",
        f"- DB tables read: **{len(db_tables)}**",
        f"- DB error: `{db_error or '-'}`",
        f"- Python files scanned: **{len(py_files)}**",
        f"- Strict entrypoints: **{len(entries)}**",
        f"- Runtime target: `{default_target or '-'}`",
        f"- Runtime import nodes: **{len(runtime_nodes)}**",
        f"- Runtime import edges: **{len(runtime_edges)}**",
        f"- Runtime error: `{runtime_error or '-'}`",
        f"- Code table references: **{len(table_refs)}**",
        f"- Tables referenced by code but missing in DB: **{len(missing_tables)}**",
        f"- Remote/access/service modules: **{len(remote_modules)}**",
        f"- Debt-gate candidate lines: **{len(debt_candidates)}**",
        "",
        "## Key output files",
        "",
        "- `inventory/strict_entrypoints.csv`",
        "- `inventory/runtime_import_nodes.csv`",
        "- `inventory/runtime_import_edges.csv`",
        "- `inventory/missing_tables_from_code.csv`",
        "- `inventory/remote_access_modules.csv`",
        "- `inventory/debt_gate_candidates.csv`",
    ]
    write_md(out_dir / "00_Runtime_Schema_Audit.md", "OSBB Runtime + Schema Audit", lines)

    ep_lines = []
    for e in sorted(entries, key=lambda x: (x.kind, x.path)):
        ep_lines.append(f"- `{e.path}` — {e.kind}, {e.lines} lines; {e.reasons}")
    write_md(out_dir / "01_Strict_Entrypoints.md", "Strict Entrypoints", ep_lines or ["(none)"])

    rt_lines = []
    if runtime_error:
        rt_lines.append(f"Runtime import closure error: `{runtime_error}`")
    for n in sorted(runtime_nodes, key=lambda x: (x.depth, x.path)):
        indent = "  " * min(n.depth, 8)
        rt_lines.append(f"{indent}- `{n.path}` — depth={n.depth}, lines={n.lines}, local imports: {n.local_imports or '-'}")
    write_md(out_dir / "02_Runtime_Import_Closure.md", "Runtime Import Closure", rt_lines or ["(none)"])

    mt_lines = []
    if missing_tables:
        mt_lines.append("These are table names referenced by scanned source code but not present in the selected DB schema.")
        mt_lines.append("")
        for m in missing_tables[:200]:
            mt_lines.append(f"## `{m.table}`")
            mt_lines.append(f"- refs: {m.refs}")
            mt_lines.append(f"- files: {m.files}")
            mt_lines.append(f"- sample: `{m.sample_context}`")
            mt_lines.append("")
    else:
        mt_lines.append("(none)")
    write_md(out_dir / "03_Missing_Tables_From_Code.md", "Tables Referenced by Code but Missing in DB", mt_lines)

    rm_lines = []
    for m in sorted(remote_modules, key=lambda x: (x.category, x.path)):
        rm_lines.append(
            f"- `{m.path}` — {m.category}, lines={m.lines}, "
            f"debt_refs={m.debt_lines}, flow_refs={m.creation_lines}"
        )
    write_md(out_dir / "04_Remote_Access_Modules.md", "Remote / Access / Service Modules", rm_lines or ["(none)"])

    dg_lines = []
    for c in debt_candidates[:1000]:
        dg_lines.append(f"- `{c.path}:{c.line}` **{c.kind}** — {c.context}")
    if len(debt_candidates) > 1000:
        dg_lines.append(f"- ... and {len(debt_candidates) - 1000} more")
    write_md(out_dir / "05_Debt_Gate_Candidates.md", "Debt Gate Candidate Lines", dg_lines or ["(none)"])

    print("OSBB Project Passport v4 - Runtime + Schema Audit - READ ONLY")
    print(f"Root: {root}")
    print(f"Output: {out_dir}")
    print(f"DB mode: {mode}")
    print(f"DB path: {db_path}")
    print(f"DB tables: {len(db_tables)}")
    if db_error:
        print(f"DB error: {db_error}")
    print(f"Python files: {len(py_files)}")
    print(f"Strict entrypoints: {len(entries)}")
    print(f"Runtime target: {default_target or '-'}")
    print(f"Runtime import nodes: {len(runtime_nodes)}")
    if runtime_error:
        print(f"Runtime error: {runtime_error}")
    print(f"Code table refs: {len(table_refs)}")
    print(f"Missing tables from code: {len(missing_tables)}")
    print(f"Remote/access modules: {len(remote_modules)}")
    print(f"Debt gate candidate lines: {len(debt_candidates)}")
    print("READ ONLY COMPLETED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
