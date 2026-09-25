#!/usr/bin/env python3
"""Add Excel recognition reports from a monthly ZIP without replacing sources.

Only .xlsx reports are accepted. Existing names must have identical bytes;
the archive's manuals, executables, nested ZIPs and other files are ignored.
Run without --apply first to inspect the planned changes.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import tempfile
import zipfile
from collections import Counter
from pathlib import Path, PurePosixPath

from standardize_video_recognition import DEFAULT_SOURCE_DIR


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--source-dir", type=Path, default=DEFAULT_SOURCE_DIR)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    destination = args.source_dir.resolve()
    planned: list[tuple[str, str, bytes]] = []
    unchanged = []
    conflicts = []
    with zipfile.ZipFile(args.archive) as archive:
        entries = [item for item in archive.infolist() if item.filename.lower().endswith(".xlsx") and not item.is_dir()]
        names = Counter(PurePosixPath(item.filename).name.casefold() for item in entries)
        duplicate_names = sorted(name for name, count in names.items() if count > 1)
        if duplicate_names:
            raise ValueError(f"Repeated Excel names in archive: {duplicate_names}")
        for item in entries:
            path = PurePosixPath(item.filename)
            if path.is_absolute() or ".." in path.parts or not path.name or path.name.startswith("~$"):
                raise ValueError(f"Unsafe or temporary Excel entry: {item.filename}")
            data = archive.read(item)
            target = destination / path.name
            if target.exists():
                if digest(target.read_bytes()) == digest(data):
                    unchanged.append(path.name)
                else:
                    conflicts.append(path.name)
            else:
                planned.append((item.filename, path.name, data))
    print(f"Excel in archive: {len(entries)}; unchanged: {len(unchanged)}; new: {len(planned)}; conflicts: {len(conflicts)}")
    for name in conflicts:
        print(f"CONFLICT {name}")
    if conflicts:
        return 1
    if not args.apply:
        print("Dry run only. Use --apply to copy new Excel reports.")
        return 0
    destination.mkdir(parents=True, exist_ok=True)
    for archive_path, name, data in planned:
        with tempfile.NamedTemporaryFile(dir=destination, prefix=".video_import_", delete=False) as handle:
            temp_path = Path(handle.name)
            handle.write(data)
        try:
            if (destination / name).exists():
                raise FileExistsError(f"Destination appeared during import: {name}")
            os.link(temp_path, destination / name)
        finally:
            temp_path.unlink(missing_ok=True)
        print(f"ADDED {name} <- {archive_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
