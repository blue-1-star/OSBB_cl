#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
READ ONLY

Тест нового API service_access_policy v1.1
"""

from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from service_access_policy import (
    resolve_service_code,
    ensure_service_order_allowed,
    ServiceAccessDenied,
    result_to_short_text,
)

DB = Path(
    r"G:\Programming\Py\OSBB\Data\db\sandbox\osbb_test_live_services_2026-06-26_20-13-26.db"
)

print("=" * 88)
print("OSBB service_access_policy v1.1 test - READ ONLY")
print("=" * 88)
print()

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row

try:

    print("1. resolve_service_code()")
    print("-" * 40)

    r = resolve_service_code(
        conn,
        "TEST_REMOTE_NEW",
    )

    print(r)
    print()

    print("2. ensure_service_order_allowed()")
    print("-" * 40)

    try:

        result = ensure_service_order_allowed(
            conn=conn,
            apartment_number=89,
            service_item_code="TEST_REMOTE_NEW",
        )

        print("Unexpected ALLOW")
        print(result)

    except ServiceAccessDenied as ex:

        print("ServiceAccessDenied caught")
        print("Decision :", ex.result["decision"])
        print("Allowed  :", ex.result["allowed"])
        print()

        print(result_to_short_text(ex.result))

finally:
    conn.close()

print()
print("READ ONLY COMPLETED")