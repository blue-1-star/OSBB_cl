from __future__ import annotations
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
STAMP = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

BLOCK = """
## Python internal import order

Date: 2026-07-06

For OSBB Python entrypoints, internal project imports must be placed only after
the project root has been added to `sys.path`.

Correct order:

1. Python standard library imports.
2. External library imports.
3. Determine `BOT_DIR`, `OSBB_ROOT`, `PY_ROOT` or equivalent project roots.
4. Insert project roots into `sys.path`.
5. Import internal OSBB modules, for example `config`, `handlers`, `tools`.

Reason: some entrypoints are launched from subdirectories, for example `OSBB\\Bots`.
In that case `tools`, `config`, and other OSBB-level packages are not importable
until `OSBB_ROOT` is added to `sys.path`.

Patchers must preserve this rule and must not insert `tools.*` imports at the
top of `parking_bot.py` before the `sys.path` setup block.
"""

def append_once(path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    old = path.read_text(encoding="utf-8") if path.exists() else f"# {path.stem}\n"
    if "## Python internal import order" in old:
        return f"SKIP: {path}"
    path.write_text(old.rstrip() + "\n\n---\n\n" + f"<!-- Added by OSBB Documentator: {STAMP} -->\n\n" + BLOCK.strip() + "\n", encoding="utf-8")
    return f"UPDATED: {path}"

def main() -> int:
    print("=" * 90)
    print("OSBB Documentator: Python internal import order")
    print("=" * 90)
    for target in [PROJECT_ROOT/"Development_Conventions.md", PROJECT_ROOT/"tools"/"user_onboarding"/"MODULE.md"]:
        print(append_once(target))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
