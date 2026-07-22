#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
stop_live_services_bot.py

Safely stops ONLY OSBB Live Services Sandbox bot processes:
  run_bot_live_services_sandbox_v1.py

Default is DRY RUN.
Use --apply to stop them.

PowerShell from G:\Programming\Py:
  python .\OSBB\tools\stop_live_services_bot.py
  python .\OSBB\tools\stop_live_services_bot.py --apply
"""

from __future__ import annotations

import argparse
import json
import subprocess


TARGET = "run_bot_live_services_sandbox_v1.py"


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
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="Actually stop matching bot processes.")
    args = ap.parse_args()

    procs = find_targets()

    print("OSBB Live Services bot stopper")
    print("Mode:", "APPLY" if args.apply else "DRY RUN")
    print("Target:", TARGET)
    print("Found:", len(procs))
    print("")

    if not procs:
        print("No running Live Services bot process found.")
        return 0

    for p in procs:
        print(f"PID : {p.get('ProcessId')}")
        print(f"Name: {p.get('Name')}")
        print("Cmd :")
        print(p.get("CommandLine"))
        print("-" * 80)

    if not args.apply:
        print("")
        print("DRY RUN ONLY. To stop these processes:")
        print("python .\\OSBB\\tools\\stop_live_services_bot.py --apply")
        return 0

    for p in procs:
        pid = int(p["ProcessId"])
        ps(f"Stop-Process -Id {pid} -Force")
        print(f"Stopped PID {pid}")

    print("")
    print("STOP COMPLETED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
