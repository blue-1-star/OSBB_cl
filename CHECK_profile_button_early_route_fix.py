# -*- coding: utf-8 -*-
"""
Read-only test of the in-memory bot routing for the profile button.

It does not start Telegram polling and does not change SQLite data.
"""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    print("OSBB profile button early-route check")
    print("Read-only in-memory routing check.")
    print()

    launcher = ROOT / "run_bot_live_services_sandbox_v1.py"
    if not launcher.is_file():
        raise FileNotFoundError(f"Launcher missing:\n{launcher}")

    import run_bot_live_services_sandbox_v1 as runner

    source, changes = runner.patch_bot_source()

    direct_marker = "# Прямая кнопка перевірки даних"
    direct_index = source.find(direct_marker)
    legacy_state_index = source.find(
        "    # =========================\n"
        "    # Состояния пользователя\n"
    )
    profile_call_index = source.find("await show_profile_verification(")
    portal_index = source.find("if await handle_client_portal_text(")

    if min(direct_index, legacy_state_index, profile_call_index, portal_index) < 0:
        raise RuntimeError("Required runtime routing markers were not found.")
    if not (direct_index < legacy_state_index < portal_index):
        raise RuntimeError(
            "Profile button is not placed before legacy state router/client portal."
        )

    print("Runtime changes:")
    for item in changes:
        print(" -", item)
    print()
    print("Result:")
    print(" - direct profile button is before legacy states")
    print(" - direct profile button is before client portal")
    print(" - stale service/cashier state cannot intercept it")
    print("CHECK COMPLETED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
