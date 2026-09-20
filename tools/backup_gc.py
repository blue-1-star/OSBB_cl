#!/usr/bin/env python3
"""Conservative retention manager for OSBB pre-migration SQLite backups.

Default mode only prints a plan.  ``--apply`` moves selected copies to the
local ``_retired`` folder, preserving a reversible seven-day quarantine before
they can be permanently removed by an explicit later decision.
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BACKUP_DIR = ROOT / "Data" / "db" / "backups"


@dataclass(frozen=True)
class Backup:
    path: Path
    modified_at: datetime
    size: int
    digest: str


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_backups(folder: Path) -> list[Backup]:
    return sorted(
        (
            Backup(
                path=path,
                modified_at=datetime.fromtimestamp(path.stat().st_mtime),
                size=path.stat().st_size,
                digest=sha256(path),
            )
            for path in folder.glob("before_*.db")
            if path.is_file()
        ),
        key=lambda item: item.modified_at,
        reverse=True,
    )


def retention_plan(backups: list[Backup], keep_recent: int) -> dict[Path, str]:
    """Return backup paths to retire and the reason for each decision."""
    retire: dict[Path, str] = {}
    by_hash: dict[str, list[Backup]] = defaultdict(list)
    for item in backups:
        by_hash[item.digest].append(item)

    unique: list[Backup] = []
    for copies in by_hash.values():
        copies.sort(key=lambda item: item.modified_at, reverse=True)
        unique.append(copies[0])
        for duplicate in copies[1:]:
            retire[duplicate.path] = f"точный дубликат {copies[0].path.name}"
    unique.sort(key=lambda item: item.modified_at, reverse=True)

    # Keep the N most recent distinct DB states unconditionally.
    protected = set(unique[:keep_recent])
    daily_seen: set[datetime.date] = set()
    monthly_seen: set[tuple[int, int]] = set()
    now = datetime.now()
    for item in unique[keep_recent:]:
        age = now - item.modified_at
        day_key = item.modified_at.date()
        month_key = (item.modified_at.year, item.modified_at.month)
        # Fresh development history is intentionally compact: only the three
        # latest states survive.  Daily/monthly checkpoints start later.
        if timedelta(days=14) < age <= timedelta(days=30) and day_key not in daily_seen:
            daily_seen.add(day_key)
            protected.add(item)
        elif timedelta(days=30) < age <= timedelta(days=180) and month_key not in monthly_seen:
            monthly_seen.add(month_key)
            protected.add(item)

    for item in unique:
        if item not in protected and item.path not in retire:
            retire[item.path] = "вне политики хранения"
    return retire


def main() -> int:
    parser = argparse.ArgumentParser(description="Plan or apply conservative OSBB backup retention.")
    parser.add_argument("--backup-dir", type=Path, default=DEFAULT_BACKUP_DIR)
    parser.add_argument("--keep-recent", type=int, default=3, help="Number of newest unique states to retain.")
    parser.add_argument("--apply", action="store_true", help="Move selected backups to _retired; default is dry run.")
    args = parser.parse_args()
    folder = args.backup_dir.resolve()
    if not folder.is_dir():
        raise SystemExit(f"Не найдена папка резервных копий: {folder}")
    if args.keep_recent < 1:
        raise SystemExit("--keep-recent должен быть не меньше 1.")

    backups = load_backups(folder)
    retire = retention_plan(backups, args.keep_recent)
    total_size = sum(item.size for item in backups)
    reclaim_size = sum(item.size for item in backups if item.path in retire)
    print(f"Backups scanned: {len(backups)}; size: {total_size / 1024 / 1024:.1f} MB")
    print(f"Keep recent unique states: {args.keep_recent}")
    print(f"Selected for retirement: {len(retire)}; {reclaim_size / 1024 / 1024:.1f} MB")
    for item in backups:
        action = f"RETIRE — {retire[item.path]}" if item.path in retire else "KEEP"
        print(f"{action:50} {item.modified_at:%Y-%m-%d %H:%M:%S} {item.path.name}")

    if not args.apply:
        print("DRY RUN ONLY — files were not moved.")
        return 0
    retired_dir = folder / "_retired" / datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    retired_dir.mkdir(parents=True, exist_ok=True)
    for path in retire:
        shutil.move(str(path), str(retired_dir / path.name))
    print(f"Moved {len(retire)} backups to {retired_dir}")
    print("They remain recoverable there; permanent deletion is intentionally not automated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
