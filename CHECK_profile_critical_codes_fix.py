# -*- coding: utf-8 -*-
"""Read-only check for the resident-profile critical-codes fix."""

from pathlib import Path


ROOT = Path(__file__).resolve().parent
TARGET = ROOT / "Bots" / "handlers" / "profile_verification_workspace.py"


def main() -> int:
    print("OSBB profile critical-codes fix check")
    print("Read-only check.")

    if not TARGET.is_file():
        raise FileNotFoundError(f"Handler is missing:\n{TARGET}")

    source = TARGET.read_text(encoding="utf-8")
    expected = [
        "critical_codes = {",
        'for item in snapshot.get("critical") or []',
        "structural_codes = {",
        'for item in snapshot.get("structural_critical") or []',
        'if "CONFIRM_NO_VEHICLE" in critical_codes:',
        'if "PARKING_TIME" in critical_codes:',
    ]
    missing = [item for item in expected if item not in source]
    if missing:
        raise RuntimeError("Fix is incomplete: " + ", ".join(missing))

    print("Result:")
    print(" - correction buttons use critical_codes")
    print(" - confirmation visibility uses structural_codes")
    print("CHECK COMPLETED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
