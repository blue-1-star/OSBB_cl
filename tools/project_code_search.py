# project_code_search.py
# OSBB project code search - READ ONLY
# Place to: G:\\Programming\\Py\\OSBB\\tools\\project_code_search.py

from pathlib import Path
from datetime import datetime
import sys
import argparse

DEFAULT_PATTERNS = [
    "пульт", "пультів", "Поставка пультів", "Оплачені заявки",
    "Заявка оператора", "remote", "remote_requests", "SO-", "RB-",
    "новый пульт", "новий пульт", "READY", "RECEIVED",
]

SKIP_DIRS = {
    ".git", ".hg", ".svn", "__pycache__", ".pytest_cache", ".mypy_cache",
    "venv", ".venv", "env", ".env", "node_modules",
    "Data", "data", "db", "backups", "exports", "logs", "raw", "typed",
}

TEXT_EXTS = {
    ".py", ".txt", ".md", ".json", ".yaml", ".yml", ".ini", ".cfg", ".toml",
}


def find_py_root():
    here = Path(__file__).resolve()
    # Expected: G:/Programming/Py/OSBB/tools/project_code_search.py
    # OSBB root = parents[1], Py root = parents[2]
    for p in [here.parent, *here.parents]:
        if (p / "OSBB").exists() and (p / "config.py").exists():
            return p
    # fallback for tool located in OSBB/tools
    if here.parent.name.lower() == "tools":
        return here.parent.parent.parent
    return Path.cwd()


def safe_read(path):
    for enc in ("utf-8", "utf-8-sig", "cp1251"):
        try:
            return path.read_text(encoding=enc)
        except UnicodeDecodeError:
            continue
        except Exception as e:
            return None
    return None


def should_skip(path):
    parts = set(path.parts)
    return bool(parts & SKIP_DIRS)


def context_lines(lines, idx, radius):
    start = max(0, idx - radius)
    end = min(len(lines), idx + radius + 1)
    out = []
    for i in range(start, end):
        mark = ">" if i == idx else " "
        out.append(f"{mark} {i+1:5}: {lines[i]}")
    return "\n".join(out)


def main():
    parser = argparse.ArgumentParser(description="OSBB code search report - READ ONLY")
    parser.add_argument("patterns", nargs="*", help="Search words. If omitted, remote/pult patterns are used.")
    parser.add_argument("--root", default="", help="Project root. Default: auto G:/Programming/Py/OSBB")
    parser.add_argument("--context", type=int, default=2, help="Context lines around match")
    parser.add_argument("--max-matches-per-file", type=int, default=80)
    parser.add_argument("--all-text", action="store_true", help="Scan all text-like files, not only known extensions")
    args = parser.parse_args()

    py_root = find_py_root()
    osbb_root = Path(args.root).resolve() if args.root else (py_root / "OSBB")
    if not osbb_root.exists():
        print("OSBB root not found:", osbb_root)
        sys.exit(2)

    patterns = args.patterns or DEFAULT_PATTERNS
    patterns_lower = [p.lower() for p in patterns]

    export_dir = osbb_root / "Data" / "exports" / "code"
    export_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    report_file = export_dir / f"code_search_{ts}.txt"

    files_scanned = 0
    files_with_matches = 0
    total_matches = 0
    blocks = []

    for path in sorted(osbb_root.rglob("*")):
        if not path.is_file() or should_skip(path):
            continue
        if not args.all_text and path.suffix.lower() not in TEXT_EXTS:
            continue
        text = safe_read(path)
        if text is None:
            continue
        files_scanned += 1
        lines = text.splitlines()
        file_matches = []
        for idx, line in enumerate(lines):
            low = line.lower()
            matched = [p for p, pl in zip(patterns, patterns_lower) if pl in low]
            if matched:
                file_matches.append((idx, matched))
                total_matches += 1
                if len(file_matches) >= args.max_matches_per_file:
                    break
        if not file_matches:
            continue
        files_with_matches += 1
        rel = path.relative_to(osbb_root)
        blocks.append("=" * 120)
        blocks.append(f"FILE: {rel}")
        blocks.append(f"MATCHES: {len(file_matches)}")
        blocks.append("=" * 120)
        for idx, matched in file_matches:
            blocks.append(f"\nLine {idx+1} | patterns: {', '.join(matched)}")
            blocks.append("-" * 120)
            blocks.append(context_lines(lines, idx, args.context))
        blocks.append("")

    header = [
        "=" * 120,
        "OSBB PROJECT CODE SEARCH - READ ONLY",
        "=" * 120,
        f"Generated          : {datetime.now():%Y-%m-%d %H:%M:%S}",
        f"OSBB root          : {osbb_root}",
        f"Report             : {report_file}",
        f"Files scanned      : {files_scanned}",
        f"Files with matches : {files_with_matches}",
        f"Total line matches : {total_matches}",
        f"Patterns           : {', '.join(patterns)}",
        "",
    ]

    report_file.write_text("\n".join(header + blocks), encoding="utf-8")

    print("OSBB project code search - READ ONLY")
    print("Root:", osbb_root)
    print("Report:", report_file)
    print("Files scanned:", files_scanned)
    print("Files with matches:", files_with_matches)
    print("Total line matches:", total_matches)
    print("READ ONLY COMPLETED")


if __name__ == "__main__":
    main()
