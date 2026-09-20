#!/usr/bin/env python3
"""Refresh aggregated video-recognition evidence for operator verification.

The importer never changes ``vehicles``.  It materializes a compact, indexed
evidence cache from the read-only recognition workbooks, so the Streamlit
operator page can offer fast plate and model hints with full provenance.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import paths
from tools.standardize_video_recognition import DEFAULT_SOURCE_DIR, extract_file


ALGORITHM_VERSION = "video-evidence-v1"
ACTOR = "video_recognition_import"


def build_evidence(source_dir: Path) -> tuple[list[dict], str, int]:
    files = sorted(path for path in source_dir.glob("*.xlsx") if not path.name.startswith("~$"))
    observations: list[dict] = []
    for path in files:
        rows, _source = extract_file(path)
        observations.extend(rows)

    signature = hashlib.sha256(
        "\n".join(f"{path.name}:{path.stat().st_size}:{path.stat().st_mtime_ns}" for path in files).encode()
    ).hexdigest()

    by_plate: dict[str, list[dict]] = defaultdict(list)
    for row in observations:
        if row["plate_status"] == "STANDARD" and row["plate_normalized"]:
            by_plate[row["plate_normalized"]].append(row)

    evidence: list[dict] = []
    for plate, rows in sorted(by_plate.items()):
        models = Counter(row["make_model_normalized"] for row in rows if row["make_model_normalized"])
        total_models = sum(models.values())
        consensus_model = ""
        matching = 0
        agreement = None
        status = "NO_MODEL"
        if models:
            ordered = models.most_common()
            consensus_model, matching = ordered[0]
            tied = len(ordered) > 1 and ordered[1][1] == matching
            agreement = matching / total_models
            if tied:
                consensus_model = ""
                status = "CONFLICT"
            elif matching == 1:
                consensus_model = ""
                status = "ONE_OBSERVATION"
            elif agreement >= 0.75:
                status = "CONSENSUS"
            else:
                status = "DOMINANT_REVIEW"
        display_plate = Counter(row["plate"] for row in rows).most_common(1)[0][0]
        dates = sorted({row["video_date"] for row in rows if row["video_date"]})
        evidence.append({
            "plate_normalized": plate,
            "display_plate": display_plate,
            "consensus_model": consensus_model,
            "matching_observations": matching,
            "model_observations": total_models,
            "model_agreement": agreement,
            "evidence_status": status,
            "observation_count": len(rows),
            "first_seen_date": dates[0] if dates else None,
            "last_seen_date": dates[-1] if dates else None,
            "source_files_json": json.dumps(sorted({row["source_file"] for row in rows}), ensure_ascii=False),
        })
    return evidence, signature, len(observations)


def apply(source_dir: Path) -> dict:
    evidence, signature, observations_count = build_evidence(source_dir)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = sqlite3.connect(paths.OSBB_DB_FILE)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        cur.executescript("""
            CREATE TABLE IF NOT EXISTS video_plate_evidence (
                plate_normalized TEXT PRIMARY KEY,
                display_plate TEXT NOT NULL,
                consensus_model TEXT,
                matching_observations INTEGER NOT NULL,
                model_observations INTEGER NOT NULL,
                model_agreement REAL,
                evidence_status TEXT NOT NULL,
                observation_count INTEGER NOT NULL,
                first_seen_date TEXT,
                last_seen_date TEXT,
                source_files_json TEXT NOT NULL,
                registry_vehicle_id INTEGER,
                registry_model TEXT,
                registry_model_relation TEXT,
                source_signature TEXT NOT NULL,
                imported_at TEXT NOT NULL,
                algorithm_version TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_video_plate_evidence_status
                ON video_plate_evidence(evidence_status);
            CREATE TABLE IF NOT EXISTS video_recognition_imports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_signature TEXT NOT NULL,
                source_files_count INTEGER NOT NULL,
                observations_count INTEGER NOT NULL,
                evidence_rows_count INTEGER NOT NULL,
                imported_at TEXT NOT NULL,
                imported_by TEXT NOT NULL,
                algorithm_version TEXT NOT NULL
            );
        """)
        existing_columns = {row[1] for row in cur.execute("PRAGMA table_info(video_plate_evidence)")}
        for column, definition in (
            ("registry_vehicle_id", "INTEGER"),
            ("registry_model", "TEXT"),
            ("registry_model_relation", "TEXT"),
        ):
            if column not in existing_columns:
                cur.execute(f"ALTER TABLE video_plate_evidence ADD COLUMN {column} {definition}")
        registry = {
            row["license_plate_normalized"]: row
            for row in conn.execute(
                """SELECT id, license_plate_normalized, car_model, car_model_normalized
                   FROM vehicles WHERE license_plate_normalized IS NOT NULL"""
            ).fetchall()
        }
        for row in evidence:
            db_row = registry.get(row["plate_normalized"])
            row["registry_vehicle_id"] = db_row["id"] if db_row else None
            row["registry_model"] = (db_row["car_model"] or db_row["car_model_normalized"] or "") if db_row else ""
            if not row["registry_model"]:
                row["registry_model_relation"] = "NO_REGISTRY_MODEL"
            elif not row["consensus_model"]:
                row["registry_model_relation"] = "SUPPLEMENT_VIDEO"
            elif row["consensus_model"].upper() == row["registry_model"].upper():
                row["registry_model_relation"] = "MATCH"
            else:
                row["registry_model_relation"] = "DIFFERENT"
        cur.execute("DELETE FROM video_plate_evidence")
        cur.executemany(
            """
            INSERT INTO video_plate_evidence(
                plate_normalized, display_plate, consensus_model, matching_observations,
                model_observations, model_agreement, evidence_status, observation_count,
                first_seen_date, last_seen_date, source_files_json, source_signature,
                registry_vehicle_id, registry_model, registry_model_relation, imported_at, algorithm_version
            ) VALUES (
                :plate_normalized, :display_plate, :consensus_model, :matching_observations,
                :model_observations, :model_agreement, :evidence_status, :observation_count,
                :first_seen_date, :last_seen_date, :source_files_json, :source_signature,
                :registry_vehicle_id, :registry_model, :registry_model_relation, :imported_at, :algorithm_version
            )
            """,
            [row | {"source_signature": signature, "imported_at": timestamp, "algorithm_version": ALGORITHM_VERSION} for row in evidence],
        )
        cur.execute(
            """
            INSERT INTO video_recognition_imports(
                source_signature, source_files_count, observations_count, evidence_rows_count,
                imported_at, imported_by, algorithm_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (signature, len(list(source_dir.glob("*.xlsx"))), observations_count, len(evidence), timestamp, ACTOR, ALGORITHM_VERSION),
        )
        cur.execute(
            """
            INSERT INTO audit_log(
                event_time, username, table_name, record_id, action, field_name,
                old_value, new_value, comment, actor_role, actor_name, source, telegram_user_id
            ) VALUES (?, 'system', 'video_plate_evidence', 'refresh', 'refresh_video_evidence', '*',
                      '', ?, ?, 'system', ?, 'video_recognition', NULL)
            """,
            (
                timestamp,
                json.dumps({"observations": observations_count, "unique_plates": len(evidence), "source_signature": signature}, ensure_ascii=False),
                "Обновлён агрегированный кэш распознанных номеров для операторской верификации.",
                ACTOR,
            ),
        )
        conn.commit()
        return {"observations": observations_count, "unique_plates": len(evidence), "signature": signature}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Import aggregated video plate evidence into the OSBB DB.")
    parser.add_argument("--source-dir", type=Path, default=DEFAULT_SOURCE_DIR)
    parser.add_argument("--apply", action="store_true", help="Write the evidence cache to the main DB.")
    args = parser.parse_args()
    evidence, signature, observations = build_evidence(args.source_dir)
    print(f"Source files: {len(list(args.source_dir.glob('*.xlsx')))}; observations: {observations}; unique standard plates: {len(evidence)}")
    if not args.apply:
        print("Dry run only. Run with --apply to create/refresh the evidence cache.")
        return 0
    result = apply(args.source_dir)
    print(f"APPLIED: {result['observations']} observations, {result['unique_plates']} plates.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
