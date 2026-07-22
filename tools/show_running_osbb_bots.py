#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
show_running_osbb_bots.py

READ ONLY diagnostic.

Shows Python processes that look like OSBB bot/runtime launches:
- PID
- python executable
- command line
- likely OSBB script/bat target
- whether more than one bot-like process is running

Does not kill anything by default.

Optional:
  --kill PID
requires explicit PID and confirmation flag:
  --kill 12345 --yes

PowerShell from G:\Programming\Py:
  python .\OSBB\tools\show_running_osbb_bots.py
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


OSBB_HINTS = [
    "OSBB",
    "parking_bot.py",
    "run_bot_guard_sandbox",
    "Start_OSBB",
    "Live_Services",
    "Guard_Sandbox",
]


@dataclass
class Proc:
    pid: str
    name: str
    command_line: str


def run_powershell(command: str) -> str:
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip())
    return completed.stdout


def list_processes() -> list[Proc]:
    ps = r"""
Get-CimInstance Win32_Process |
Where-Object {
    $_.Name -match 'python|cmd|powershell' -and
    $_.CommandLine -match 'OSBB|parking_bot|run_bot_guard_sandbox|Start_OSBB|Live_Services|Guard_Sandbox'
} |
Select-Object ProcessId,Name,CommandLine |
ConvertTo-Json -Depth 3
"""
    import json
    raw = run_powershell(ps).strip()
    if not raw:
        return []
    data = json.loads(raw)
    if isinstance(data, dict):
        data = [data]
    result = []
    for item in data:
        result.append(Proc(
            pid=str(item.get("ProcessId") or ""),
            name=str(item.get("Name") or ""),
            command_line=str(item.get("CommandLine") or ""),
        ))
    return result


def classify(cmd: str) -> str:
    c = cmd.lower()
    if "run_bot_guard_sandbox_v3.py" in c:
        return "CURRENT guarded sandbox runtime"
    if "parking_bot.py" in c:
        return "direct parking_bot runtime"
    if "start_osbb_live_services_sandbox_bot_v1.bat" in c:
        return "Live Services Sandbox BAT v1"
    if "start_osbb_guard_sandbox_bot_v2.bat" in c:
        return "Guard Sandbox BAT v2"
    if "start_osbb_bot.bat" in c:
        return "old Start_OSBB_Bot BAT"
    if "python" in c and "osbb" in c:
        return "OSBB Python process"
    if "cmd" in c and "osbb" in c:
        return "OSBB launcher shell"
    return "OSBB-related process"


def kill_process(pid: str) -> None:
    if not pid.isdigit():
        raise ValueError("PID must be numeric.")
    run_powershell(f"Stop-Process -Id {int(pid)} -Force")


def main() -> int:
    parser = argparse.ArgumentParser(description="Show running OSBB bot-related processes.")
    parser.add_argument("--kill", default="", help="PID to kill. Use only after inspecting output.")
    parser.add_argument("--yes", action="store_true", help="Confirm --kill.")
    args = parser.parse_args()

    print("=" * 90)
    print("OSBB running bot/process diagnostic - READ ONLY")
    print("=" * 90)

    if args.kill:
        if not args.yes:
            print("Refused: --kill requires --yes.")
            print(f"Example: python .\\OSBB\\tools\\show_running_osbb_bots.py --kill {args.kill} --yes")
            return 2
        kill_process(args.kill)
        print(f"Killed PID: {args.kill}")
        return 0

    procs = list_processes()
    if not procs:
        print("No OSBB-related python/cmd/powershell processes found.")
        return 0

    print(f"Found OSBB-related processes: {len(procs)}")
    print("")

    bot_like = []
    for p in procs:
        kind = classify(p.command_line)
        if "runtime" in kind.lower() or "bat" in kind.lower() or "bot" in p.command_line.lower():
            bot_like.append(p)

        print("-" * 90)
        print(f"PID : {p.pid}")
        print(f"Name: {p.name}")
        print(f"Kind: {kind}")
        print("Cmd :")
        print(p.command_line)

    print("")
    print("=" * 90)
    print("Summary")
    print("=" * 90)
    print(f"OSBB-related processes: {len(procs)}")
    print(f"Bot-like processes    : {len(bot_like)}")

    if len(bot_like) > 1:
        print("")
        print("WARNING: more than one bot-like process is running.")
        print("Telegram getUpdates Conflict is expected until only one bot instance remains.")
        print("")
        print("To stop a specific PID after checking it:")
        print("python .\\OSBB\\tools\\show_running_osbb_bots.py --kill PID --yes")
    elif len(bot_like) == 1:
        print("")
        print("Looks like exactly one bot-like process is running.")
    else:
        print("")
        print("No clearly bot-like process detected among OSBB-related processes.")

    print("")
    print("READ ONLY COMPLETED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
