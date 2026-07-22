# -*- coding: utf-8 -*-
r"""
OSBB — repair for sqlite3.OperationalError: no such column: p.source_ref

What this file does:
  1. Reads Bots\handlers\service_orders_workspace.py.
  2. Finds the table aliased as "p" in the SQL that uses p.source_ref.
  3. Makes a timestamped copy of Data\db\osbb.db.
  4. Adds <that_table>.source_ref TEXT only when it is missing.
  5. Creates an index for the new field and performs a verification query.

The script is repeat-safe: a second run changes nothing if the repair was
already applied. It never deletes or rewrites existing payment rows.
"""

from __future__ import annotations

import re
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Iterable


# This file must be placed directly in:
# G:\Programming\Py\OSBB\
PROJECT_ROOT = Path(__file__).resolve().parent
HANDLER_PATH = PROJECT_ROOT / "Bots" / "handlers" / "service_orders_workspace.py"
DB_PATH = PROJECT_ROOT / "Data" / "db" / "osbb.db"
BACKUP_DIR = PROJECT_ROOT / "Data" / "db" / "backups"
LOG_DIR = PROJECT_ROOT / "Data" / "db" / "logs"

_MESSAGES: list[str] = []
_LOG_PATH: Path | None = None


def emit(message: str = "") -> None:
    """Print and retain a line for the log file."""
    print(message)
    _MESSAGES.append(message)


def flush_log() -> None:
    """Write the full run report even when the repair fails."""
    global _LOG_PATH
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        if _LOG_PATH is None:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            _LOG_PATH = LOG_DIR / f"fix_source_ref_schema_{stamp}.log"
        _LOG_PATH.write_text("\n".join(_MESSAGES) + "\n", encoding="utf-8")
        print(f"\nLog: {_LOG_PATH}")
    except Exception as log_error:  # noqa: BLE001 - a log failure must not hide the real error
        print(f"\nCould not write log file: {log_error}")


def quote_identifier(name: str) -> str:
    """Quote one SQLite identifier safely."""
    return '"' + name.replace('"', '""') + '"'


def read_python_source(path: Path) -> str:
    """Read the handler source with common encodings used in this project."""
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "cp1251"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise RuntimeError(f"Cannot decode source file: {path}")


def normalize_table_identifier(raw_name: str) -> str:
    """
    Convert SQL identifier forms such as [payments], `payments`, "payments"
    to the actual SQLite table name. Schema prefixes are not expected in OSBB.
    """
    raw_name = raw_name.strip()
    parts = [part.strip() for part in re.split(r"\s*\.\s*", raw_name)]
    normalized_parts: list[str] = []

    for part in parts:
        if len(part) >= 2 and (
            (part[0] == '"' and part[-1] == '"')
            or (part[0] == "`" and part[-1] == "`")
            or (part[0] == "[" and part[-1] == "]")
        ):
            part = part[1:-1]
        normalized_parts.append(part)

    # SQLite PRAGMA table_info expects the final table name in this project.
    return normalized_parts[-1]


def _all_from_or_join_matches(sql_before_reference: str) -> Iterable[re.Match[str]]:
    identifier = r'(?:\[[^\]]+\]|"[^"]+"|`[^`]+`|[A-Za-z_][A-Za-z0-9_]*)'
    pattern = re.compile(
        rf"\b(?:FROM|JOIN)\s+({identifier}(?:\s*\.\s*{identifier})?)"
        rf"\s+(?:AS\s+)?p\b",
        flags=re.IGNORECASE,
    )
    return pattern.finditer(sql_before_reference)


def _sql_block_containing(source_text: str, position: int) -> str:
    """
    Return the triple-quoted SQL block containing a reference. In the handler,
    p.source_ref normally appears in SELECT before FROM, so the full block is
    required instead of looking only at text before the field reference.
    """
    triple_double = '"' * 3
    triple_single = "'" * 3
    left_double = source_text.rfind(triple_double, 0, position)
    left_single = source_text.rfind(triple_single, 0, position)

    if left_double == -1 and left_single == -1:
        # Fallback for a dynamically assembled one-line SQL query.
        return source_text[max(0, position - 12000): min(len(source_text), position + 12000)]

    quote = triple_double if left_double > left_single else triple_single
    start = max(left_double, left_single)
    end = source_text.find(quote, position)

    if end == -1:
        return source_text[start + len(quote): min(len(source_text), position + 12000)]

    return source_text[start + len(quote): end]


def find_p_alias_tables(source_text: str) -> list[str]:
    """
    Locate table names assigned to alias p in the SQL statement that reads
    p.source_ref. The traceback proves this is the exact incompatibility.
    """
    candidates: list[str] = []
    reference_pattern = re.compile(r"\bp\s*\.\s*source_ref\b", re.IGNORECASE)

    for reference in reference_pattern.finditer(source_text):
        sql_block = _sql_block_containing(source_text, reference.start())
        matches = list(_all_from_or_join_matches(sql_block))
        if not matches:
            continue

        # A SELECT can contain joins. The p alias is unique inside its SQL block;
        # preserve every distinct target to repair all genuinely referenced tables.
        for match in matches:
            table = normalize_table_identifier(match.group(1))
            if table not in candidates:
                candidates.append(table)

    return candidates


def existing_tables(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table'
          AND name NOT LIKE 'sqlite_%'
        """
    ).fetchall()
    return {str(row[0]) for row in rows}


def table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({quote_identifier(table_name)})").fetchall()
    return {str(row[1]) for row in rows}


def create_backup() -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = BACKUP_DIR / f"osbb_before_source_ref_{stamp}.db"
    shutil.copy2(DB_PATH, backup_path)
    return backup_path


def make_index_name(table_name: str) -> str:
    safe_table = re.sub(r"[^A-Za-z0-9_]+", "_", table_name).strip("_") or "table"
    return f"idx_{safe_table}_source_ref"


def repair_table(conn: sqlite3.Connection, table_name: str) -> bool:
    """Return True only when a schema change was applied."""
    columns_before = table_columns(conn, table_name)
    if "source_ref" in columns_before:
        emit(f"OK: table '{table_name}' already has column source_ref.")
        return False

    emit(f"Repairing: {table_name}.source_ref")
    conn.execute(
        f"ALTER TABLE {quote_identifier(table_name)} ADD COLUMN source_ref TEXT"
    )

    index_name = make_index_name(table_name)
    conn.execute(
        f"CREATE INDEX IF NOT EXISTS {quote_identifier(index_name)} "
        f"ON {quote_identifier(table_name)} ({quote_identifier('source_ref')})"
    )

    columns_after = table_columns(conn, table_name)
    if "source_ref" not in columns_after:
        raise RuntimeError(
            f"SQLite completed ALTER TABLE but {table_name}.source_ref is still absent."
        )

    # This is the minimal verification of the exact failed SQL capability.
    conn.execute(
        f"SELECT {quote_identifier('source_ref')} "
        f"FROM {quote_identifier(table_name)} LIMIT 1"
    ).fetchall()
    emit(f"PASS: table '{table_name}' now contains source_ref and is queryable.")
    return True


def main() -> int:
    emit("OSBB source_ref schema repair")
    emit("=" * 72)
    emit(f"Project: {PROJECT_ROOT}")
    emit(f"Handler: {HANDLER_PATH}")
    emit(f"Database: {DB_PATH}")

    if not HANDLER_PATH.is_file():
        raise FileNotFoundError(
            "Handler file was not found. Put this repair file directly into "
            r"G:\Programming\Py\OSBB\ and run it from there."
        )

    if not DB_PATH.is_file():
        raise FileNotFoundError(
            "Database was not found. Expected: "
            r"G:\Programming\Py\OSBB\Data\db\osbb.db"
        )

    source_text = read_python_source(HANDLER_PATH)
    candidates = find_p_alias_tables(source_text)

    if not candidates:
        raise RuntimeError(
            "Could not determine the table aliased as p from the SQL containing "
            "p.source_ref. No database change was made."
        )

    emit(f"Detected p.source_ref table candidate(s): {', '.join(candidates)}")

    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        actual_tables = existing_tables(conn)
        targets = [name for name in candidates if name in actual_tables]

        unknown = [name for name in candidates if name not in actual_tables]
        if unknown:
            emit(
                "WARNING: table name(s) found in source but absent in osbb.db: "
                + ", ".join(unknown)
            )

        if not targets:
            raise RuntimeError(
                "None of the table names detected in the handler exists in osbb.db. "
                "No database change was made."
            )

        missing = [
            name for name in targets
            if "source_ref" not in table_columns(conn, name)
        ]

        if not missing:
            emit("No repair needed: all detected table(s) already contain source_ref.")
            return 0

        backup_path = create_backup()
        emit(f"Backup created: {backup_path}")

        try:
            conn.execute("BEGIN IMMEDIATE")
            changed = False
            for table_name in targets:
                changed = repair_table(conn, table_name) or changed
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    if changed:
        emit("")
        emit("DONE: database schema has been repaired.")
        emit("Now start the Telegram bot again and repeat the same action.")
    else:
        emit("")
        emit("DONE: no schema change was necessary.")
    return 0


if __name__ == "__main__":
    try:
        exit_code = main()
    except Exception as exc:  # noqa: BLE001 - show exact operational reason in the log
        emit("")
        emit("FAILED: " + str(exc))
        flush_log()
        sys.exit(1)
    else:
        flush_log()
        sys.exit(exit_code)
