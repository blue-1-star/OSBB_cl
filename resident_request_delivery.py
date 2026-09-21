"""Telegram delivery queue for resident-request questions and final decisions."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from config import paths, USE_TEST_DB


def _db_path() -> Path:
    return paths.OSBB_TEST_DB_FILE if USE_TEST_DB else paths.OSBB_DB_FILE


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _audit(cur: sqlite3.Cursor, message_id: int, action: str, old: str, new: str, note: str, user_id: str) -> None:
    cur.execute(
        """
        INSERT INTO audit_log(event_time, username, table_name, record_id, action, field_name,
            old_value, new_value, comment, actor_role, actor_name, source, telegram_user_id)
        VALUES (?, 'system', 'resident_request_messages', ?, ?, 'delivery_status', ?, ?, ?,
                'system', 'Telegram bot', 'resident_request_delivery', ?)
        """,
        (_now(), str(message_id), action, old, new, note, user_id),
    )


async def deliver_ready_resident_request_messages(bot, *, limit: int = 30, db_path: str | Path | None = None) -> dict:
    """Send queued operator messages. A failure stays visible and audited."""
    conn = sqlite3.connect(Path(db_path) if db_path else _db_path())
    conn.row_factory = sqlite3.Row
    result = {"selected": 0, "sent": 0, "failed": 0}
    try:
        cur = conn.cursor()
        if not cur.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='resident_request_messages'"
        ).fetchone():
            return result
        rows = cur.execute(
            """
            SELECT id, request_id, telegram_user_id, message_text,
                   COALESCE(message_kind, 'CLARIFICATION') AS message_kind
            FROM resident_request_messages
            WHERE direction='OPERATOR_TO_RESIDENT' AND delivery_status='READY'
            ORDER BY created_at, id LIMIT ?
            """, (int(limit),),
        ).fetchall()
        result["selected"] = len(rows)
        for row in rows:
            try:
                if row["message_kind"] == "RESOLUTION":
                    text = (
                        f"✅ Результат по заявке №{row['request_id']}\n\n{row['message_text']}\n\n"
                        "Результат сохранён в боте: «Мои изменения» → «История»."
                    )
                    audit_action = "resident_request_resolution_sent"
                    audit_note = "Результат рассмотрения отправлен жителю в Telegram."
                else:
                    text = (
                        f"📨 Уточнение по заявке №{row['request_id']}\n\n{row['message_text']}\n\n"
                        "Чтобы ответить, откройте в боте: «Мои изменения» → «Ожидающие» → «Ответить оператору»."
                    )
                    audit_action = "resident_request_question_sent"
                    audit_note = "Вопрос оператора отправлен в Telegram."
                await bot.send_message(
                    chat_id=int(row["telegram_user_id"]),
                    text=text,
                )
                cur.execute(
                    "UPDATE resident_request_messages SET delivery_status='SENT', sent_at=?, delivery_error=NULL WHERE id=?",
                    (_now(), row["id"]),
                )
                _audit(cur, row["id"], audit_action, "READY", "SENT", audit_note, row["telegram_user_id"])
                result["sent"] += 1
            except Exception as exc:
                cur.execute(
                    "UPDATE resident_request_messages SET delivery_status='FAILED', delivery_error=? WHERE id=?",
                    (str(exc)[:1000], row["id"]),
                )
                _audit(cur, row["id"], "resident_request_question_failed", "READY", "FAILED", str(exc)[:1000], row["telegram_user_id"])
                result["failed"] += 1
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
