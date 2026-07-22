import sqlite3
from pathlib import Path

# DB = Path(r"G:\Programming\Py\OSBB\Data\db\osbb_test.db")
from pathlib import Path
import sqlite3

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "Data" / "db" / "osbb_test.db"
TABLES = [
    "cashier_receipts",
    "cashbox_operations",
    "bank_transactions",
    "operator_audit_log",
    "vehicles",
]

con = sqlite3.connect(DB)

for table in TABLES:
    print("\n" + "=" * 100)
    print(table)
    print("=" * 100)

    row = con.execute(
        """
        SELECT sql
        FROM sqlite_master
        WHERE type = 'table' AND name = ?
        """,
        (table,),
    ).fetchone()

    print(row[0] if row and row[0] else "TABLE NOT FOUND")

con.close()