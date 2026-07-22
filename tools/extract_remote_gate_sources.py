#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OSBB Remote/Pult Gate Source Extractor - READ ONLY

Извлекает точные тела функций, в которых нужно проверить/вставить debt-gate:
- создание заявки на пульт через remote_requests;
- выдача пульта охраной;
- современный service_orders flow для справки.

Ничего не меняет в исходниках и БД.
Пишет только TXT-отчет в:
OSBB/Data/exports/code_passport/remote_gate_sources_<timestamp>.txt
"""

from __future__ import annotations

import ast
from datetime import datetime
from pathlib import Path
import re
import sys
import warnings


TARGETS = {
    "Bots/handlers/client_portal.py": [
        "_billing_data",
        "_create_remote_request",
        "_update_remote_status",
        "_show_my_remote_requests",
    ],
    "Bots/handlers/client_portal_safe_linking.py": [
        "_billing_data",
        "_create_remote_request",
        "_update_remote_status",
        "_show_my_remote_requests",
    ],
    "Bots/handlers/guard_workspace.py": [
        "_remote_rows_for_issue",
        "_show_remote_issue_card",
        "_save_remote_issued",
        "handle_guard_workspace_text",
    ],
    "Bots/handlers/service_orders_workspace.py": [
        "_show_preview",
        "_create_interest_from_state",
        "_handle_resident",
        "_show_operator_order",
        "_handle_operator",
        "handle_service_orders_text",
    ],
    "service_preorders_core.py": [
        "create_service_interest",
        "attach_payment_notice_to_interest",
        "reconcile_paid_service_interests",
        "issue_new_remotes_from_batch",
    ],
    "service_orders_core.py": [
        "create_service_order",
        "required_tables",
        "_derive_status",
    ],
    "run_bot_guard_sandbox_v3.py": [],
    "Bots/parking_bot.py": [],
}


SEARCH_PATTERNS = [
    "client_portal",
    "client_portal_v2",
    "client_portal_v3",
    "client_portal_safe_linking",
    "service_orders_workspace",
    "guard_workspace",
    "remote_requests",
    "Пульт",
    "пульт",
    "NEW_REMOTE_PROFILE",
    "handle_service_orders_text",
    "handle_guard_workspace_text",
]


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


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        try:
            return path.read_text(encoding="cp1251")
        except UnicodeDecodeError:
            return path.read_text(encoding="utf-8", errors="replace")


def find_functions(source: str, filename: str) -> dict[str, tuple[int, int]]:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SyntaxWarning)
        tree = ast.parse(source, filename=filename)
    result: dict[str, tuple[int, int]] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            start = int(getattr(node, "lineno", 1))
            end = int(getattr(node, "end_lineno", start))
            result[node.name] = (start, end)
    return result


def numbered_block(lines: list[str], start: int, end: int) -> list[str]:
    width = len(str(end))
    out = []
    for idx in range(start, end + 1):
        text = lines[idx - 1] if 0 <= idx - 1 < len(lines) else ""
        out.append(f"{idx:>{width}} | {text}")
    return out


def search_lines(lines: list[str], patterns: list[str], context: int = 3) -> list[str]:
    hits = []
    lowered = [(p, p.lower()) for p in patterns]
    seen_ranges: set[tuple[int, int]] = set()

    for idx, line in enumerate(lines, start=1):
        low = line.lower()
        if any(pat_low in low for _pat, pat_low in lowered):
            a = max(1, idx - context)
            b = min(len(lines), idx + context)
            if (a, b) in seen_ranges:
                continue
            seen_ranges.add((a, b))
            hits.append(f"--- lines {a}-{b} ---")
            hits.extend(numbered_block(lines, a, b))
            hits.append("")
    return hits


def main() -> int:
    root = project_root_from_script()
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    out_dir = root / "Data" / "exports" / "code_passport"
    out_dir.mkdir(parents=True, exist_ok=True)
    report = out_dir / f"remote_gate_sources_{stamp}.txt"

    lines_out = []
    lines_out.append("=" * 120)
    lines_out.append("OSBB REMOTE/PULT GATE SOURCE EXTRACTOR - READ ONLY")
    lines_out.append("=" * 120)
    lines_out.append(f"Generated: {datetime.now():%Y-%m-%d %H:%M:%S}")
    lines_out.append(f"Root     : {root}")
    lines_out.append(f"Report   : {report}")
    lines_out.append("")

    for rel_path, func_names in TARGETS.items():
        path = root / rel_path
        lines_out.append("=" * 120)
        lines_out.append(f"FILE: {rel_path}")
        lines_out.append("=" * 120)

        if not path.exists():
            lines_out.append("MISSING")
            lines_out.append("")
            continue

        source = read_text(path)
        src_lines = source.splitlines()
        lines_out.append(f"Lines: {len(src_lines)}")
        lines_out.append("")

        try:
            functions = find_functions(source, str(path))
        except Exception as exc:
            functions = {}
            lines_out.append(f"AST parse error: {type(exc).__name__}: {exc}")
            lines_out.append("")

        if func_names:
            for name in func_names:
                lines_out.append("-" * 120)
                lines_out.append(f"FUNCTION: {name}")
                lines_out.append("-" * 120)
                if name not in functions:
                    lines_out.append("NOT FOUND")
                    lines_out.append("")
                    continue
                start, end = functions[name]
                lines_out.extend(numbered_block(src_lines, start, end))
                lines_out.append("")
        else:
            lines_out.append("SEARCH SNIPPETS")
            lines_out.append("-" * 120)
            snippets = search_lines(src_lines, SEARCH_PATTERNS, context=4)
            lines_out.extend(snippets or ["No search snippets found."])
            lines_out.append("")

    report.write_text("\n".join(lines_out), encoding="utf-8")

    print("OSBB Remote/Pult Gate Source Extractor - READ ONLY")
    print(f"Root: {root}")
    print(f"Report: {report}")
    print("READ ONLY COMPLETED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
