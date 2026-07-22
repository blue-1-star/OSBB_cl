#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OSBB Remote / Pult Debt Gate Audit v1 - READ ONLY

Цель:
- точечно проверить существующую логику "Пульты и доступ";
- найти функции, где создаётся намерение/заявка/заказ/выдача пульта;
- определить, есть ли рядом явная проверка задолженности;
- показать конкретные места, куда вероятно нужно вставить debt-gate.

Скрипт ничего не меняет в исходниках и БД.
Он только читает .py и структуру выбранной config.py базы в read-only режиме.
Отчёты пишет в:
  OSBB/Data/exports/code_passport/remote_debt_gate_audit_<timestamp>/
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
from typing import Any, Iterable


EXCLUDED_DIR_PARTS = {
    ".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".idea", ".vscode", "venv", ".venv", "env", ".env",
    "Data", "exports", "logs", "db", "backups", "raw", "typed", "sandbox",
}

LEGACY_OR_PACKAGE_RE = re.compile(
    r"( - Copy|_copy|копия|before_|_before_|backup|bak|payload|foundation_v|"
    r"20\d{2}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}|INSTALL_|PATCH_|patch_|migrate_|"
    r"preflight|prepare_|test_payload|parking_time_test_payload)",
    re.IGNORECASE,
)

REMOTE_TEXT_RE = re.compile(
    r"(пульт|пульти|пультів|remote|handover|service_order|service_orders|"
    r"NEW_REMOTE_PROFILE|REMOTE_|ACCESS|barrier|шлагбаум|телефон)",
    re.IGNORECASE,
)

REMOTE_FLOW_RE = re.compile(
    r"(NEW_REMOTE_PROFILE|REMOTE_REPROGRAM_OWN|REMOTE_REFURBISHED_FROM_STOCK|"
    r"PHONE_ACCESS_CONNECT|create_service_interest|attach_payment_notice_to_interest|"
    r"create_paid_order|create_service_order|issue_new_remotes_from_batch|"
    r"remote_requests|remote_order_details|service_order_interests|service_orders|"
    r"READY_FOR_ISSUE|REMOTE_BATCH_ISSUED|SUPPLIER_BATCH_RECEIVED|"
    r"INSERT\s+INTO\s+remote_requests|UPDATE\s+remote_requests|"
    r"Пульт выдан|Пульт прийня|Пульт принят|new_remote|issue_new)",
    re.IGNORECASE,
)

# "payment" alone is not enough: paid-preorder is normal.
# We are looking for blocking/checking debt semantics.
DEBT_CHECK_RE = re.compile(
    r"(debt|задолж|борг|arrears|overdue|outstanding|unpaid|underpaid|"
    r"непогаш|борж|долг|борг|остаток|залишок|rest_sum|amount_due|"
    r"payment_allocations|allocated_amount_for_charge|allocated_amount|"
    r"charges\s+c|FROM\s+charges|JOIN\s+payment_allocations|"
    r"cashier_reconciliation|blocking_outstanding)",
    re.IGNORECASE,
)

WRITE_FLOW_RE = re.compile(
    r"(INSERT\s+INTO|UPDATE\s+|DELETE\s+FROM|conn\.commit|commit\(\)|"
    r"create_service_interest|attach_payment_notice_to_interest|"
    r"issue_new_remotes_from_batch|record_remote_movement|create_remote_asset)",
    re.IGNORECASE,
)

CRITICAL_NAMES_RE = re.compile(
    r"(show_quantity|show_preview|save|create|interest|remote|issue|order|"
    r"activate|phone|service|request|handle|entry)",
    re.IGNORECASE,
)


@dataclass
class FileScan:
    path: str
    lines: int
    selected_reason: str
    remote_flow_lines: int
    debt_check_lines: int
    write_flow_lines: int


@dataclass
class FunctionScan:
    path: str
    function: str
    line: int
    end_line: int
    async_func: bool
    remote_flow_hits: int
    debt_check_hits: int
    write_flow_hits: int
    category: str
    first_remote_context: str
    first_debt_context: str


@dataclass
class CandidateLine:
    path: str
    line: int
    kind: str
    context: str


@dataclass
class DBStatus:
    mode: str
    db_path: str
    db_error: str
    has_charges: bool
    has_payment_allocations: bool
    has_payments: bool
    has_remote_requests: bool
    has_service_orders: bool
    has_service_order_interests: bool
    has_remote_order_details: bool
    has_payment_notices: bool


def project_root_from_script() -> Path:
    here = Path(__file__).resolve()
    if here.parent.name.lower() == "tools":
        return here.parent.parent
    cwd = Path.cwd().resolve()
    if (cwd / "Data").exists() and (cwd / "Bots").exists():
        return cwd
    if (cwd / "OSBB").exists():
        return cwd / "OSBB"
    return here.parent


def rel(path: Path, root: Path) -> str:
    return str(path.relative_to(root)).replace("\\", "/")


def is_excluded(path: Path, root: Path) -> bool:
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        return True
    return any(part in EXCLUDED_DIR_PARTS for part in parts)


def iter_py_files(root: Path) -> Iterable[Path]:
    for dirpath, dirnames, filenames in os.walk(root):
        dpath = Path(dirpath)
        dirnames[:] = [d for d in dirnames if d not in EXCLUDED_DIR_PARTS and not is_excluded(dpath / d, root)]
        if is_excluded(dpath, root):
            continue
        for name in filenames:
            p = dpath / name
            if p.suffix.lower() == ".py" and not is_excluded(p, root):
                yield p


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        try:
            return path.read_text(encoding="cp1251")
        except UnicodeDecodeError:
            return path.read_text(encoding="utf-8", errors="replace")


def line_count(text: str) -> int:
    return text.count("\n") + 1 if text else 0


def load_db_status(root: Path) -> DBStatus:
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
        return DBStatus("unknown", "", f"Cannot import config.py: {type(exc).__name__}: {exc}", False, False, False, False, False, False, False, False)

    try:
        uri = db_path.resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        try:
            cur = conn.cursor()
            tables = {row[0] for row in cur.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            conn.close()
        return DBStatus(
            mode=mode,
            db_path=str(db_path),
            db_error="",
            has_charges="charges" in tables,
            has_payment_allocations="payment_allocations" in tables,
            has_payments="payments" in tables,
            has_remote_requests="remote_requests" in tables,
            has_service_orders="service_orders" in tables,
            has_service_order_interests="service_order_interests" in tables,
            has_remote_order_details="remote_order_details" in tables,
            has_payment_notices="payment_notices" in tables,
        )
    except Exception as exc:
        return DBStatus(mode, str(db_path), f"Cannot read DB schema: {type(exc).__name__}: {exc}", False, False, False, False, False, False, False, False)


def selected_files(root: Path) -> list[Path]:
    result: list[Path] = []

    priority = [
        "run_bot_guard_sandbox_v3.py",
        "service_orders_core.py",
        "service_preorders_core.py",
        "access_control.py",
        "Bots/handlers/service_orders_workspace.py",
        "Bots/handlers/client_portal_v3.py",
        "Bots/handlers/client_portal_v2.py",
        "Bots/handlers/client_portal.py",
        "Bots/handlers/guard_workspace.py",
        "Bots/handlers/cashier_operator.py",
        "Bots/handlers/cashier_operator_v2.py",
    ]

    seen: set[Path] = set()
    for r in priority:
        p = root / r
        if p.exists() and p.suffix.lower() == ".py":
            result.append(p)
            seen.add(p.resolve())

    for p in iter_py_files(root):
        rp = rel(p, root)
        if p.resolve() in seen:
            continue
        if LEGACY_OR_PACKAGE_RE.search(rp):
            continue
        try:
            text = read_text(p)
        except Exception:
            continue
        if REMOTE_TEXT_RE.search(rp + "\n" + text[:300000]):
            result.append(p)
            seen.add(p.resolve())

    return sorted(result, key=lambda x: rel(x, root).lower())


def extract_function_segments(path: Path, root: Path, text: str) -> list[FunctionScan]:
    scans: list[FunctionScan] = []
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", SyntaxWarning)
            tree = ast.parse(text, filename=str(path))
    except Exception:
        return scans

    lines = text.splitlines()
    r = rel(path, root)

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue

        start = int(getattr(node, "lineno", 1))
        end = int(getattr(node, "end_lineno", start))
        segment = "\n".join(lines[start - 1:end])

        remote_hits = len(REMOTE_FLOW_RE.findall(segment))
        debt_hits = len(DEBT_CHECK_RE.findall(segment))
        write_hits = len(WRITE_FLOW_RE.findall(segment))

        if not (remote_hits or debt_hits or write_hits or CRITICAL_NAMES_RE.search(node.name)):
            continue

        first_remote = ""
        first_debt = ""
        for idx, line in enumerate(lines[start - 1:end], start=start):
            if not first_remote and REMOTE_FLOW_RE.search(line):
                first_remote = f"{idx}: {line.strip()[:300]}"
            if not first_debt and DEBT_CHECK_RE.search(line):
                first_debt = f"{idx}: {line.strip()[:300]}"

        if remote_hits and debt_hits:
            category = "REMOTE_FLOW_WITH_DEBT_WORDS"
        elif remote_hits and not debt_hits:
            category = "REMOTE_FLOW_NO_DEBT_WORDS"
        elif debt_hits and not remote_hits:
            category = "DEBT_OR_PAYMENT_HELPER"
        elif write_hits:
            category = "WRITE_FLOW_OTHER"
        else:
            category = "NAME_ONLY_REVIEW"

        scans.append(FunctionScan(
            path=r,
            function=node.name,
            line=start,
            end_line=end,
            async_func=isinstance(node, ast.AsyncFunctionDef),
            remote_flow_hits=remote_hits,
            debt_check_hits=debt_hits,
            write_flow_hits=write_hits,
            category=category,
            first_remote_context=first_remote,
            first_debt_context=first_debt,
        ))

    return scans


def scan_candidate_lines(path: Path, root: Path, text: str) -> list[CandidateLine]:
    out: list[CandidateLine] = []
    r = rel(path, root)

    for i, line in enumerate(text.splitlines(), start=1):
        s = line.strip()
        if not s:
            continue
        if REMOTE_FLOW_RE.search(line):
            out.append(CandidateLine(r, i, "remote_flow", s[:500]))
        elif DEBT_CHECK_RE.search(line):
            out.append(CandidateLine(r, i, "debt_check", s[:500]))
        elif WRITE_FLOW_RE.search(line) and REMOTE_TEXT_RE.search(line):
            out.append(CandidateLine(r, i, "remote_write", s[:500]))

    return out


def write_csv(path: Path, rows: list[Any], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow(asdict(row) if hasattr(row, "__dataclass_fields__") else row)


def write_md(path: Path, title: str, lines: list[str]) -> None:
    path.write_text("# " + title + "\n\n" + "\n".join(lines).rstrip() + "\n", encoding="utf-8")


def main() -> int:
    warnings.simplefilter("ignore", SyntaxWarning)

    root = project_root_from_script()
    now = datetime.now()
    stamp = now.strftime("%Y-%m-%d_%H-%M-%S")
    out_dir = root / "Data" / "exports" / "code_passport" / f"remote_debt_gate_audit_{stamp}"
    inv = out_dir / "inventory"
    out_dir.mkdir(parents=True, exist_ok=True)
    inv.mkdir(parents=True, exist_ok=True)

    db = load_db_status(root)

    files: list[FileScan] = []
    funcs: list[FunctionScan] = []
    lines_out: list[CandidateLine] = []

    for p in selected_files(root):
        r = rel(p, root)
        text = read_text(p)
        if LEGACY_OR_PACKAGE_RE.search(r):
            reason = "priority_or_remote_match_legacy_name"
        elif r in {
            "run_bot_guard_sandbox_v3.py",
            "service_orders_core.py",
            "service_preorders_core.py",
            "access_control.py",
            "Bots/handlers/service_orders_workspace.py",
            "Bots/handlers/client_portal_v3.py",
            "Bots/handlers/guard_workspace.py",
        }:
            reason = "priority_file"
        else:
            reason = "remote_text_match"

        remote_lines = sum(1 for line in text.splitlines() if REMOTE_FLOW_RE.search(line))
        debt_lines = sum(1 for line in text.splitlines() if DEBT_CHECK_RE.search(line))
        write_lines = sum(1 for line in text.splitlines() if WRITE_FLOW_RE.search(line))

        files.append(FileScan(
            path=r,
            lines=line_count(text),
            selected_reason=reason,
            remote_flow_lines=remote_lines,
            debt_check_lines=debt_lines,
            write_flow_lines=write_lines,
        ))

        funcs.extend(extract_function_segments(p, root, text))
        lines_out.extend(scan_candidate_lines(p, root, text))

    # Sort most important first.
    funcs_sorted = sorted(
        funcs,
        key=lambda f: (
            0 if f.category == "REMOTE_FLOW_NO_DEBT_WORDS" else
            1 if f.category == "REMOTE_FLOW_WITH_DEBT_WORDS" else
            2 if f.category == "DEBT_OR_PAYMENT_HELPER" else 3,
            f.path,
            f.line,
        ),
    )

    write_csv(inv / "files_scanned.csv", files, list(FileScan.__dataclass_fields__.keys()))
    write_csv(inv / "functions_remote_debt_scan.csv", funcs_sorted, list(FunctionScan.__dataclass_fields__.keys()))
    write_csv(inv / "candidate_lines.csv", lines_out, list(CandidateLine.__dataclass_fields__.keys()))
    write_csv(inv / "db_status.csv", [db], list(DBStatus.__dataclass_fields__.keys()))

    no_debt = [f for f in funcs_sorted if f.category == "REMOTE_FLOW_NO_DEBT_WORDS"]
    with_debt = [f for f in funcs_sorted if f.category == "REMOTE_FLOW_WITH_DEBT_WORDS"]
    helpers = [f for f in funcs_sorted if f.category == "DEBT_OR_PAYMENT_HELPER"]

    # Heuristic high-priority suspects.
    suspect_name_re = re.compile(
        r"(show_quantity|show_preview|save|create|interest|new|remote|issue|order|attach|payment|activate)",
        re.IGNORECASE,
    )
    high_priority = [
        f for f in no_debt
        if suspect_name_re.search(f.function)
        or "service_orders_workspace.py" in f.path
        or "service_orders_core.py" in f.path
        or "service_preorders_core.py" in f.path
        or "guard_workspace.py" in f.path
    ]

    summary = {
        "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "root": str(root),
        "out_dir": str(out_dir),
        "db": asdict(db),
        "files_scanned": len(files),
        "functions_scanned": len(funcs_sorted),
        "remote_flow_no_debt_functions": len(no_debt),
        "remote_flow_with_debt_words_functions": len(with_debt),
        "debt_or_payment_helper_functions": len(helpers),
        "high_priority_suspects": len(high_priority),
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    md: list[str] = []
    md.append("## Summary")
    md.append("")
    md.append(f"- Generated: `{summary['generated_at']}`")
    md.append(f"- Root: `{root}`")
    md.append(f"- DB mode: **{db.mode}**")
    md.append(f"- DB path: `{db.db_path}`")
    md.append(f"- DB error: `{db.db_error or '-'}`")
    md.append(f"- has charges/payment_allocations/payments: **{db.has_charges} / {db.has_payment_allocations} / {db.has_payments}**")
    md.append(f"- has remote_requests: **{db.has_remote_requests}**")
    md.append(f"- has service_orders/service_order_interests/remote_order_details: **{db.has_service_orders} / {db.has_service_order_interests} / {db.has_remote_order_details}**")
    md.append(f"- files scanned: **{len(files)}**")
    md.append(f"- functions scanned: **{len(funcs_sorted)}**")
    md.append(f"- remote-flow functions without debt words: **{len(no_debt)}**")
    md.append(f"- remote-flow functions with debt words: **{len(with_debt)}**")
    md.append(f"- debt/payment helper functions: **{len(helpers)}**")
    md.append(f"- high-priority suspects: **{len(high_priority)}**")
    md.append("")
    md.append("## Interpretation")
    md.append("")
    md.append("Functions in `REMOTE_FLOW_NO_DEBT_WORDS` are not automatically wrong, but they are the first places to inspect before adding a blocking rule.")
    md.append("For pult ordering, the likely gate should be before creating a resident interest/order and again before physical issue.")
    md.append("")
    md.append("## High-priority suspects")
    md.append("")
    if high_priority:
        for f in high_priority[:100]:
            md.append(f"- `{f.path}:{f.line}-{f.end_line}` `{f.function}` — remote_hits={f.remote_flow_hits}, write_hits={f.write_flow_hits}")
            if f.first_remote_context:
                md.append(f"  - first remote context: `{f.first_remote_context}`")
    else:
        md.append("(none)")
    md.append("")
    md.append("## Remote flow with debt words")
    md.append("")
    if with_debt:
        for f in with_debt[:100]:
            md.append(f"- `{f.path}:{f.line}-{f.end_line}` `{f.function}` — debt_hits={f.debt_check_hits}, remote_hits={f.remote_flow_hits}")
            if f.first_debt_context:
                md.append(f"  - first debt context: `{f.first_debt_context}`")
    else:
        md.append("(none)")
    md.append("")
    md.append("## Debt/payment helper candidates")
    md.append("")
    if helpers:
        for f in helpers[:100]:
            md.append(f"- `{f.path}:{f.line}-{f.end_line}` `{f.function}` — debt_hits={f.debt_check_hits}")
            if f.first_debt_context:
                md.append(f"  - first debt context: `{f.first_debt_context}`")
    else:
        md.append("(none)")

    write_md(out_dir / "00_Remote_Debt_Gate_Audit.md", "Remote / Pult Debt Gate Audit", md)

    print("OSBB Remote / Pult Debt Gate Audit v1 - READ ONLY")
    print(f"Root: {root}")
    print(f"Output: {out_dir}")
    print(f"DB mode: {db.mode}")
    print(f"DB path: {db.db_path}")
    if db.db_error:
        print(f"DB error: {db.db_error}")
    print(f"DB has charges/payment_allocations/payments: {db.has_charges}/{db.has_payment_allocations}/{db.has_payments}")
    print(f"DB has remote_requests: {db.has_remote_requests}")
    print(f"DB has service_orders/interests/details: {db.has_service_orders}/{db.has_service_order_interests}/{db.has_remote_order_details}")
    print(f"Files scanned: {len(files)}")
    print(f"Functions scanned: {len(funcs_sorted)}")
    print(f"Remote-flow functions WITHOUT debt words: {len(no_debt)}")
    print(f"Remote-flow functions WITH debt words: {len(with_debt)}")
    print(f"Debt/payment helper functions: {len(helpers)}")
    print(f"High-priority suspects: {len(high_priority)}")
    print("READ ONLY COMPLETED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
