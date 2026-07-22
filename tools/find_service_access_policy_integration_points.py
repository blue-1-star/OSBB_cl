#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
find_service_access_policy_integration_points.py

READ ONLY.

Ищет места для подключения service_access_policy.py:
- service_orders_workspace
- service_preorders_core
- guard_workspace
- client_portal
- phone access / remote flows
- TEST_REMOTE_NEW / TEST_REMOTE_REPROGRAM_OWN / TEST_PHONE_ACCESS_CONNECT
- create_service_interest / attach_payment_notice / issue

Ничего не меняет.
Пишет отчёт в:
  Data/exports/code/service_access_policy_integration_points_*.txt

PowerShell:
  python .\OSBB\tools\find_service_access_policy_integration_points.py
"""

from __future__ import annotations

import ast
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


TARGET_FILES = [
    "Bots/handlers/service_orders_workspace.py",
    "Bots/handlers/guard_workspace.py",
    "Bots/handlers/client_portal.py",
    "Bots/handlers/client_portal_v3.py",
    "Bots/handlers/client_portal_safe_linking.py",
    "service_preorders_core.py",
    "service_orders_core.py",
    "access_control.py",
    "run_bot_live_services_sandbox_v1.py",
    "run_bot_guard_sandbox_v3.py",
]

PATTERNS = [
    "TEST_REMOTE_NEW",
    "TEST_REMOTE_REFURBISHED",
    "TEST_REMOTE_REPROGRAM_OWN",
    "TEST_PHONE_ACCESS_CONNECT",
    "BARRIER_PHONE_CONNECT",
    "BARRIER_PHONE",
    "NEW_REMOTE_PROFILE",
    "REMOTE_REPROGRAM_OWN",
    "PHONE_ACCESS_CONNECT",
    "create_service_interest",
    "attach_payment_notice_to_interest",
    "issue_new_remotes_from_batch",
    "remote_requests",
    "remote_request",
    "service_catalog",
    "service_item",
    "access_policy",
    "debt",
    "parking_debt",
    "parking_debt_check_mode",
    "Пульт",
    "пульт",
    "телефон",
    "Телефон",
]

WRITE_PATTERNS = [
    "INSERT INTO",
    "UPDATE ",
    "conn.commit",
    "commit()",
    "create_service_interest",
    "attach_payment_notice_to_interest",
    "issue_new_remotes_from_batch",
    "_create_remote_request",
    "_save_remote_issued",
]

FUNCTION_NAME_HINTS = [
    "show",
    "preview",
    "create",
    "interest",
    "order",
    "remote",
    "issue",
    "phone",
    "access",
    "save",
    "handle",
]


@dataclass
class Hit:
    path: str
    line: int
    kind: str
    function: str
    text: str


def root_from_script() -> Path:
    here = Path(__file__).resolve()
    if here.parent.name.lower() == "tools":
        return here.parent.parent
    cwd = Path.cwd().resolve()
    if (cwd / "OSBB").exists():
        return cwd / "OSBB"
    return cwd


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        try:
            return path.read_text(encoding="cp1251")
        except UnicodeDecodeError:
            return path.read_text(encoding="utf-8", errors="replace")


def rel(path: Path, root: Path) -> str:
    return str(path.relative_to(root)).replace("\\", "/")


def line_to_function_map(source: str, filename: str) -> dict[int, str]:
    result: dict[int, str] = {}
    try:
        tree = ast.parse(source, filename=filename)
    except Exception:
        return result

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            start = int(getattr(node, "lineno", 1))
            end = int(getattr(node, "end_lineno", start))
            for i in range(start, end + 1):
                result[i] = node.name
    return result


def grep_file(path: Path, root: Path) -> tuple[list[Hit], list[str]]:
    source = read_text(path)
    fmap = line_to_function_map(source, str(path))
    hits: list[Hit] = []
    pattern_re = re.compile("|".join(re.escape(p) for p in PATTERNS), re.IGNORECASE)
    write_re = re.compile("|".join(re.escape(p) for p in WRITE_PATTERNS), re.IGNORECASE)

    for i, line in enumerate(source.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue

        kind = ""
        if pattern_re.search(line):
            kind = "MATCH"
        if write_re.search(line) and pattern_re.search(source[max(0, source.find(line)-1000):source.find(line)+1000]):
            kind = "WRITE_NEAR_FLOW" if not kind else kind + "+WRITE_NEAR_FLOW"
        if kind:
            hits.append(Hit(rel(path, root), i, kind, fmap.get(i, ""), stripped[:500]))

    functions = []
    try:
        tree = ast.parse(source, filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = node.name
                if any(h in name.lower() for h in FUNCTION_NAME_HINTS):
                    start = int(node.lineno)
                    end = int(getattr(node, "end_lineno", start))
                    segment = "\n".join(source.splitlines()[start-1:end])
                    if pattern_re.search(segment) or write_re.search(segment):
                        functions.append(f"{rel(path, root)}:{start}-{end} {name}")
    except Exception:
        pass

    return hits, functions


def main() -> int:
    root = root_from_script()
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    out_dir = root / "Data" / "exports" / "code"
    out_dir.mkdir(parents=True, exist_ok=True)
    report = out_dir / f"service_access_policy_integration_points_{stamp}.txt"

    all_hits: list[Hit] = []
    all_functions: list[str] = []
    missing: list[str] = []

    for rp in TARGET_FILES:
        path = root / rp
        if not path.exists():
            missing.append(rp)
            continue
        hits, funcs = grep_file(path, root)
        all_hits.extend(hits)
        all_functions.extend(funcs)

    lines = []
    lines.append("OSBB service_access_policy integration point search - READ ONLY")
    lines.append(f"Generated: {datetime.now():%Y-%m-%d %H:%M:%S}")
    lines.append(f"Root: {root}")
    lines.append("")
    lines.append(f"Target files: {len(TARGET_FILES)}")
    lines.append(f"Missing target files: {len(missing)}")
    for m in missing:
        lines.append(f" - MISSING {m}")
    lines.append("")
    lines.append(f"Candidate functions: {len(all_functions)}")
    for f in all_functions:
        lines.append(f" - {f}")
    lines.append("")
    lines.append(f"Line hits: {len(all_hits)}")
    lines.append("")

    current = ""
    for h in all_hits:
        if h.path != current:
            current = h.path
            lines.append("=" * 120)
            lines.append(current)
            lines.append("=" * 120)
        fn = f" [{h.function}]" if h.function else ""
        lines.append(f"{h.line}: {h.kind}{fn}: {h.text}")

    report.write_text("\n".join(lines), encoding="utf-8")

    print("OSBB service_access_policy integration point search - READ ONLY")
    print(f"Root: {root}")
    print(f"Report: {report}")
    print(f"Candidate functions: {len(all_functions)}")
    print(f"Line hits: {len(all_hits)}")
    print("READ ONLY COMPLETED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
