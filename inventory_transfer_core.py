"""Quantity ledger for physical goods moving between OSBB stock points.

Before individual remotes are issued, a supplier batch is counted as a lot.
Sending stock removes it from the sender's available balance.  The receiver's
balance increases only after a different person confirms physical receipt.
"""

from __future__ import annotations

from datetime import datetime
import sqlite3


LOCATIONS = {"CS": "Центральный склад", "O": "Пост охраны O", "K": "Консьерж K"}


def now_db() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def ensure_inventory_schema(conn: sqlite3.Connection) -> None:
    statements = [
        """CREATE TABLE IF NOT EXISTS inventory_locations (
            location_code TEXT PRIMARY KEY, location_name TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1)""",
        """CREATE TABLE IF NOT EXISTS inventory_lots (
            id INTEGER PRIMARY KEY, source_kind TEXT NOT NULL, source_id INTEGER NOT NULL,
            service_item_code TEXT NOT NULL, quantity_received INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            UNIQUE(source_kind, source_id))""",
        """CREATE TABLE IF NOT EXISTS inventory_balances (
            lot_id INTEGER NOT NULL, location_code TEXT NOT NULL,
            quantity INTEGER NOT NULL DEFAULT 0 CHECK(quantity >= 0),
            updated_at TEXT NOT NULL,
            PRIMARY KEY(lot_id, location_code),
            FOREIGN KEY(lot_id) REFERENCES inventory_lots(id),
            FOREIGN KEY(location_code) REFERENCES inventory_locations(location_code))""",
        """CREATE TABLE IF NOT EXISTS inventory_transfers (
            id INTEGER PRIMARY KEY, lot_id INTEGER NOT NULL,
            from_location_code TEXT NOT NULL, to_location_code TEXT NOT NULL,
            quantity INTEGER NOT NULL CHECK(quantity > 0),
            transfer_status TEXT NOT NULL DEFAULT 'SENT',
            sent_by TEXT NOT NULL, sent_at TEXT NOT NULL,
            received_by TEXT, received_at TEXT, note TEXT,
            reported_quantity INTEGER, reported_by TEXT, reported_at TEXT,
            FOREIGN KEY(lot_id) REFERENCES inventory_lots(id))""",
        """CREATE TABLE IF NOT EXISTS inventory_events (
            id INTEGER PRIMARY KEY, lot_id INTEGER NOT NULL, transfer_id INTEGER,
            service_order_id INTEGER, event_code TEXT NOT NULL,
            location_code TEXT NOT NULL, quantity_delta INTEGER NOT NULL,
            actor_id TEXT NOT NULL, note TEXT, created_at TEXT NOT NULL,
            FOREIGN KEY(lot_id) REFERENCES inventory_lots(id))""",
        "CREATE INDEX IF NOT EXISTS idx_inventory_transfers_status ON inventory_transfers(transfer_status, to_location_code)",
        "CREATE INDEX IF NOT EXISTS idx_inventory_events_lot ON inventory_events(lot_id, id)",
    ]
    for sql in statements:
        conn.execute(sql)
    for code, name in LOCATIONS.items():
        conn.execute(
            "INSERT OR IGNORE INTO inventory_locations(location_code,location_name) VALUES (?,?)",
            (code, name),
        )


def _lot(conn: sqlite3.Connection, batch_id: int) -> sqlite3.Row:
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM inventory_lots WHERE source_kind='REMOTE_SUPPLIER_BATCH' AND source_id=?",
        (int(batch_id),),
    ).fetchone()
    if row is None:
        raise ValueError("Поставка ещё не принята на учёт ЦС.")
    return row


def _balance(conn: sqlite3.Connection, lot_id: int, location: str) -> int:
    row = conn.execute(
        "SELECT quantity FROM inventory_balances WHERE lot_id=? AND location_code=?",
        (lot_id, location),
    ).fetchone()
    return int(row[0]) if row else 0


def _credit(conn: sqlite3.Connection, lot_id: int, location: str, quantity: int) -> None:
    conn.execute(
        """INSERT INTO inventory_balances(lot_id,location_code,quantity,updated_at)
           VALUES (?,?,?,?) ON CONFLICT(lot_id,location_code) DO UPDATE SET
           quantity=quantity+excluded.quantity,updated_at=excluded.updated_at""",
        (lot_id, location, quantity, now_db()),
    )


def _debit(conn: sqlite3.Connection, lot_id: int, location: str, quantity: int) -> None:
    cur = conn.execute(
        """UPDATE inventory_balances SET quantity=quantity-?,updated_at=?
           WHERE lot_id=? AND location_code=? AND quantity>=?""",
        (quantity, now_db(), lot_id, location, quantity),
    )
    if cur.rowcount != 1:
        raise ValueError(f"В {location} недостаточно пультов этой партии: доступно {_balance(conn, lot_id, location)}, нужно {quantity}.")


def _event(conn: sqlite3.Connection, lot_id: int, code: str, location: str,
           delta: int, actor: str | int, *, transfer_id: int | None = None,
           order_id: int | None = None, note: str = "") -> None:
    conn.execute(
        """INSERT INTO inventory_events(lot_id,transfer_id,service_order_id,event_code,
           location_code,quantity_delta,actor_id,note,created_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (lot_id, transfer_id, order_id, code, location, delta, str(actor), note or None, now_db()),
    )


def receive_supplier_stock(conn: sqlite3.Connection, *, batch_id: int,
                           service_item_code: str, quantity: int, actor_id: str | int) -> None:
    """Called in the same transaction that confirms receipt from supplier."""
    ensure_inventory_schema(conn)
    if quantity <= 0:
        raise ValueError("Количество должно быть положительным.")
    row = conn.execute(
        "SELECT id FROM inventory_lots WHERE source_kind='REMOTE_SUPPLIER_BATCH' AND source_id=?",
        (batch_id,),
    ).fetchone()
    if row:
        lot_id = int(row[0])
        conn.execute("UPDATE inventory_lots SET quantity_received=quantity_received+?,updated_at=? WHERE id=?",
                     (quantity, now_db(), lot_id))
    else:
        lot_id = int(conn.execute(
            """INSERT INTO inventory_lots(source_kind,source_id,service_item_code,quantity_received,created_at,updated_at)
               VALUES ('REMOTE_SUPPLIER_BATCH',?,?,?,?,?)""",
            (batch_id, service_item_code, quantity, now_db(), now_db()),
        ).lastrowid)
    _credit(conn, lot_id, "CS", quantity)
    _event(conn, lot_id, "SUPPLIER_RECEIVED", "CS", quantity, actor_id)


def send_transfer(conn: sqlite3.Connection, *, batch_id: int, from_location: str,
                  to_location: str, quantity: int, actor_id: str | int, note: str = "") -> int:
    ensure_inventory_schema(conn)
    from_location, to_location = from_location.upper(), to_location.upper()
    if from_location not in LOCATIONS or to_location not in LOCATIONS or from_location == to_location:
        raise ValueError("Выберите разные действующие пункты хранения: ЦС, O или K.")
    if quantity <= 0:
        raise ValueError("Количество должно быть положительным.")
    lot_id = int(_lot(conn, batch_id)["id"])
    _debit(conn, lot_id, from_location, quantity)
    transfer_id = int(conn.execute(
        """INSERT INTO inventory_transfers(lot_id,from_location_code,to_location_code,
           quantity,transfer_status,sent_by,sent_at,note)
           VALUES (?,?,?,?,'SENT',?,?,?)""",
        (lot_id, from_location, to_location, quantity, str(actor_id), now_db(), note or None),
    ).lastrowid)
    _event(conn, lot_id, "TRANSFER_SENT", from_location, -quantity, actor_id,
           transfer_id=transfer_id, note=note)
    return transfer_id


def confirm_transfer(conn: sqlite3.Connection, *, transfer_id: int, actor_id: str | int,
                     note: str = "") -> None:
    ensure_inventory_schema(conn)
    conn.row_factory = sqlite3.Row
    transfer = conn.execute("SELECT * FROM inventory_transfers WHERE id=?", (transfer_id,)).fetchone()
    if transfer is None or transfer["transfer_status"] != "SENT":
        raise ValueError("Ожидающая подтверждения передача не найдена.")
    if str(transfer["sent_by"]) == str(actor_id):
        raise ValueError("Отправитель не может подтвердить приём собственной передачи.")
    updated = conn.execute(
        """UPDATE inventory_transfers SET transfer_status='RECEIVED',received_by=?,received_at=?,
           note=COALESCE(?,note) WHERE id=? AND transfer_status='SENT'""",
        (str(actor_id), now_db(), note or None, transfer_id),
    )
    if updated.rowcount != 1:
        raise ValueError("Передача уже обработана другим оператором.")
    lot_id = int(transfer["lot_id"])
    location = str(transfer["to_location_code"])
    quantity = int(transfer["quantity"])
    _credit(conn, lot_id, location, quantity)
    _event(conn, lot_id, "TRANSFER_RECEIVED", location, quantity, actor_id,
           transfer_id=transfer_id, note=note)


def report_discrepancy(conn: sqlite3.Connection, *, transfer_id: int,
                       actual_quantity: int, actor_id: str | int, note: str = "") -> None:
    """Quarantine the full transfer until a separate reconciliation is made."""
    ensure_inventory_schema(conn)
    conn.row_factory = sqlite3.Row
    transfer = conn.execute("SELECT * FROM inventory_transfers WHERE id=?", (transfer_id,)).fetchone()
    if transfer is None or transfer["transfer_status"] != "SENT":
        raise ValueError("Ожидающая передача не найдена.")
    if str(transfer["sent_by"]) == str(actor_id):
        raise ValueError("Отправитель не может сверить собственную передачу.")
    actual_quantity = int(actual_quantity)
    if actual_quantity < 0 or actual_quantity == int(transfer["quantity"]):
        raise ValueError("Для совпадающего количества используйте подтверждение приёма.")
    updated = conn.execute(
        """UPDATE inventory_transfers SET transfer_status='DISPUTED',reported_quantity=?,
           reported_by=?,reported_at=?,note=? WHERE id=? AND transfer_status='SENT'""",
        (actual_quantity, str(actor_id), now_db(), note or "Расхождение при пересчёте", transfer_id),
    )
    if updated.rowcount != 1:
        raise ValueError("Передача уже обработана другим оператором.")
    _event(conn, int(transfer["lot_id"]), "TRANSFER_DISPUTED", str(transfer["to_location_code"]),
           0, actor_id, transfer_id=transfer_id,
           note=f"Отправлено {transfer['quantity']}, пересчитано {actual_quantity}. {note}")


def consume_for_order(conn: sqlite3.Connection, *, batch_id: int, location: str,
                      quantity: int, service_order_id: int, actor_id: str | int) -> None:
    """Debit goods from an accepted location when the operator issues an order."""
    ensure_inventory_schema(conn)
    location = location.upper()
    if location not in LOCATIONS or quantity <= 0:
        raise ValueError("Некорректный пункт выдачи или количество.")
    lot_id = int(_lot(conn, batch_id)["id"])
    _debit(conn, lot_id, location, quantity)
    _event(conn, lot_id, "ISSUED_TO_RESIDENT", location, -quantity, actor_id,
           order_id=service_order_id)


def stock_snapshot(conn: sqlite3.Connection) -> tuple[list[dict], list[dict]]:
    ensure_inventory_schema(conn)
    conn.row_factory = sqlite3.Row
    balances = [dict(row) for row in conn.execute(
        """SELECT b.lot_id,l.source_id AS batch_id,s.batch_number,l.service_item_code,
                  b.location_code,b.quantity
           FROM inventory_balances b JOIN inventory_lots l ON l.id=b.lot_id
           LEFT JOIN remote_supplier_batches s ON s.id=l.source_id AND l.source_kind='REMOTE_SUPPLIER_BATCH'
           WHERE b.quantity>0 ORDER BY s.id,b.location_code"""
    )]
    pending = [dict(row) for row in conn.execute(
        """SELECT t.id,t.lot_id,l.source_id AS batch_id,s.batch_number,t.from_location_code,
                  t.to_location_code,t.quantity,t.sent_by,t.sent_at,t.note,
                  t.transfer_status,t.reported_quantity,t.reported_by,t.reported_at
           FROM inventory_transfers t JOIN inventory_lots l ON l.id=t.lot_id
           LEFT JOIN remote_supplier_batches s ON s.id=l.source_id AND l.source_kind='REMOTE_SUPPLIER_BATCH'
           WHERE t.transfer_status IN ('SENT','DISPUTED') ORDER BY t.id"""
    )]
    return balances, pending
