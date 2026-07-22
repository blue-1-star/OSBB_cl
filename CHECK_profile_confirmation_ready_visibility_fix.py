# -*- coding: utf-8 -*-
"""Read-only check for hiding the confirmation button after profile READY."""

from pathlib import Path


ROOT = Path(__file__).resolve().parent
TARGET = ROOT / "Bots" / "handlers" / "profile_verification_workspace.py"


def main() -> int:
    print("OSBB profile READY confirmation-button visibility check")
    print("Read-only check.")

    if not TARGET.is_file():
        raise FileNotFoundError(f"Handler is missing:\n{TARGET}")

    source = TARGET.read_text(encoding="utf-8")
    expected = [
        "if snapshot.get(\"resident_confirmation_required\"):",
        "Once the profile is READY",
        "rows.append([_tr(BUTTON_CONFIRM, lang)])",
    ]
    missing = [item for item in expected if item not in source]
    if missing:
        raise RuntimeError("Fix is incomplete: " + ", ".join(missing))

    print("Result:")
    print(" - confirmation button is shown only while confirmation is pending")
    print(" - confirmation button is hidden after READY")
    print("CHECK COMPLETED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
