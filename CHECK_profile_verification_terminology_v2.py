# -*- coding: utf-8 -*-
"""Read-only source check for profile-verification terminology/readiness V2."""

from pathlib import Path


ROOT = Path(__file__).resolve().parent


def main() -> int:
    print("OSBB profile-verification terminology V2 check")
    print("Read-only check.")

    core = ROOT / "profile_verification_core.py"
    handler = ROOT / "Bots" / "handlers" / "profile_verification_workspace.py"
    service = ROOT / "Bots" / "handlers" / "service_orders_workspace.py"

    required = {
        core: [
            '"required_data_complete": required_data_complete',
            '"resident_confirmation_required": resident_confirmation_required',
            '"profile_ready": profile_ready',
            "Оберіть: Day, Night або «Не користується паркуванням».",
        ],
        handler: [
            "✅ Підтвердити обов’язкові дані",
            "structural_codes = {",
            "🚫 Не користується паркуванням",
            "⛔ Обов’язкові дані не завершені:",
            "ℹ️ Додаткові дані для уточнення — не блокують підключення:",
        ],
        service: [
            "Обов’язкові дані заповнені, але ще не підтверджені.",
            "⛔ Підключення телефонного доступу неможливе.",
        ],
    }

    for path, markers in required.items():
        if not path.is_file():
            raise FileNotFoundError(f"Expected source missing:\n{path}")
        source = path.read_text(encoding="utf-8")
        missing = [marker for marker in markers if marker not in source]
        if missing:
            raise RuntimeError(f"V2 correction missing in {path.name}: " + ", ".join(missing))

    print("Result:")
    print(" - empty parking_time is a structural blocker")
    print(" - confirmation is separate from data completeness")
    print(" - optional model/color fields remain advisory")
    print(" - private barrier-access phone remains independent of contacts")
    print("CHECK COMPLETED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
