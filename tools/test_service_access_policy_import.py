#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
READ ONLY

Проверка импорта рабочего модуля service_access_policy.py
и вызова check_service_allowed().
"""

from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from service_access_policy import (
    check_service_allowed,
    result_to_short_text,
)

DB = Path(
    r"G:\Programming\Py\OSBB\Data\db\sandbox\osbb_test_live_services_2026-06-26_20-13-26.db"
)

print("=" * 88)
print("OSBB service_access_policy import test - READ ONLY")
print("=" * 88)
print("DB:", DB)
print()

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row

try:
    result = check_service_allowed(
        conn,
        apartment_number=89,
        service_code="TEST_REMOTE_NEW",
    )

    print("Decision :", result["decision"])
    print("Allowed  :", result["allowed"])
    print("Debt     :", result["debt"]["total"])
    print("Services :", result["debt"]["services"])
    print()
    print(result_to_short_text(result))

finally:
    conn.close()

print()
print("READ ONLY COMPLETED")