"""Focused smoke test for CS/O/K stock conservation; run from the project root."""

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from inventory_transfer_core import (
    confirm_transfer,
    consume_for_order,
    receive_supplier_stock,
    report_discrepancy,
    send_transfer,
    stock_snapshot,
)


def test_stock_transfer_and_discrepancy() -> None:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE remote_supplier_batches(id INTEGER PRIMARY KEY, batch_number TEXT)")
    conn.execute("INSERT INTO remote_supplier_batches VALUES (?,?)", (1, "TEST-LOT"))

    receive_supplier_stock(conn, batch_id=1, service_item_code="REMOTE_NEW",
                           quantity=8, actor_id="supplier")
    transfer_o = send_transfer(conn, batch_id=1, from_location="CS", to_location="O",
                               quantity=5, actor_id="operator")
    try:
        confirm_transfer(conn, transfer_id=transfer_o, actor_id="operator")
    except ValueError:
        pass
    else:
        raise AssertionError("Sender confirmed own transfer")
    confirm_transfer(conn, transfer_id=transfer_o, actor_id="guard")
    consume_for_order(conn, batch_id=1, location="O", quantity=2,
                      service_order_id=7, actor_id="operator")

    transfer_k = send_transfer(conn, batch_id=1, from_location="CS", to_location="K",
                               quantity=2, actor_id="operator")
    report_discrepancy(conn, transfer_id=transfer_k, actual_quantity=1, actor_id="concierge")
    balances, pending = stock_snapshot(conn)
    assert {(row["location_code"], row["quantity"]) for row in balances} == {
        ("CS", 1), ("O", 3),
    }
    assert len(pending) == 1 and pending[0]["transfer_status"] == "DISPUTED"
    assert pending[0]["reported_quantity"] == 1

    transfer_back = send_transfer(conn, batch_id=1, from_location="O", to_location="CS",
                                  quantity=1, actor_id="guard")
    confirm_transfer(conn, transfer_id=transfer_back, actor_id="operator")
    balances, _ = stock_snapshot(conn)
    assert {(row["location_code"], row["quantity"]) for row in balances} == {
        ("CS", 2), ("O", 2),
    }
    conn.close()


if __name__ == "__main__":
    test_stock_transfer_and_discrepancy()
    print("CS/O/K stock transfer test OK")
