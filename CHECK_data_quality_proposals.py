"""Smoke-test the two-stage correction flow against a temporary DB copy."""

from __future__ import annotations

from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from unittest.mock import patch

from config import paths
from data_quality_proposals import (
    approve_proposal, create_proposal, get_proposal,
    reply_to_clarification, request_clarification,
)


def main() -> None:
    with TemporaryDirectory(prefix="osbb-quality-") as folder:
        test_db = Path(folder) / "quality.db"
        with sqlite3.connect(paths.OSBB_TEST_DB_FILE) as source, sqlite3.connect(test_db) as target:
            source.backup(target)

        def test_conn() -> sqlite3.Connection:
            conn = sqlite3.connect(test_db)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys=ON")
            return conn

        with patch("data_quality_proposals.get_conn", side_effect=test_conn):
            task_id = create_proposal(
                rule_code="VEHICLE_COLOR", object_id=88, apartment="160",
                proposed_value="Сірий", evidence="Тестовая анкета", actor="telegram:1046150340",
                telegram_user_id="1046150340",
            )
            with test_conn() as conn:
                assert conn.execute("SELECT car_color FROM vehicles WHERE id=88").fetchone()[0] is None
            try:
                approve_proposal(task_id=task_id, reviewer_id="1046150340", note="Не админ")
            except PermissionError:
                pass
            else:
                raise AssertionError("Non-super-admin approved a proposal")
            request_clarification(task_id=task_id, reviewer_id="210312208", question="Как проверено?")
            assert get_proposal(task_id)["status"] == "NEEDS_CLARIFICATION"
            reply_to_clarification(task_id=task_id, actor="telegram:1046150340",
                                   telegram_user_id="1046150340", reply="По бумажной анкете")
            assert get_proposal(task_id)["status"] == "PENDING"
            approve_proposal(task_id=task_id, reviewer_id="210312208", note="Анкета проверена")
            assert get_proposal(task_id)["status"] == "RESOLVED"
            with test_conn() as conn:
                assert conn.execute("SELECT car_color FROM vehicles WHERE id=88").fetchone()[0] == "Сірий"
                assert conn.execute(
                    "SELECT COUNT(*) FROM operator_audit_log WHERE row_id=? AND action_type='quality_proposal_approved'",
                    ("88",),
                ).fetchone()[0] == 1
        print("PASS: proposal, permission denial, clarification, reply, approval and audit")


if __name__ == "__main__":
    main()
