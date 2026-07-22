#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OSBB Project Passport v3 Classifier - READ ONLY / SOURCE FOCUSED

Строит следующий слой паспорта проекта:
- классификация файлов: active candidate / runtime entrypoint / handler / tool / migration / legacy backup;
- список точек входа;
- список Telegram handler/workspace модулей;
- список исторических копий и патчей;
- тематические признаки: cashier, parking_time, remote/pult, service_orders, db/schema.

Ничего не меняет в проекте и БД.
Пишет только в OSBB/Data/exports/code_passport/project_passport_v3_<timestamp>/.
"""

from __future__ import annotations

import ast
import csv
import json
import os
import re
import warnings
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Any


EXCLUDED_DIRS = {
    ".git", ".hg", ".svn", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".idea", ".vscode", "venv", ".venv", "env", ".env", "node_modules", "build", "dist",
    ".tox", "exports", "logs", "db", "backups", "raw", "typed", "sandbox",
}

EXCLUDED_REL_PREFIXES = {
    "Data/exports", "Data/logs", "Data/db", "Data/raw", "Data/backups", "Data/typed",
}

EXCLUDED_FILE_SUFFIXES = {
    ".pyc", ".pyo", ".pyd", ".dll", ".exe", ".db", ".sqlite", ".sqlite3",
    ".jpg", ".jpeg", ".png", ".gif", ".ico", ".pdf", ".zip", ".7z", ".rar",
    ".xlsx", ".xls", ".docx", ".pptx",
}

TEXT_SUFFIXES = {
    ".py", ".txt", ".md", ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg",
    ".bat", ".ps1", ".sql", ".csv",
}

DATE_STAMP_RE = re.compile(r"(20\d{2}[-_]\d{2}[-_]\d{2}|\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})")
COPY_RE = re.compile(r"(\bcopy\b| - copy|_copy|копия|before_|_before_|backup|bak)", re.IGNORECASE)

THEME_PATTERNS = {
    "cashier": re.compile(r"cashier|cashbox|касс|квитан|payment|payments|charges|receipt", re.IGNORECASE),
    "parking_time": re.compile(r"parking_time|Day|Night|Не користується паркуванням|парков", re.IGNORECASE),
    "remote": re.compile(r"remote|пульт|пульти|пультів|брел|handover", re.IGNORECASE),
    "service_orders": re.compile(r"service_order|service_orders|workflow_profile|supplier_batch|remote_order", re.IGNORECASE),
    "schema_db": re.compile(r"schema|sqlite|database|db_|PRAGMA|CREATE TABLE|ALTER TABLE", re.IGNORECASE),
    "telegram_bot": re.compile(r"telegram|Update|ContextTypes|Application\.builder|run_polling|MessageHandler|CallbackQueryHandler", re.IGNORECASE),
    "access_control": re.compile(r"access|permission|guard|охрана|шлагбаум|barrier|phone", re.IGNORECASE),
}

ENTRYPOINT_HINT_RE = re.compile(
    r"if\s+__name__\s*==\s*['\"]__main__['\"]|Application\.builder|run_polling|asyncio\.run|argparse\.ArgumentParser",
    re.IGNORECASE,
)


@dataclass
class ClassifiedFile:
    path: str
    suffix: str
    size_bytes: int
    lines: int
    modified_at: str
    kind: str
    category: str
    confidence: int
    reasons: str
    themes: str


@dataclass
class PythonModule:
    path: str
    lines: int
    category: str
    confidence: int
    reasons: str
    themes: str
    functions: int
    classes: int
    imports: int
    local_imports: str
    imported_by_count: int
    has_main_guard: bool
    has_argparse: bool
    has_telegram_entry: bool
    has_async_handlers: bool
    parse_error: str


@dataclass
class Entrypoint:
    path: str
    entrypoint_type: str
    confidence: int
    reasons: str
    themes: str


@dataclass
class FunctionInfo:
    path: str
    name: str
    line: int
    end_line: int
    async_func: bool


@dataclass
class ImportEdge:
    importer_path: str
    imported_module: str
    imported_name: str
    line: int
    local_candidate: bool


def project_root_from_script() -> Path:
    here = Path(__file__).resolve()
    if here.parent.name.lower() == "tools":
        return here.parent.parent
    cwd = Path.cwd().resolve()
    if (cwd / "Data").exists() or (cwd / "Bots").exists():
        return cwd
    if (cwd / "OSBB").exists():
        return cwd / "OSBB"
    return here.parent


def rel(path: Path, root: Path) -> str:
    return str(path.relative_to(root)).replace("\\", "/")


def is_excluded(path: Path, root: Path) -> bool:
    try:
        r = rel(path, root)
    except ValueError:
        return True

    for prefix in EXCLUDED_REL_PREFIXES:
        if r == prefix or r.startswith(prefix + "/"):
            return True

    parts = path.relative_to(root).parts
    if any(part in EXCLUDED_DIRS for part in parts):
        return True

    if path.is_file():
        lower_name = path.name.lower()
        if path.suffix.lower() in EXCLUDED_FILE_SUFFIXES:
            return True
        if lower_name.endswith(".db-wal") or lower_name.endswith(".db-shm"):
            return True

    return False


def iter_files(root: Path) -> Iterable[Path]:
    for dirpath, dirnames, filenames in os.walk(root):
        dpath = Path(dirpath)
        dirnames[:] = [d for d in dirnames if not is_excluded(dpath / d, root)]
        if is_excluded(dpath, root):
            continue
        for name in filenames:
            p = dpath / name
            if not is_excluded(p, root):
                yield p


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        try:
            return path.read_text(encoding="cp1251")
        except UnicodeDecodeError:
            return path.read_text(encoding="utf-8", errors="replace")


def count_lines(path: Path) -> int:
    if path.suffix.lower() not in TEXT_SUFFIXES:
        return 0
    try:
        return read_text(path).count("\n") + 1
    except Exception:
        return 0


def kind(path: Path) -> str:
    s = path.suffix.lower()
    if s == ".py":
        return "python"
    if s in {".md", ".txt"}:
        return "doc"
    if s in {".json", ".yaml", ".yml", ".toml", ".ini", ".cfg"}:
        return "config"
    if s == ".sql":
        return "sql"
    if s in {".bat", ".ps1"}:
        return "script"
    if s == ".csv":
        return "data_text"
    return "other"


def detect_themes(path: Path, root: Path, text: str | None = None) -> list[str]:
    hay = rel(path, root)
    if text is None and path.suffix.lower() in TEXT_SUFFIXES:
        try:
            text = read_text(path)
        except Exception:
            text = ""
    if text:
        hay += "\n" + text[:200000]  # enough for classification without huge memory
    themes = [name for name, rx in THEME_PATTERNS.items() if rx.search(hay)]
    return sorted(themes)


def classify(path: Path, root: Path, text: str | None = None) -> tuple[str, int, list[str]]:
    r = rel(path, root)
    name = path.name
    lower = r.lower()
    reasons: list[str] = []
    confidence = 40

    if COPY_RE.search(r) or DATE_STAMP_RE.search(r) or "foundation_v" in lower:
        reasons.append("historical/copy/date-stamped/foundation package")
        return "legacy_or_backup", 90, reasons

    if path.suffix.lower() != ".py":
        if lower.startswith("docs/") or path.suffix.lower() in {".md", ".txt"}:
            return "documentation", 70, ["documentation/text file"]
        if path.suffix.lower() in {".json", ".yaml", ".yml", ".toml", ".ini", ".cfg"}:
            return "configuration", 70, ["configuration file"]
        return "non_python_support", 45, ["non-python support file"]

    if lower.startswith("tools/"):
        reasons.append("under tools/")
        return "tooling", 90, reasons

    if lower.startswith("bots/handlers/"):
        reasons.append("telegram handler/workspace module")
        return "bot_handler", 88, reasons

    if name.startswith("run_") or name in {"main.py", "app.py", "bot.py"}:
        reasons.append("runtime-style entrypoint filename")
        return "runtime_entrypoint", 90, reasons

    if any(x in lower for x in ["install_", "installer", "patch_", "migrate_", "migration", "preflight"]):
        reasons.append("installer/patch/migration/preflight filename")
        return "installer_patch_migration", 85, reasons

    if any(x in lower for x in ["test_", "_test", "sandbox", "check_", "diagnostic", "debug"]):
        reasons.append("test/sandbox/check/diagnostic filename")
        return "test_or_diagnostic", 75, reasons

    if any(x in lower for x in ["report", "export", "excel", "statement", "billing"]):
        reasons.append("report/export/billing filename")
        return "report_export", 70, reasons

    if any(x in lower for x in ["import", "clean", "audit_", "parse", "normalize"]):
        reasons.append("import/clean/audit/normalization filename")
        return "import_cleanup_audit", 65, reasons

    if text is None:
        try:
            text = read_text(path)
        except Exception:
            text = ""

    if ENTRYPOINT_HINT_RE.search(text or ""):
        reasons.append("contains main/argparse/telegram application hint")
        return "runtime_or_cli_entrypoint", 78, reasons

    reasons.append("regular source module candidate")
    return "source_module", confidence, reasons


def ast_name(node: ast.AST) -> str:
    try:
        return ast.unparse(node)
    except Exception:
        return getattr(node, "id", "") or getattr(node, "attr", "") or ""


def module_name_from_path(path: str) -> str:
    p = Path(path)
    if p.name == "__init__.py":
        parts = p.parent.parts
    else:
        parts = p.with_suffix("").parts
    return ".".join(parts).replace("/", ".")


def parse_python(path: Path, root: Path) -> tuple[list[FunctionInfo], list[ImportEdge], dict, str]:
    r = rel(path, root)
    funcs: list[FunctionInfo] = []
    imports: list[ImportEdge] = []
    flags = {
        "classes": 0,
        "has_main_guard": False,
        "has_argparse": False,
        "has_telegram_entry": False,
        "has_async_handlers": False,
    }
    parse_error = ""

    try:
        source = read_text(path)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", SyntaxWarning)
            tree = ast.parse(source, filename=str(path))
    except Exception as exc:
        return funcs, imports, flags, f"{type(exc).__name__}: {exc}"

    source_text = read_text(path)
    flags["has_main_guard"] = "__name__" in source_text and "__main__" in source_text
    flags["has_argparse"] = "argparse.ArgumentParser" in source_text
    flags["has_telegram_entry"] = any(x in source_text for x in [
        "Application.builder", "run_polling", "MessageHandler", "CallbackQueryHandler", "telegram.ext"
    ])

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            flags["classes"] += 1
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            funcs.append(FunctionInfo(
                path=r,
                name=node.name,
                line=node.lineno,
                end_line=getattr(node, "end_lineno", node.lineno),
                async_func=isinstance(node, ast.AsyncFunctionDef),
            ))
            if isinstance(node, ast.AsyncFunctionDef) and (
                node.name.startswith("handle") or node.name.startswith("show") or node.name.startswith("start")
            ):
                flags["has_async_handlers"] = True
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(ImportEdge(
                    importer_path=r,
                    imported_module=alias.name,
                    imported_name=alias.asname or "",
                    line=node.lineno,
                    local_candidate=False,
                ))
        elif isinstance(node, ast.ImportFrom):
            mod = "." * node.level + (node.module or "")
            for alias in node.names:
                imports.append(ImportEdge(
                    importer_path=r,
                    imported_module=mod,
                    imported_name=alias.name + (f" as {alias.asname}" if alias.asname else ""),
                    line=node.lineno,
                    local_candidate=False,
                ))

    return funcs, imports, flags, parse_error


def write_csv(path: Path, rows: list[Any], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow(asdict(row) if hasattr(row, "__dataclass_fields__") else row)


def write_md(path: Path, title: str, body: str) -> None:
    path.write_text(f"# {title}\n\n{body.rstrip()}\n", encoding="utf-8")


def main() -> int:
    warnings.simplefilter("ignore", SyntaxWarning)

    root = project_root_from_script()
    now = datetime.now()
    stamp = now.strftime("%Y-%m-%d_%H-%M-%S")
    out_dir = root / "Data" / "exports" / "code_passport" / f"project_passport_v3_{stamp}"
    inv = out_dir / "inventory"
    out_dir.mkdir(parents=True, exist_ok=True)
    inv.mkdir(parents=True, exist_ok=True)

    paths = sorted(iter_files(root), key=lambda p: rel(p, root).lower())

    files: list[ClassifiedFile] = []
    modules: list[PythonModule] = []
    functions: list[FunctionInfo] = []
    import_edges: list[ImportEdge] = []
    entrypoints: list[Entrypoint] = []

    # First pass: parse all python and collect possible local module names.
    py_paths = [p for p in paths if p.suffix.lower() == ".py"]
    local_modules = {module_name_from_path(rel(p, root)): rel(p, root) for p in py_paths}
    local_toplevels = {m.split(".")[0] for m in local_modules}

    parsed_cache: dict[str, tuple[list[FunctionInfo], list[ImportEdge], dict, str]] = {}

    for p in paths:
        r = rel(p, root)
        text = ""
        if p.suffix.lower() in TEXT_SUFFIXES:
            try:
                text = read_text(p)
            except Exception:
                text = ""

        category, confidence, reasons = classify(p, root, text)
        themes = detect_themes(p, root, text)
        st = p.stat()
        lines = count_lines(p)

        files.append(ClassifiedFile(
            path=r,
            suffix=p.suffix.lower(),
            size_bytes=st.st_size,
            lines=lines,
            modified_at=datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
            kind=kind(p),
            category=category,
            confidence=confidence,
            reasons="; ".join(reasons),
            themes=", ".join(themes),
        ))

        if p.suffix.lower() == ".py":
            funcs, imports, flags, parse_error = parse_python(p, root)
            parsed_cache[r] = (funcs, imports, flags, parse_error)
            functions.extend(funcs)

            # Mark local import candidates.
            local_import_names = []
            for edge in imports:
                first = edge.imported_module.lstrip(".").split(".")[0]
                if first in local_toplevels or edge.imported_module.lstrip(".") in local_modules:
                    edge.local_candidate = True
                    local_import_names.append(edge.imported_module)
                import_edges.append(edge)

            ep_type = ""
            ep_reasons = []
            if category in {"runtime_entrypoint", "runtime_or_cli_entrypoint"}:
                ep_type = category
                ep_reasons.append(category)
            if flags.get("has_main_guard"):
                ep_reasons.append("has __main__ guard")
            if flags.get("has_argparse"):
                ep_reasons.append("has argparse")
            if flags.get("has_telegram_entry"):
                ep_reasons.append("has telegram application/handler hints")
            if ep_reasons:
                entrypoints.append(Entrypoint(
                    path=r,
                    entrypoint_type=ep_type or "candidate_entrypoint",
                    confidence=max(confidence, 70),
                    reasons="; ".join(ep_reasons),
                    themes=", ".join(themes),
                ))

    # Imported-by counts for local modules, approximate.
    imported_by_count: dict[str, int] = {rel(p, root): 0 for p in py_paths}
    for edge in import_edges:
        mod = edge.imported_module.lstrip(".")
        candidates = []
        if mod in local_modules:
            candidates.append(local_modules[mod])
        first = mod.split(".")[0]
        for local_mod, local_path in local_modules.items():
            if local_mod == mod or local_mod.endswith("." + mod) or local_mod.split(".")[-1] == first:
                candidates.append(local_path)
        for c in set(candidates):
            imported_by_count[c] = imported_by_count.get(c, 0) + 1

    for p in py_paths:
        r = rel(p, root)
        funcs, imports, flags, parse_error = parsed_cache.get(r, ([], [], {}, "not parsed"))
        cf = next(f for f in files if f.path == r)
        modules.append(PythonModule(
            path=r,
            lines=cf.lines,
            category=cf.category,
            confidence=cf.confidence,
            reasons=cf.reasons,
            themes=cf.themes,
            functions=len(funcs),
            classes=int(flags.get("classes", 0)),
            imports=len(imports),
            local_imports=", ".join(sorted({e.imported_module for e in imports if e.local_candidate})),
            imported_by_count=imported_by_count.get(r, 0),
            has_main_guard=bool(flags.get("has_main_guard")),
            has_argparse=bool(flags.get("has_argparse")),
            has_telegram_entry=bool(flags.get("has_telegram_entry")),
            has_async_handlers=bool(flags.get("has_async_handlers")),
            parse_error=parse_error,
        ))

    # Write CSV
    write_csv(inv / "files_classified.csv", files, list(ClassifiedFile.__dataclass_fields__.keys()))
    write_csv(inv / "python_modules_classified.csv", modules, list(PythonModule.__dataclass_fields__.keys()))
    write_csv(inv / "entrypoints.csv", entrypoints, list(Entrypoint.__dataclass_fields__.keys()))
    write_csv(inv / "functions.csv", functions, list(FunctionInfo.__dataclass_fields__.keys()))
    write_csv(inv / "import_edges.csv", import_edges, list(ImportEdge.__dataclass_fields__.keys()))

    # Summaries.
    by_category: dict[str, int] = {}
    for f in files:
        by_category[f.category] = by_category.get(f.category, 0) + 1

    by_theme: dict[str, int] = {}
    for f in files:
        for t in [x.strip() for x in f.themes.split(",") if x.strip()]:
            by_theme[t] = by_theme.get(t, 0) + 1

    summary = {
        "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "root": str(root),
        "out_dir": str(out_dir),
        "scope": "source-focused; generated data and DB folders excluded",
        "files": len(files),
        "python_modules": len(modules),
        "functions": len(functions),
        "entrypoints": len(entrypoints),
        "categories": by_category,
        "themes": by_theme,
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    # Markdown reports.
    lines = []
    lines.append("## Summary\n")
    for k in ["generated_at", "root", "scope", "files", "python_modules", "functions", "entrypoints"]:
        lines.append(f"- {k}: **{summary[k]}**" if isinstance(summary[k], int) else f"- {k}: `{summary[k]}`")
    lines.append("")
    lines.append("## Categories\n")
    for k, v in sorted(by_category.items(), key=lambda x: (-x[1], x[0])):
        lines.append(f"- {k}: {v}")
    lines.append("")
    lines.append("## Themes\n")
    for k, v in sorted(by_theme.items(), key=lambda x: (-x[1], x[0])):
        lines.append(f"- {k}: {v}")
    lines.append("")
    lines.append("## Top large modules\n")
    for m in sorted(modules, key=lambda x: x.lines, reverse=True)[:30]:
        lines.append(f"- `{m.path}` — {m.lines} lines, category={m.category}, themes={m.themes or '-'}")
    write_md(out_dir / "00_Project_v3.md", "OSBB Project Passport v3 Classifier", "\n".join(lines))

    ep_lines = []
    for e in sorted(entrypoints, key=lambda x: (x.entrypoint_type, x.path)):
        ep_lines.append(f"## `{e.path}`")
        ep_lines.append(f"- Type: {e.entrypoint_type}")
        ep_lines.append(f"- Confidence: {e.confidence}")
        ep_lines.append(f"- Themes: {e.themes or '-'}")
        ep_lines.append(f"- Reasons: {e.reasons}")
        ep_lines.append("")
    write_md(out_dir / "05_Entrypoints.md", "Entrypoints and CLI Candidates", "\n".join(ep_lines) or "(none)")

    active_lines = []
    active_categories = {"runtime_entrypoint", "runtime_or_cli_entrypoint", "bot_handler", "source_module"}
    for m in sorted([m for m in modules if m.category in active_categories], key=lambda x: (x.category, x.path)):
        active_lines.append(f"- `{m.path}` — {m.category}, lines={m.lines}, funcs={m.functions}, imported_by={m.imported_by_count}, themes={m.themes or '-'}")
    write_md(out_dir / "06_Active_Candidates.md", "Active Source Candidates", "\n".join(active_lines) or "(none)")

    legacy_lines = []
    for f in sorted([f for f in files if f.category == "legacy_or_backup"], key=lambda x: x.path):
        legacy_lines.append(f"- `{f.path}` — {f.lines} lines, reasons={f.reasons}")
    write_md(out_dir / "07_Legacy_Backups.md", "Legacy / Backup / Historical Files", "\n".join(legacy_lines) or "(none)")

    handler_lines = []
    for m in sorted([m for m in modules if m.category == "bot_handler"], key=lambda x: x.path):
        handler_lines.append(f"- `{m.path}` — lines={m.lines}, funcs={m.functions}, async_handlers={m.has_async_handlers}, themes={m.themes or '-'}")
    write_md(out_dir / "08_Bot_Handlers.md", "Bot Handlers and Workspaces", "\n".join(handler_lines) or "(none)")

    remote_lines = []
    for m in sorted([m for m in modules if "remote" in m.themes or "service_orders" in m.themes or "access_control" in m.themes], key=lambda x: x.path):
        remote_lines.append(f"- `{m.path}` — category={m.category}, lines={m.lines}, funcs={m.functions}, themes={m.themes or '-'}")
    write_md(out_dir / "09_Remote_Access_Service_Modules.md", "Remote / Access / Service Order Related Modules", "\n".join(remote_lines) or "(none)")

    risk_lines = []
    risk_lines.append("## Very large modules\n")
    for m in sorted([m for m in modules if m.lines >= 1000], key=lambda x: x.lines, reverse=True):
        risk_lines.append(f"- `{m.path}` — {m.lines} lines, {m.functions} functions, category={m.category}")
    risk_lines.append("")
    risk_lines.append("## Parse errors\n")
    errs = [m for m in modules if m.parse_error]
    if errs:
        for m in errs:
            risk_lines.append(f"- `{m.path}` — {m.parse_error}")
    else:
        risk_lines.append("(none)")
    risk_lines.append("")
    risk_lines.append("## Historical copies included in source tree\n")
    legacy_count = sum(1 for f in files if f.category == "legacy_or_backup")
    risk_lines.append(f"- Legacy/backup files detected: {legacy_count}")
    write_md(out_dir / "10_Code_Risk_Summary.md", "Code Risk Summary", "\n".join(risk_lines))

    print("OSBB Project Passport v3 Classifier - READ ONLY")
    print(f"Root: {root}")
    print(f"Output: {out_dir}")
    print("Scope: source-focused classifier")
    print(f"Files: {len(files)}")
    print(f"Python modules: {len(modules)}")
    print(f"Functions: {len(functions)}")
    print(f"Entrypoints: {len(entrypoints)}")
    print("Categories:")
    for k, v in sorted(by_category.items(), key=lambda x: (-x[1], x[0])):
        print(f" - {k}: {v}")
    print("Themes:")
    for k, v in sorted(by_theme.items(), key=lambda x: (-x[1], x[0])):
        print(f" - {k}: {v}")
    print("READ ONLY COMPLETED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
