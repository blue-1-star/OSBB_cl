"""
Правка cashbox_code одного платежа — на случай, если он был по ошибке
записан не в ту кассу (например, банковский платёж записан как
наличный, или наоборот).

Синхронно правит payments и связанную cashbox_operations, и
пересчитывает баланс ОБЕИХ касс — старой (откуда убрали сумму) и
новой (куда она реально попадает).

Использование:
    python3 fix_payment_cashbox.py PAYMENT_ID НОВЫЙ_КОД

Пример:
    python3 fix_payment_cashbox.py 154 BANK
    python3 fix_payment_cashbox.py 100 C

Целевая касса должна уже существовать в cashboxes — если её нет,
скрипт честно откажется и не будет ничего создавать вслепую.
"""

import sys
import sqlite3
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config import paths, USE_TEST_DB


def get_db_path():
    return paths.OSBB_TEST_DB_FILE if USE_TEST_DB else paths.OSBB_DB_FILE


def calculated_cashbox_balance(cur, code):
    cur.execute("SELECT initial_balance FROM cashboxes WHERE cashbox_code = ?", (code,))
    row = cur.fetchone()
    if not row:
        return None
    initial = float(row["initial_balance"] or 0)
    cur.execute(
        """
        SELECT COALESCE(SUM(
            CASE WHEN direction = 'in' THEN amount
                 WHEN direction = 'out' THEN -amount
                 ELSE 0 END
        ), 0) FROM cashbox_operations WHERE cashbox_code = ?
        """,
        (code,),
    )
    return round(initial + float(cur.fetchone()[0] or 0), 2)


def recalc_and_store_cashbox_balance(cur, code):
    balance = calculated_cashbox_balance(cur, code)
    if balance is not None:
        cur.execute(
            "UPDATE cashboxes SET current_balance = ?, updated_at = datetime('now') WHERE cashbox_code = ?",
            (balance, code),
        )
    return balance


def fix_cashbox_code(conn, payment_id: int, new_code: str):
    cur = conn.cursor()
    payment = cur.execute("SELECT * FROM payments WHERE id = ?", (payment_id,)).fetchone()
    if not payment:
        return None, "Платёж не найден."

    target = cur.execute("SELECT 1 FROM cashboxes WHERE cashbox_code = ?", (new_code,)).fetchone()
    if not target:
        return None, f"Касса '{new_code}' не заведена в cashboxes — сначала создайте её."

    old_code = payment["cashbox_code"]
    cbo_id = payment["cashbox_operation_id"] if "cashbox_operation_id" in payment.keys() else None

    cur.execute("UPDATE payments SET cashbox_code = ? WHERE id = ?", (new_code, payment_id))
    if cbo_id:
        cur.execute("UPDATE cashbox_operations SET cashbox_code = ? WHERE id = ?", (new_code, cbo_id))

    # payment_channel и payment_method оба дублируют смысл cashbox_code
    # (BANK/CASH и bank/cash соответственно) в отдельных полях payments —
    # подтверждено сверкой с реальными банковскими записями, держим
    # синхронно все три.
    pcols = payment.keys()
    if "payment_channel" in pcols:
        new_channel = "BANK" if new_code == "BANK" else "CASH"
        cur.execute("UPDATE payments SET payment_channel = ? WHERE id = ?", (new_channel, payment_id))
    if "payment_method" in pcols:
        new_method = "bank" if new_code == "BANK" else "cash"
        cur.execute("UPDATE payments SET payment_method = ? WHERE id = ?", (new_method, payment_id))

    old_balance = recalc_and_store_cashbox_balance(cur, old_code) if old_code else None
    new_balance = recalc_and_store_cashbox_balance(cur, new_code)
    conn.commit()

    return {
        "old_code": old_code, "new_code": new_code,
        "old_balance": old_balance, "new_balance": new_balance,
    }, None


def main():
    if len(sys.argv) < 3:
        print("Использование: python3 fix_payment_cashbox.py PAYMENT_ID НОВЫЙ_КОД")
        print("Пример: python3 fix_payment_cashbox.py 154 BANK")
        return

    payment_id = int(sys.argv[1])
    new_code = sys.argv[2]

    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    cur = conn.cursor()
    payment = cur.execute("SELECT * FROM payments WHERE id = ?", (payment_id,)).fetchone()
    if not payment:
        print(f"⚠ Платёж #{payment_id} не найден.")
        conn.close()
        return

    print(f"Платёж #{payment_id}: кв.{payment['apartment_number']}, {payment['amount']} грн")
    print(f"  Текущая касса: {payment['cashbox_code']}")
    print(f"  Новая касса: {new_code}")
    print()
    answer = input("Применить? (да/нет): ").strip().lower()
    if answer not in ("да", "yes", "y"):
        print("Отменено, ничего не изменено.")
        conn.close()
        return

    result, err = fix_cashbox_code(conn, payment_id, new_code)
    if err:
        print(f"❌ {err}")
    else:
        print(f"✅ Готово. Касса {result['old_code']}: остаток {result['old_balance']:.2f} грн")
        print(f"   Касса {result['new_code']}: остаток {result['new_balance']:.2f} грн")

    conn.close()


if __name__ == "__main__":
    main()
    ``