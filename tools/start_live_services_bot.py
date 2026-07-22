#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
start_live_services_bot.py

Starts OSBB Live Services Sandbox bot only if it is not already running.

PowerShell from G:\Programming\Py:
  python .\OSBB\tools\start_live_services_bot.py
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path


TARGET = "run_bot_live_services_sandbox_v1.py"
BAT = r"G:\Programming\Py\OSBB\Start_OSBB_Live_Services_Sandbox_Bot_v1.bat"
WORKDIR = r"G:\Programming\Py\OSBB"


def ps(command: str) -> str:
    result = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return result.stdout.strip()


def find_targets() -> list[dict]:
    cmd = rf"""
Get-CimInstance Win32_Process |
Where-Object {{
    $_.Name -match 'python' -and
    $_.CommandLine -match '{TARGET.replace('.', '\.')}'
}} |
Select-Object ProcessId,Name,CommandLine |
ConvertTo-Json -Depth 3
"""
    raw = ps(cmd)
    if not raw:
        return []
    data = json.loads(raw)
    return data if isinstance(data, list) else [data]


def main() -> int:
    print("OSBB Live Services bot starter")
    print("Target:", TARGET)

    procs = find_targets()
    if procs:
        print("")
        print("Bot already running. Not starting another instance.")
        for p in procs:
            print(f" - PID {p.get('ProcessId')}: {p.get('CommandLine')}")
        return 0

    if not Path(BAT).exists():
        raise SystemExit(f"BAT not found: {BAT}")

    print("Starting:")
    print(BAT)

    ps(f'Start-Process -FilePath "{BAT}" -WorkingDirectory "{WORKDIR}"')
    print("START REQUESTED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
