#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
trace_phone_policy_path.py

OSBB phone policy path tracer - READ ONLY.

Purpose:
  Find where Telegram phone-access flow bypasses or uses Business Policy Layer.

It does not execute bot/business logic.
It inspects source files and reports:
  - where create_phone_barrier_access_interest() is called;
  - whether ensure_service_order_allowed() is imported/called near that path;
  - whether ServiceAccessDenied is handled near phone flow;
  - whether legacy parking_debt_check_mode is used;
  - likely integration gap and next patch point.

PowerShell:

  python .\OSBB\tools\trace_phone_policy_path.py

Optional:

  python .\OSBB\tools\trace_phone_policy_path.py --out "G:\Programming\Py\OSBB\Data\exports\code\phone_policy_path_trace.txt"
"""

from __future__ import annotations

import argparse
import ast
import re
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

FILES = [
    "Bots/handlers/service_orders_workspace.py",
    "phone_barrier_access_service.py",
    "phone_barrier_access_core.py",
    "service_access_policy.py",
    "service_orders_core.py",
    "service_preorders_core.py",
]

KEYS = [
    "create_phone_barrier_access_interest",
    "ensure_service_order_allowed",
    "ServiceAccessDenied",
    "result_to_short_text",
    "parking_debt_check_mode",
    "CHECK_LINKED_PARKING_ACCOUNT",
    "MANUAL_REVIEW",
    "PHONE_ACCESS_CONNECT",
    "BARRIER_PHONE_CONNECT",
    "01_BarrierPhoneConnect",
]


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def section(out: list[str], title: str) -> None:
    out.append("")
    out.append("=" * 100)
    out.append(title)
    out.append("=" * 100)


def grep_context(path: Path, pattern: str, radius: int = 4) -> list[str]:
    if not path.exists():
        return [f"MISSING FILE: {path}"]
    lines = read(path).splitlines()
    out: list[str] = []
    rx = re.compile(pattern)
    for idx, line in enumerate(lines, start=1):
        if rx.search(line):
            a = max(1, idx - radius)
            b = min(len(lines), idx + radius)
            out.append("-" * 80)
            for i in range(a, b + 1):
                marker = ">" if i == idx else " "
                out.append(f"{marker} {i}: {lines[i-1]}")
    return out or [f" - no hits for {pattern}"]


def find_functions_containing(path: Path, needle: str) -> list[tuple[str, int, int, str]]:
    if not path.exists():
        return []
    source = read(path)
    tree = ast.parse(source)
    lines = source.splitlines()
    results = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            start = node.lineno
            end = getattr(node, "end_lineno", start)
            body_lines = lines[start - 1:end]
            body = "\n".join(body_lines)
            if needle in body:
                numbered = "\n".join(f"{i+1}: {lines[i]}" for i in range(start - 1, end))
                results.append((node.name, start, end, numbered))
    return sorted(results, key=lambda x: x[1])


def summarize_file(path: Path) -> dict[str, bool]:
    data = read(path) if path.exists() else ""
    return {key: key in data for key in KEYS}


def build_report() -> str:
    out: list[str] = []
    out.append("=" * 100)
    out.append("OSBB phone policy path trace - READ ONLY")
    out.append("=" * 100)
    out.append(f"Generated: {datetime.now():%Y-%m-%d %H:%M:%S}")
    out.append(f"Root: {ROOT}")

    section(out, "1. SOURCE FILE KEYWORD SUMMARY")
    for rel in FILES:
        path = ROOT / rel
        out.append("")
        out.append(rel)
        if not path.exists():
            out.append(" - MISSING")
            continue
        summary = summarize_file(path)
        for key in KEYS:
            out.append(f" - {key:<38}: {'YES' if summary[key] else 'no'}")

    section(out, "2. TELEGRAM / WORKSPACE CALL SITES")
    workspace = ROOT / "Bots/handlers/service_orders_workspace.py"
    out.append("")
    out.append("create_phone_barrier_access_interest call contexts:")
    out.extend(grep_context(workspace, r"\bcreate_phone_barrier_access_interest\s*\(", radius=8))
    out.append("")
    out.append("ensure_service_order_allowed call contexts in workspace:")
    out.extend(grep_context(workspace, r"\bensure_service_order_allowed\s*\(", radius=8))
    out.append("")
    out.append("ServiceAccessDenied handling contexts in workspace:")
    out.extend(grep_context(workspace, r"\bServiceAccessDenied\b", radius=6))

    section(out, "3. FUNCTIONS CONTAINING PHONE INTEREST CREATION")
    phone_call_funcs = find_functions_containing(workspace, "create_phone_barrier_access_interest")
    if not phone_call_funcs:
        out.append(" - no workspace function contains create_phone_barrier_access_interest")
    else:
        for name, start, end, body in phone_call_funcs:
            out.append("")
            out.append(f"Function {name} lines {start}-{end}")
            has_policy = "ensure_service_order_allowed" in body
            has_denied = "ServiceAccessDenied" in body
            has_mode = "parking_debt_check_mode" in body
            out.append(f" - contains ensure_service_order_allowed: {'YES' if has_policy else 'NO'}")
            out.append(f" - contains ServiceAccessDenied: {'YES' if has_denied else 'NO'}")
            out.append(f" - contains parking_debt_check_mode: {'YES' if has_mode else 'NO'}")
            out.append("")
            out.append(body)

    section(out, "4. PHONE SERVICE FUNCTION POLICY CHECK")
    service_file = ROOT / "phone_barrier_access_service.py"
    out.append("")
    out.append("create_phone_barrier_access_interest function:")
    funcs = find_functions_containing(service_file, "def create_phone_barrier_access_interest")
    if not funcs:
        # fallback by function name extraction
        try:
            source = read(service_file)
            tree = ast.parse(source)
            lines = source.splitlines()
            found = False
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef) and node.name == "create_phone_barrier_access_interest":
                    found = True
                    start = node.lineno
                    end = getattr(node, "end_lineno", start)
                    body = "\n".join(f"{i+1}: {lines[i]}" for i in range(start-1, end))
                    out.append(f"Lines {start}-{end}")
                    out.append(f" - contains ensure_service_order_allowed: {'YES' if 'ensure_service_order_allowed' in body else 'NO'}")
                    out.append(f" - contains ServiceAccessDenied: {'YES' if 'ServiceAccessDenied' in body else 'NO'}")
                    out.append(f" - contains parking_debt_check_mode: {'YES' if 'parking_debt_check_mode' in body else 'NO'}")
                    out.append("")
                    out.append(body)
            if not found:
                out.append(" - function not found")
        except Exception as exc:
            out.append(f" - ERROR: {exc}")

    section(out, "5. SERVICE ORDERS CORE POLICY ANCHOR")
    core = ROOT / "service_orders_core.py"
    out.append("")
    out.append("ensure_service_order_allowed contexts in service_orders_core:")
    out.extend(grep_context(core, r"\bensure_service_order_allowed\s*\(", radius=8))

    section(out, "6. DIAGNOSIS")
    workspace_data = read(workspace) if workspace.exists() else ""
    service_data = read(service_file) if service_file.exists() else ""

    phone_path_uses_interest = "create_phone_barrier_access_interest" in workspace_data
    workspace_policy_call = "ensure_service_order_allowed(" in workspace_data
    service_policy_call = "ensure_service_order_allowed(" in service_data

    if phone_path_uses_interest and not service_policy_call:
        out.append("WARN phone-access interest creation is not protected inside phone_barrier_access_service.py.")
        out.append("     If Telegram calls create_phone_barrier_access_interest() directly, Business Policy can be bypassed before payment/order creation.")
    if workspace_policy_call:
        out.append("OK workspace imports/calls ensure_service_order_allowed somewhere.")
        out.append("NOTE Need to verify whether that call is in the resident phone-interest path, not only operator/order path.")
    else:
        out.append("WARN workspace does not call ensure_service_order_allowed directly.")
    if not service_policy_call:
        out.append("RECOMMENDATION: add policy check before create_service_interest() inside create_phone_barrier_access_interest(),")
        out.append("                or add it in the exact Telegram handler branch immediately before that function is called.")
    else:
        out.append("OK phone service module appears to call Business Policy.")

    out.append("")
    out.append("Expected minimal patch point for fastest launch:")
    out.append(" - phone_barrier_access_service.create_phone_barrier_access_interest()")
    out.append(" - after phone/points normalization and before create_service_interest()")
    out.append(" - call ensure_service_order_allowed(conn=conn, apartment_number=apartment_number, service_item_code=service_item_code)")
    out.append(" - let ServiceAccessDenied bubble to existing Telegram UX handler if present")
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
