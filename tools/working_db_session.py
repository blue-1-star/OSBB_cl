#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
working_db_session.py

Creates and manages temporary OSBB working DB copies for acceptance tests.

Purpose:
- keep osbb_test.db as Golden Master;
- create a disposable working copy for pult/phone/access/payment tests;
- write a manifest;
- archive or delete the working DB at the end.

Default paths:
  Golden Master:
    G:\Programming\Py\OSBB\Data\db\osbb_test.db

  Working sessions:
    G:\Programming\Py\OSBB\Data\db\working\

Examples:

  Create new working DB:
    python .\OSBB\tools\working_db_session.py create --label pult_order_test

  Show current sessions:
    python .\OSBB\tools\working_db_session.py list

  Check one DB:
    python .\OSBB\tools\working_db_session.py status --db G:\Programming\Py\OSBB\Data\db\working\osbb_working_....

  Finish and delete:
    python .\OSBB\tools\working_db_session.py finish --db <path> --delete --apply

  Finish and archive:
    python .\OSBB\tools\working_db_session.py finish --db <path> --archive --apply

DRY RUN by default for finish. Use --apply to delete/archive.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

DEFAULT_ROOT = Path(r"G:\Programming\Py\OSBB")
DEFAULT_GOLDEN_DB = DEFAULT_ROOT / "Data" / "db" / "osbb_test.db"
DEFAULT_WORKING_DIR = DEFAULT_ROOT / "Data" / "db" / "working"
DEFAULT_ARCHIVE_DIR = DEFAULT_ROOT / "Data" / "db" / "working_archive"


KEY_TABLES = [
    "apartments",
    "vehicles",
    "bot_admins",
    "service_orders",
    "service_order_steps",
    "remote_requests",
    "remote_order_details",
    "phone_access_requests",
    "phone_access_request_points",
    "payments",
    "payment_allocations",
    "cashbox_operations",
    "operator_audit_log",
    "commercial_contracts",
]


def now_stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d_%H-%M-%S")


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def safe_label(label: str) -> str:
    out = []
    for ch in label.strip().lower():
        if ch.isalnum():
            out.append(ch)
        elif ch in (" ", "-", "_"):
            out.append("_")
    s = "".join(out).strip("_")
    return s or "test"


def connect_readonly(db: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)


def integrity(db: Path) -> str:
    try:
        with connect_readonly(db) as conn:
            row = conn.execute("PRAGMA integrity_check").fetchone()
            return row[0] if row else "no result"
    except Exception as e:
        return f"ERROR: {e}"


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return bool(row)


def db_snapshot(db: Path) -> dict:
    report = {
        "path": str(db),
        "exists": db.exists(),
        "size_bytes": db.stat().st_size if db.exists() else None,
        "integrity": None,
        "tables_count": None,
        "key_counts": {},
    }
    if not db.exists():
        return report

    report["integrity"] = integrity(db)

    with connect_readonly(db) as conn:
        tables = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
        ]
        report["tables_count"] = len(tables)

        for table in KEY_TABLES:
            if table_exists(conn, table):
                try:
                    report["key_counts"][table] = conn.execute(
                        f"SELECT COUNT(*) FROM {table}"
                    ).fetchone()[0]
                except Exception as e:
                    report["key_counts"][table] = f"ERROR: {e}"
            else:
                report["key_counts"][table] = None

    return report


def write_manifest(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def create_session(args: argparse.Namespace) -> int:
    golden = Path(args.golden_db)
    working_dir = Path(args.working_dir)
    label = safe_label(args.label)

    if not golden.exists():
        raise SystemExit(f"Golden DB not found: {golden}")

    working_dir.mkdir(parents=True, exist_ok=True)

    dst = working_dir / f"osbb_working_{label}_{now_stamp()}.db"
    manifest = dst.with_suffix(".manifest.json")
    note = dst.with_suffix(".README.md")

    print("=" * 100)
    print("OSBB working DB session: CREATE")
    print("=" * 100)
    print("Golden:", golden)
    print("Working:", dst)

    golden_report = db_snapshot(golden)
    print("Golden integrity:", golden_report["integrity"])
    print("Golden tables:", golden_report["tables_count"])

    if golden_report["integrity"] != "ok":
        raise SystemExit("Golden DB integrity is not ok. Refusing to create working copy.")

    shutil.copy2(golden, dst)

    working_report = db_snapshot(dst)
    print("Working integrity:", working_report["integrity"])
    print("Working tables:", working_report["tables_count"])

    data = {
        "session_type": "OSBB_WORKING_DB_SESSION",
        "created_at": now_text(),
        "label": label,
        "golden_db": str(golden),
        "working_db": str(dst),
        "status": "active",
        "golden_snapshot": golden_report,
        "working_initial_snapshot": working_report,
        "intended_use": args.purpose,
        "finish": None,
    }
    write_manifest(manifest, data)

    note.write_text(
        f"""# OSBB Working DB Session

Created: {now_text()}

Label: `{label}`

Golden DB:

`{golden}`

Working DB:

`{dst}`

Purpose:

{args.purpose}

## Rule

This DB is disposable.

Use it for acceptance testing only.

At the end run one of:

```powershell
python .\\OSBB\\tools\\working_db_session.py finish --db "{dst}" --delete --apply
```

or

```powershell
python .\\OSBB\\tools\\working_db_session.py finish --db "{dst}" --archive --apply
```
""",
        encoding="utf-8",
    )

    print("")
    print("CREATED")
    print("Manifest:", manifest)
    print("Note:", note)
    print("")
    print("Use this DB for the bot test:")
    print(dst)
    return 0


def list_sessions(args: argparse.Namespace) -> int:
    working_dir = Path(args.working_dir)
    archive_dir = Path(args.archive_dir)

    print("=" * 100)
    print("OSBB working DB sessions")
    print("=" * 100)

    for title, folder in [("ACTIVE", working_dir), ("ARCHIVE", archive_dir)]:
        print("")
        print(title)
        print("-" * 100)
        if not folder.exists():
            print("No folder:", folder)
            continue
        items = sorted(folder.glob("*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not items:
            print("No DB files.")
            continue
        for p in items:
            print(f"{p.name} | {p.stat().st_size / 1024 / 1024:.2f} MB | {datetime.fromtimestamp(p.stat().st_mtime):%Y-%m-%d %H:%M:%S}")
    return 0


def status_session(args: argparse.Namespace) -> int:
    db = Path(args.db)
    print("=" * 100)
    print("OSBB working DB session: STATUS")
    print("=" * 100)
    print("DB:", db)
    rep = db_snapshot(db)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    return 0


def finish_session(args: argparse.Namespace) -> int:
    db = Path(args.db)
    archive_dir = Path(args.archive_dir)

    if not db.exists():
        raise SystemExit(f"DB not found: {db}")

    if args.delete == args.archive:
        raise SystemExit("Choose exactly one: --delete or --archive")

    final_snapshot = db_snapshot(db)
    manifest = db.with_suffix(".manifest.json")

    print("=" * 100)
    print("OSBB working DB session: FINISH")
    print("=" * 100)
    print("Mode:", "APPLY" if args.apply else "DRY RUN")
    print("DB:", db)
    print("Action:", "archive" if args.archive else "delete")
    print("Integrity:", final_snapshot["integrity"])
    print("Tables:", final_snapshot["tables_count"])

    if not args.apply:
        print("")
        print("DRY RUN COMPLETED. Re-run with --apply to finish.")
        return 0

    if manifest.exists():
        data = json.loads(manifest.read_text(encoding="utf-8"))
    else:
        data = {"session_type": "OSBB_WORKING_DB_SESSION", "working_db": str(db)}

    data["status"] = "archived" if args.archive else "deleted"
    data["finish"] = {
        "finished_at": now_text(),
        "action": "archive" if args.archive else "delete",
        "final_snapshot": final_snapshot,
        "comment": args.comment,
    }

    if args.archive:
        archive_dir.mkdir(parents=True, exist_ok=True)
        dst_db = archive_dir / db.name
        dst_manifest = archive_dir / manifest.name
        shutil.move(str(db), str(dst_db))
        write_manifest(dst_manifest, data)
        if manifest.exists():
            manifest.unlink()
        note = db.with_suffix(".README.md")
        if note.exists():
            shutil.move(str(note), str(archive_dir / note.name))
        print("ARCHIVED:", dst_db)
    else:
        db.unlink()
        write_manifest(manifest, data)
        final_manifest = db.with_suffix(".finished.json")
        shutil.move(str(manifest), str(final_manifest))
        note = db.with_suffix(".README.md")
        if note.exists():
            note.unlink()
        print("DELETED:", db)
        print("Finish record:", final_manifest)

    print("")
    print("FINISH COMPLETED")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    common_defaults = {
        "golden_db": str(DEFAULT_GOLDEN_DB),
        "working_dir": str(DEFAULT_WORKING_DIR),
        "archive_dir": str(DEFAULT_ARCHIVE_DIR),
    }

    p = sub.add_parser("create")
    p.add_argument("--golden-db", default=common_defaults["golden_db"])
    p.add_argument("--working-dir", default=common_defaults["working_dir"])
    p.add_argument("--label", default="acceptance_test")
    p.add_argument("--purpose", default="Acceptance testing on disposable working DB.")
    p.set_defaults(func=create_session)

    p = sub.add_parser("list")
    p.add_argument("--working-dir", default=common_defaults["working_dir"])
    p.add_argument("--archive-dir", default=common_defaults["archive_dir"])
    p.set_defaults(func=list_sessions)

    p = sub.add_parser("status")
    p.add_argument("--db", required=True)
    p.set_defaults(func=status_session)

    p = sub.add_parser("finish")
    p.add_argument("--db", required=True)
    p.add_argument("--archive-dir", default=common_defaults["archive_dir"])
    p.add_argument("--delete", action="store_true")
    p.add_argument("--archive", action="store_true")
    p.add_argument("--comment", default="")
    p.add_argument("--apply", action="store_true")
    p.set_defaults(func=finish_session)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
