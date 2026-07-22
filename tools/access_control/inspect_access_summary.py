#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import sqlite3
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DB = PROJECT_ROOT / "Data" / "db" / "osbb_test.db"

con = sqlite3.connect(str(DB))
con.row_factory = sqlite3.Row

print("=" * 100)
print("ACCESS CONTROL QUICK SUMMARY")
print("=" * 100)

for name in [
    "access_roles",
    "access_role_permissions",
    "access_user_roles",
    "access_user_permissions",
    "access_permissions",
]:
    exists = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    if not exists:
        print(f"{name}: MISSING")
        continue
    count = con.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
    print(f"{name}: {count}")

print()
print("Roles")
print("-" * 100)
if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='access_roles'").fetchone():
    for r in con.execute("SELECT role_code, role_name, is_active FROM access_roles ORDER BY role_code"):
        print(dict(r))

print()
print("Role permissions")
print("-" * 100)
if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='access_role_permissions'").fetchone():
    for r in con.execute("""
        SELECT role_code, resource, action, scope_type, scope_value, effect, is_active
        FROM access_role_permissions
        ORDER BY role_code, resource, action
        LIMIT 200
    """):
        print(dict(r))

con.close()
