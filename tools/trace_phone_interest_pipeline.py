#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
trace_phone_interest_pipeline.py

OSBB phone interest pipeline tracer - READ ONLY.

It does NOT execute create_phone_barrier_access_interest().
It inspects source files and prints function bodies and SQL/commit signals.

Pipeline:
  create_phone_barrier_access_interest()
  create_service_interest()
  create_phone_access_request_from_interest()
  _request_row()
  phone_access_request_summary()
"""

from __future__ import annotations

import argparse
import ast
import re
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

TARGETS = [
    ("phone_barrier_access_service.py", "create_phone_barrier_access_interest"),
    ("service_preorders_core.py", "create_service_interest"),
    ("phone_barrier_access_core.py", "create_phone_access_request_from_interest"),
    ("phone_barrier_access_core.py", "_request_row"),
    ("phone_barrier_access_core.py", "phone_access_request_summary"),
]

SIGNAL_PATTERNS = [
    "INSERT",
    "UPDATE",
    "DELETE",
    "SELECT",
    "lastrowid",
    "commit",
    "rollback",
    "get_conn",
    "conn =",
    "conn=",
    "execute",
    "executemany",
    "phone_access_requests",
    "service_interests",
    "service_orders",
]


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def find_function_source(path: Path, func_name: str) -> tuple[int, int, str]:
    text = read(path)
    tree = ast.parse(text)
    lines = text.splitlines()

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
            start = node.lineno
            end = getattr(node, "end_lineno", start)
            body = "\n".join(f"{i+1}: {lines[i]}" for i in range(start - 1, end) if i < len(lines))
            return start, end, body

    raise RuntimeError(f"Function not found: {func_name} in {path}")


def extract_signals(function_body: str) -> list[str]:
    signals = []
    for line in function_body.splitlines():
        upper = line.upper()
        if any(p.upper() in upper for p in SIGNAL_PATTERNS):
            signals.append(line)
    return signals


def find_call_sites(path: Path, func_name: str) -> list[str]:
    text = read(path)
    lines = text.splitlines()
    results = []
    pattern = re.compile(r"\b" + re.escape(func_name) + r"\s*\(")

    for idx, line in enumerate(lines, start=1):
        if pattern.search(line) and not re.match(r"\s*def\s+" + re.escape(func_name) + r"\b", line):
            a = max(1, idx - 3)
            b = min(len(lines), idx + 3)
            results.append("\n".join(f"{i}: {lines[i-1]}" for i in range(a, b + 1)))
    return results


def build_report() -> str:
    out = []
    out.append("=" * 100)
    out.append("OSBB phone interest pipeline trace - READ ONLY")
    out.append("=" * 100)
    out.append(f"Generated: {datetime.now():%Y-%m-%d %H:%M:%S}")
    out.append(f"Root: {ROOT}")
    out.append("")

    for rel, func in TARGETS:
        path = ROOT / rel
        out.append("")
        out.append("=" * 100)
        out.append(f"{rel} :: {func}()")
        out.append("=" * 100)

        if not path.exists():
            out.append(f"MISSING FILE: {path}")
            continue

        try:
            start, end, body = find_function_source(path, func)
        except Exception as exc:
            out.append(f"ERROR: {exc}")
            continue

        out.append(f"Lines: {start}-{end}")
        out.append("")
        out.append("SIGNALS")
        signals = extract_signals(body)
        out.extend(signals if signals else [" - no SQL/connection signals found"])
        out.append("")
        out.append("FULL FUNCTION")
        out.append(body)

    out.append("")
    out.append("=" * 100)
    out.append("CALL SITES")
    out.append("=" * 100)
    for rel, func in TARGETS:
        path = ROOT / rel
        if not path.exists():
            continue
        out.append("")
        out.append(f"{func} call sites in {rel}:")
        sites = find_call_sites(path, func)
        if sites:
            for site in sites:
                out.append("-" * 80)
                out.append(site)
        else:
            out.append(" - none inside same file")

    out.append("")
    out.append("READ ONLY COMPLETED")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="", help="Optional output TXT file.")
    args = ap.parse_args()

    report = build_report()
    print(report)

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(report, encoding="utf-8")
        print("")
        print("Output:", out_path)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
