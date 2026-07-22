from pathlib import Path
import sqlite3
import sys

from telegram import Update, ReplyKeyboardMarkup

BOT_HANDLERS_DIR = Path(__file__).resolve().parent
BOTS_DIR = BOT_HANDLERS_DIR.parent
OSBB_ROOT = BOTS_DIR.parent
PY_ROOT = OSBB_ROOT.parent

for p in [str(OSBB_ROOT), str(PY_ROOT)]:
    if p not in sys.path:
        sys.path.insert(0, p)

from config import paths, USE_TEST_DB


VEHICLE_FULL_LIST_MENU = [
    ["📋 Все автомобили"],
    ["🔎 Найти авто"],
    ["⬅️ Назад", "🏠 Главное меню"],
]


def kb(rows):
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def get_db_file():
    return paths.OSBB_TEST_DB_FILE if USE_TEST_DB else paths.OSBB_DB_FILE


def get_conn():
    conn = sqlite3.connect(get_db_file())
    conn.row_factory = sqlite3.Row
    return conn


def get_all_vehicles_full():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            v.id AS vehicle_id,
            a.apartment_number,
            COALESCE(v.license_plate_normalized, v.license_plate, '') AS plate,
            COALESCE(v.car_model_normalized, v.car_model, '') AS model,
            COALESCE(v.parking_time, '') AS parking_time
        FROM vehicles v
        JOIN apartments a
            ON a.id = v.apartment_id
        ORDER BY
            CASE
                WHEN a.apartment_number GLOB '[0-9]*'
                THEN CAST(a.apartment_number AS INTEGER)
                ELSE 999999
            END,
            a.apartment_number,
            v.id
    """)

    rows = [dict(row) for row in cur.fetchall()]
    conn.close()
    return rows


def format_vehicle_line(row):
    plate = row.get("plate") or "-"
    model = row.get("model") or "-"
    tariff = row.get("parking_time") or "NULL"
    return f"• кв.{row['apartment_number']} | {plate} | {model} | {tariff}"


def build_full_vehicle_messages(rows, chunk_size=45):
    total = len(rows)

    if total == 0:
        return ["🚗 Все автомобили\n\nСписок пуст."]

    messages = []

    for start in range(0, total, chunk_size):
        part = rows[start:start + chunk_size]
        end = start + len(part)

        lines = [
            "🚗 Все автомобили",
            f"Показано: {start + 1}–{end} из {total}",
            "",
        ]

        for row in part:
            lines.append(format_vehicle_line(row))

        messages.append("\n".join(lines))

    return messages


async def send_full_vehicle_list(update: Update, reply_markup=None):
    rows = get_all_vehicles_full()
    messages = build_full_vehicle_messages(rows)

    for i, message in enumerate(messages):
        if i == len(messages) - 1:
            await update.message.reply_text(
                message,
                reply_markup=reply_markup or kb(VEHICLE_FULL_LIST_MENU),
            )
        else:
            await update.message.reply_text(message)


async def handle_vehicle_full_list_text(update: Update, user_states, user_id, text):
    normalized = (text or "").strip()

    if normalized == "📋 Все автомобили":
        await send_full_vehicle_list(
            update,
            reply_markup=kb(VEHICLE_FULL_LIST_MENU),
        )
        return True

    return False
