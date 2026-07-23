#!/usr/bin/env python3
"""
compare_db.py
===============

Честно отвечает на вопрос "эти два файла БД одинаковые или нет?" —
без догадок и "мне кажется".

Два уровня проверки:
  1) Побайтовое сравнение файлов (SHA-256). Если хэши совпали —
     файлы АБСОЛЮТНО идентичны, дальше можно не читать.
  2) Если хэши разные — детальное сравнение по таблицам:
     - какие таблицы есть только в одном файле;
     - у каких таблиц отличается количество строк;
     - у каких таблиц количество строк совпадает, но содержимое
       всё равно другое (сравнение по хэшу содержимого, без учёта
       порядка строк).

Ничего не меняет — оба файла открываются в режиме read-only.

Как запускать:

    python compare_db.py --db1 "G:\\Programming\\Py\\OSBB\\Data\\db\\osbb_test.db" --db2 "G:\\Programming\\OSBB_cl\\Data\\db\\osbb_test.db"

Можно указать --tables-only "access_permissions,access_roles,bot_admins"
чтобы проверить только конкретные таблицы (быстрее, если файлы большие
и интересует не всё, а конкретный участок, например права доступа).
"""

import argparse
import hashlib
import sqlite3
import sys
from pathlib import Path


def sha256_of_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def connect_ro(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)


def get_tables(conn: sqlite3.Connection) -> set:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {r[0] for r in rows}


def row_count(conn: sqlite3.Connection, table: str) -> int:
    return conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]


def content_hash(conn: sqlite3.Connection, table: str) -> str:
    """Хэш содержимого таблицы, независимый от порядка строк:
    берём все строки как строки, сортируем, хэшируем список."""
    rows = conn.execute(f'SELECT * FROM "{table}"').fetchall()
    row_strs = sorted(repr(r) for r in rows)
    h = hashlib.sha256()
    for r in row_strs:
        h.update(r.encode("utf-8", errors="replace"))
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db1", required=True, help="Первый файл БД (например, старый OSBB)")
    ap.add_argument("--db2", required=True, help="Второй файл БД (например, OSBB_cl)")
    ap.add_argument("--tables-only", default=None, help="Через запятую: проверить только эти таблицы (по умолчанию — все)")
    args = ap.parse_args()

    path1 = Path(args.db1).resolve()
    path2 = Path(args.db2).resolve()

    for p in (path1, path2):
        if not p.exists():
            print(f"Файл не найден: {p}", file=sys.stderr)
            sys.exit(1)

    print(f"DB1: {path1}  ({path1.stat().st_size / 1024:.1f} КБ)")
    print(f"DB2: {path2}  ({path2.stat().st_size / 1024:.1f} КБ)")
    print()

    print("Шаг 1: побайтовое сравнение (SHA-256)...")
    hash1 = sha256_of_file(path1)
    hash2 = sha256_of_file(path2)
    print(f"  DB1: {hash1}")
    print(f"  DB2: {hash2}")

    if hash1 == hash2:
        print("\n✅ ФАЙЛЫ ПОБАЙТОВО ИДЕНТИЧНЫ. Дальше можно не проверять.")
        return

    print("\n⚠ Файлы РАЗЛИЧАЮТСЯ побайтово. Это может быть как реальная разница")
    print("  в данных, так и просто разное состояние журнала/метаданных SQLite")
    print("  при идентичных данных. Проверяю по таблицам...\n")

    conn1 = connect_ro(path1)
    conn2 = connect_ro(path2)

    try:
        tables1 = get_tables(conn1)
        tables2 = get_tables(conn2)

        only_in_1 = tables1 - tables2
        only_in_2 = tables2 - tables1
        common = tables1 & tables2

        if args.tables_only:
            wanted = {t.strip() for t in args.tables_only.split(",")}
            common = common & wanted
            only_in_1 = only_in_1 & wanted
            only_in_2 = only_in_2 & wanted

        if only_in_1:
            print(f"Таблицы только в DB1 ({len(only_in_1)}): {sorted(only_in_1)}")
        if only_in_2:
            print(f"Таблицы только в DB2 ({len(only_in_2)}): {sorted(only_in_2)}")

        print(f"\nОбщих таблиц для сравнения: {len(common)}")
        print()

        diff_count_tables = []
        diff_content_tables = []
        identical_tables = []

        for table in sorted(common):
            c1 = row_count(conn1, table)
            c2 = row_count(conn2, table)
            if c1 != c2:
                diff_count_tables.append((table, c1, c2))
                continue
            if c1 == 0:
                identical_tables.append(table)
                continue
            h1 = content_hash(conn1, table)
            h2 = content_hash(conn2, table)
            if h1 == h2:
                identical_tables.append(table)
            else:
                diff_content_tables.append((table, c1))

        print(f"Идентичны (строк поровну и содержимое совпадает): {len(identical_tables)}")

        if diff_count_tables:
            print(f"\n⚠ РАЗНОЕ КОЛИЧЕСТВО СТРОК ({len(diff_count_tables)}):")
            for table, c1, c2 in diff_count_tables:
                print(f"  {table:35s} DB1: {c1:6d}   DB2: {c2:6d}   разница: {c2 - c1:+d}")

        if diff_content_tables:
            print(f"\n⚠ СТРОК ПОРОВНУ, НО СОДЕРЖИМОЕ ОТЛИЧАЕТСЯ ({len(diff_content_tables)}):")
            for table, c in diff_content_tables:
                print(f"  {table:35s} строк: {c}")

        if not diff_count_tables and not diff_content_tables:
            print("\n✅ Все общие таблицы идентичны по данным (файлы отличаются")
            print("   только на уровне метаданных SQLite, не по содержимому).")

    finally:
        conn1.close()
        conn2.close()


if __name__ == "__main__":
    main()