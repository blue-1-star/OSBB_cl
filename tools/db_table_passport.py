#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
db_table_passport.py

OSBB Table Passport System v1 - READ ONLY

Универсальный генератор паспорта таблицы SQLite.

Что делает:
- читает структуру таблицы;
- читает индексы;
- читает foreign keys;
- выгружает sample rows;
- собирает distinct values по code/name/status/category/type полям;
- ищет упоминания таблицы, её колонок и service_code по исходникам проекта;
- классифицирует чтение/запись;
- строит TXT/CSV/MD отчёты;
- ничего не меняет в БД и исходниках.

PowerShell examples:

  python .\OSBB\tools\db_table_passport.py service_catalog --db "G:\Programming\Py\OSBB\Data\db\sandbox\osbb_test_live_services_2026-06-26_20-13-26.db"

  python .\OSBB\tools\db_table_passport.py service_items --db "G:\Programming\Py\OSBB\Data\db\sandbox\osbb_test_live_services_2026-06-26_20-13-26.db"

Если --db не указан, пробует взять БД из config.py.
"""

from __future__ import annotations

import argparse
import ast
import csv
import os
import re
import sqlite3
import sys
import warnings
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


EXCLUDED_DIRS = {
    ".git", ".hg", ".svn", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".idea", ".vscode", "venv", ".venv", "env", ".env", "node_modules", "build", "dist",
    ".tox", "exports", "logs", "db", "backups", "raw", "typed", "sandbox",
}

EXCLUDED_REL_PREFIXES = {
    "Data/exports", "Data/logs", "Data/db", "Data/raw", "Data/backups", "Data/typed",
}

TEXT_SUFFIXES = {".py", ".sql", ".md", ".txt", ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".bat", ".ps1"}


@dataclass
class ColumnInfo:
    cid: int
    name: str
    type: str
    notnull: int
    default_value: str
    pk: int
    references_in_code: int
    read_contexts: int
    write_contexts: int


@dataclass
class IndexInfo:
    name: str
    unique: int
    origin: str
    partial: int
    columns: str
    sql: str


@dataclass
class ForeignKeyInfo:
    id: int
    seq: int
    table: str
    from_column: str
    to_column: str
    on_update: str
    on_delete: str
    match: str


@dataclass
class CodeReference:
    path: str
    line: int
    kind: str
    symbol: str
    context: str


@dataclass
class ServiceCodeUsage:
    service_code: str
    table_rows: int
    code_refs: int
    files: str


@dataclass
class SummaryItem:
    key: str
    value: str


def text(v: Any) -> str:
    return "" if v is None else str(v).strip()


def project_root_from_script() -> Path:
    here = Path(__file__).resolve()
    if here.parent.name.lower() == "tools":
        return here.parent.parent
    cwd = Path.cwd().resolve()
    if (cwd / "OSBB").exists():
        return cwd / "OSBB"
    return cwd


def db_from_config(root: Path) -> tuple[Path, str]:
    py_root = root.parent
    if str(py_root) not in sys.path:
        sys.path.insert(0, str(py_root))
    import config  # type: ignore
    use_test = bool(getattr(config, "USE_TEST_DB", False))
    paths = getattr(config, "paths")
    return Path(paths.OSBB_TEST_DB_FILE if use_test else paths.OSBB_DB_FILE), ("config:test" if use_test else "config:prod")


def connect_ro(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def table_exists(cur: sqlite3.Cursor, table: str) -> bool:
    cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,))
    return cur.fetchone() is not None


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
    return any(part in EXCLUDED_DIRS for part in parts)


def iter_text_files(root: Path) -> Iterable[Path]:
    for dirpath, dirnames, filenames in os.walk(root):
        dpath = Path(dirpath)
        dirnames[:] = [d for d in dirnames if not is_excluded(dpath / d, root)]
        if is_excluded(dpath, root):
            continue
        for name in filenames:
            p = dpath / name
            if p.suffix.lower() in TEXT_SUFFIXES and not is_excluded(p, root):
                yield p


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        try:
            return path.read_text(encoding="cp1251")
        except UnicodeDecodeError:
            return path.read_text(encoding="utf-8", errors="replace")


def classify_line(line: str, table: str, columns: list[str]) -> str:
    low = line.lower()
    t = table.lower()

    if re.search(rf"\b(insert\s+into|create\s+table|alter\s+table)\s+[`\"']?{re.escape(t)}\b", low):
        return "WRITE_SCHEMA_OR_INSERT"
    if re.search(rf"\b(update|delete\s+from)\s+[`\"']?{re.escape(t)}\b", low):
        return "WRITE_UPDATE_DELETE"
    if re.search(rf"\b(from|join)\s+[`\"']?{re.escape(t)}\b", low):
        return "READ_SQL"
    if t in low:
        if any(w in low for w in ["insert", "update", "delete", "create table", "alter table"]):
            return "WRITE_OR_SCHEMA_MENTION"
        return "TABLE_MENTION"

    for col in columns:
        c = col.lower()
        if c and re.search(rf"\b{re.escape(c)}\b", low):
            if any(w in low for w in ["insert", "update", "set ", "values", "create table", "alter table"]):
                return "COLUMN_WRITE_OR_SCHEMA"
            return "COLUMN_READ_OR_MENTION"

    return "OTHER"


def collect_code_references(root: Path, table: str, columns: list[str], service_codes: list[str]) -> list[CodeReference]:
    refs: list[CodeReference] = []
    table_low = table.lower()
    column_set = {c.lower(): c for c in columns}
    service_set = {s.lower(): s for s in service_codes if s}

    # Limit service code scanning if too many codes.
    scan_service_codes = len(service_set) <= 200

    for p in iter_text_files(root):
        try:
            data = read_text(p)
        except Exception:
            continue

        r = rel(p, root)
        for idx, line in enumerate(data.splitlines(), start=1):
            low = line.lower()
            symbols = []

            if table_low in low:
                symbols.append(table)

            for c_low, c_orig in column_set.items():
                if c_low and re.search(rf"\b{re.escape(c_low)}\b", low):
                    symbols.append(c_orig)

            if scan_service_codes:
                for s_low, s_orig in service_set.items():
                    if s_low and s_low in low:
                        symbols.append(s_orig)

            if not symbols:
                continue

            kind = classify_line(line, table, columns)
            # Avoid producing a huge report from generic columns if table/service not present.
            if table not in symbols and not any(s in symbols for s in service_codes):
                # Keep only likely important column hits.
                important_cols = {"service_code", "service_name", "service_type", "category", "is_active", "is_access_control", "is_cash_collectable"}
                if not any(s in important_cols for s in symbols):
                    continue

            refs.append(CodeReference(
                path=r,
                line=idx,
                kind=kind,
                symbol=", ".join(sorted(set(symbols))),
                context=line.strip()[:800],
            ))

    return refs


def get_columns(cur: sqlite3.Cursor, table: str, refs: list[CodeReference]) -> list[ColumnInfo]:
    cur.execute(f"PRAGMA table_info({table})")
    rows = cur.fetchall()

    result = []
    for row in rows:
        name = row["name"]
        ref_count = sum(1 for r in refs if name in [x.strip() for x in r.symbol.split(",")])
        read_count = sum(1 for r in refs if name in r.symbol and "READ" in r.kind)
        write_count = sum(1 for r in refs if name in r.symbol and ("WRITE" in r.kind or "SCHEMA" in r.kind))
        result.append(ColumnInfo(
            cid=int(row["cid"]),
            name=name,
            type=text(row["type"]),
            notnull=int(row["notnull"]),
            default_value=text(row["dflt_value"]),
            pk=int(row["pk"]),
            references_in_code=ref_count,
            read_contexts=read_count,
            write_contexts=write_count,
        ))
    return result


def get_indexes(cur: sqlite3.Cursor, table: str) -> list[IndexInfo]:
    cur.execute(f"PRAGMA index_list({table})")
    idx_rows = cur.fetchall()
    result = []
    for idx in idx_rows:
        name = idx["name"]
        try:
            cur.execute(f"PRAGMA index_info({name})")
            cols = ", ".join(row["name"] for row in cur.fetchall())
        except Exception:
            cols = ""
        cur.execute("SELECT sql FROM sqlite_master WHERE type='index' AND name=?", (name,))
        sql_row = cur.fetchone()
        result.append(IndexInfo(
            name=name,
            unique=int(idx["unique"]),
            origin=text(idx["origin"]),
            partial=int(idx["partial"]),
            columns=cols,
            sql=text(sql_row["sql"]) if sql_row else "",
        ))
    return result


def get_fks(cur: sqlite3.Cursor, table: str) -> list[ForeignKeyInfo]:
    cur.execute(f"PRAGMA foreign_key_list({table})")
    result = []
    for row in cur.fetchall():
        result.append(ForeignKeyInfo(
            id=int(row["id"]),
            seq=int(row["seq"]),
            table=text(row["table"]),
            from_column=text(row["from"]),
            to_column=text(row["to"]),
            on_update=text(row["on_update"]),
            on_delete=text(row["on_delete"]),
            match=text(row["match"]),
        ))
    return result


def collect_distincts(cur: sqlite3.Cursor, table: str, col_names: list[str], limit: int = 100) -> dict[str, list[str]]:
    interesting = [
        c for c in col_names
        if "code" in c.lower()
        or "name" in c.lower()
        or "title" in c.lower()
        or "status" in c.lower()
        or "type" in c.lower()
        or "category" in c.lower()
        or c.lower().startswith("is_")
    ]
    result: dict[str, list[str]] = {}
    for c in interesting:
        try:
            cur.execute(
                f'SELECT DISTINCT "{c}" FROM {table} '
                f'WHERE "{c}" IS NOT NULL AND TRIM(CAST("{c}" AS TEXT)) <> "" '
                f'ORDER BY "{c}" LIMIT {int(limit)}'
            )
            result[c] = [text(row[0]) for row in cur.fetchall()]
        except Exception as exc:
            result[c] = [f"ERROR: {type(exc).__name__}: {exc}"]
    return result


def sample_rows(cur: sqlite3.Cursor, table: str, limit: int = 30) -> list[dict[str, Any]]:
    cur.execute(f"SELECT * FROM {table} LIMIT {int(limit)}")
    return [dict(row) for row in cur.fetchall()]


def table_count(cur: sqlite3.Cursor, table: str) -> int:
    cur.execute(f"SELECT COUNT(*) FROM {table}")
    return int(cur.fetchone()[0])


def find_service_code_col(columns: list[str]) -> str:
    for c in ["service_code", "service_item_code", "code"]:
        if c in columns:
            return c
    return ""


def service_code_usage(cur: sqlite3.Cursor, table: str, columns: list[str], refs: list[CodeReference]) -> list[ServiceCodeUsage]:
    code_col = find_service_code_col(columns)
    if not code_col:
        return []

    cur.execute(f'SELECT "{code_col}", COUNT(*) AS n FROM {table} GROUP BY "{code_col}" ORDER BY "{code_col}"')
    result = []
    for row in cur.fetchall():
        code = text(row[0])
        if not code:
            continue
        matching = [r for r in refs if code in r.symbol or code in r.context]
        files = ", ".join(sorted({r.path for r in matching})[:20])
        if len({r.path for r in matching}) > 20:
            files += " ..."
        result.append(ServiceCodeUsage(
            service_code=code,
            table_rows=int(row["n"]),
            code_refs=len(matching),
            files=files,
        ))
    return result


def write_csv(path: Path, rows: list[Any], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            if hasattr(row, "__dataclass_fields__"):
                w.writerow(asdict(row))
            else:
                w.writerow(row)


def write_txt(path: Path, lines: list[str]) -> None:
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def write_md(path: Path, title: str, lines: list[str]) -> None:
    path.write_text("# " + title + "\n\n" + "\n".join(lines).rstrip() + "\n", encoding="utf-8")


def architecture_notes(table: str, columns: list[ColumnInfo], service_usages: list[ServiceCodeUsage], refs: list[CodeReference]) -> list[str]:
    lines = []
    lines.append("## Автоматическое архитектурное заключение")
    lines.append("")

    col_names = {c.name for c in columns}
    if table == "service_catalog":
        lines.append("`service_catalog` выглядит как центральный справочник услуг OSBB.")
        lines.append("Он уже содержит признаки, которые подходят для политики доступа: `is_access_control`, `is_cash_collectable`, `is_monthly`, `is_fundraising`, `is_commercial`.")
        lines.append("")
        missing_policy = [c for c in ["requires_no_debt", "block_order", "block_issue", "block_activation", "manual_review", "debt_message"] if c not in col_names]
        if missing_policy:
            lines.append("Потенциальные расширения для политики задолженности, которых сейчас нет:")
            for c in missing_policy:
                lines.append(f"- `{c}`")
        lines.append("")
        lines.append("Рекомендация v1: не создавать отдельный справочник услуг. Расширять политику поверх существующих `service_code`.")
    else:
        lines.append(f"Таблица `{table}` проанализирована универсальным паспортом. Для архитектурного заключения GOLD нужен ручной обзор.")

    unused_cols = [c.name for c in columns if c.references_in_code == 0]
    if unused_cols:
        lines.append("")
        lines.append("Колонки без найденных ссылок в коде:")
        for c in unused_cols:
            lines.append(f"- `{c}`")

    high_usage = sorted(service_usages, key=lambda x: x.code_refs, reverse=True)[:10]
    if high_usage:
        lines.append("")
        lines.append("Наиболее упоминаемые service_code:")
        for u in high_usage:
            lines.append(f"- `{u.service_code}`: refs={u.code_refs}, rows={u.table_rows}")

    write_refs = [r for r in refs if "WRITE" in r.kind or "SCHEMA" in r.kind]
    lines.append("")
    lines.append(f"Найдено ссылок в коде всего: {len(refs)}")
    lines.append(f"Из них потенциальных write/schema контекстов: {len(write_refs)}")
    return lines


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate OSBB SQLite table passport.")
    ap.add_argument("table", help="Table name, e.g. service_catalog")
    ap.add_argument("--db", default="", help="Explicit SQLite DB path.")
    ap.add_argument("--level", default="gold", choices=["bronze", "silver", "gold"])
    args = ap.parse_args()

    root = project_root_from_script()

    if args.db:
        db_path = Path(args.db)
        db_mode = "explicit"
    else:
        db_path, db_mode = db_from_config(root)

    table = args.table.strip()
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    out_dir = root / "Data" / "exports" / "db_passports" / table / f"{args.level}_{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)

    conn = connect_ro(db_path)
    try:
        cur = conn.cursor()
        if not table_exists(cur, table):
            raise SystemExit(f"Table not found: {table}")

        cur.execute(f"PRAGMA table_info({table})")
        raw_cols = cur.fetchall()
        col_names = [row["name"] for row in raw_cols]

        # collect service-like codes from current table first
        service_codes = []
        code_col = find_service_code_col(col_names)
        if code_col:
            cur.execute(f'SELECT DISTINCT "{code_col}" FROM {table} WHERE "{code_col}" IS NOT NULL')
            service_codes = [text(row[0]) for row in cur.fetchall() if text(row[0])]

        refs = collect_code_references(root, table, col_names, service_codes) if args.level in {"silver", "gold"} else []
        columns = get_columns(cur, table, refs)
        indexes = get_indexes(cur, table)
        fks = get_fks(cur, table)
        distincts = collect_distincts(cur, table, col_names)
        samples = sample_rows(cur, table)
        count = table_count(cur, table)
        usages = service_code_usage(cur, table, col_names, refs)

        # CSV outputs
        write_csv(out_dir / "02_columns.csv", columns, list(ColumnInfo.__dataclass_fields__.keys()))
        write_csv(out_dir / "03_indexes.csv", indexes, list(IndexInfo.__dataclass_fields__.keys()))
        write_csv(out_dir / "04_foreign_keys.csv", fks, list(ForeignKeyInfo.__dataclass_fields__.keys()))
        write_csv(out_dir / "05_code_references.csv", refs, list(CodeReference.__dataclass_fields__.keys()))
        write_csv(out_dir / "06_service_code_usage.csv", usages, list(ServiceCodeUsage.__dataclass_fields__.keys()))
        write_csv(out_dir / "07_sample_rows.csv", samples, col_names)

        # Schema txt
        schema_lines = []
        schema_lines.append(f"DB: {db_path}")
        schema_lines.append(f"Table: {table}")
        schema_lines.append(f"Rows: {count}")
        schema_lines.append("")
        schema_lines.append("COLUMNS")
        for c in columns:
            schema_lines.append(
                f"- {c.name} | type={c.type or '-'} | notnull={c.notnull} | pk={c.pk} | default={c.default_value or '-'} | refs={c.references_in_code}"
            )
        schema_lines.append("")
        schema_lines.append("INDEXES")
        if indexes:
            for i in indexes:
                schema_lines.append(f"- {i.name} | unique={i.unique} | columns={i.columns} | origin={i.origin}")
        else:
            schema_lines.append("(none)")
        schema_lines.append("")
        schema_lines.append("FOREIGN KEYS")
        if fks:
            for fk in fks:
                schema_lines.append(f"- {fk.from_column} -> {fk.table}.{fk.to_column} | on_update={fk.on_update} | on_delete={fk.on_delete}")
        else:
            schema_lines.append("(none)")
        write_txt(out_dir / "01_schema.txt", schema_lines)

        # Distincts txt
        distinct_lines = []
        for col, vals in distincts.items():
            distinct_lines.append("=" * 80)
            distinct_lines.append(col)
            distinct_lines.append("=" * 80)
            for v in vals:
                distinct_lines.append(str(v))
            distinct_lines.append("")
        write_txt(out_dir / "08_distinct_values.txt", distinct_lines)

        # Used/written modules
        by_kind = defaultdict(list)
        for r in refs:
            by_kind[r.kind].append(r)

        used_lines = []
        for kind in sorted(by_kind):
            used_lines.append("=" * 100)
            used_lines.append(kind)
            used_lines.append("=" * 100)
            for r in by_kind[kind][:500]:
                used_lines.append(f"{r.path}:{r.line} | {r.symbol} | {r.context}")
            if len(by_kind[kind]) > 500:
                used_lines.append(f"... and {len(by_kind[kind]) - 500} more")
            used_lines.append("")
        write_txt(out_dir / "09_code_references_by_kind.txt", used_lines or ["(none)"])

        # Architecture notes
        notes = architecture_notes(table, columns, usages, refs)
        write_md(out_dir / "10_architecture_notes.md", f"Architecture notes for {table}", notes)

        # Summary
        summary = []
        summary.append(f"OSBB Table Passport - {args.level.upper()}")
        summary.append(f"Generated: {datetime.now():%Y-%m-%d %H:%M:%S}")
        summary.append(f"DB mode: {db_mode}")
        summary.append(f"DB: {db_path}")
        summary.append(f"Table: {table}")
        summary.append(f"Rows: {count}")
        summary.append(f"Columns: {len(columns)}")
        summary.append(f"Indexes: {len(indexes)}")
        summary.append(f"Foreign keys: {len(fks)}")
        summary.append(f"Code references: {len(refs)}")
        summary.append(f"Service codes: {len(usages)}")
        summary.append("")
        summary.append("Output files:")
        for name in [
            "01_schema.txt",
            "02_columns.csv",
            "03_indexes.csv",
            "04_foreign_keys.csv",
            "05_code_references.csv",
            "06_service_code_usage.csv",
            "07_sample_rows.csv",
            "08_distinct_values.txt",
            "09_code_references_by_kind.txt",
            "10_architecture_notes.md",
        ]:
            summary.append(f"- {name}")
        write_txt(out_dir / "passport_summary.txt", summary)

        print("OSBB Table Passport - READ ONLY")
        print(f"Level: {args.level.upper()}")
        print(f"DB mode: {db_mode}")
        print(f"DB: {db_path}")
        print(f"Table: {table}")
        print(f"Rows: {count}")
        print(f"Columns: {len(columns)}")
        print(f"Indexes: {len(indexes)}")
        print(f"Foreign keys: {len(fks)}")
        print(f"Code references: {len(refs)}")
        print(f"Service codes: {len(usages)}")
        print(f"Output: {out_dir}")
        print("READ ONLY COMPLETED")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
