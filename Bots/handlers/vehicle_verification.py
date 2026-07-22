from datetime import datetime
from pathlib import Path
import sqlite3
import sys
import re

from telegram import Update, ReplyKeyboardMarkup

BOT_HANDLERS_DIR = Path(__file__).resolve().parent
BOTS_DIR = BOT_HANDLERS_DIR.parent
OSBB_ROOT = BOTS_DIR.parent
PY_ROOT = OSBB_ROOT.parent

for p in [str(OSBB_ROOT), str(PY_ROOT)]:
    if p not in sys.path:
        sys.path.insert(0, p)

from config import paths, USE_TEST_DB
from plate_consensus_report import DEFAULT_PERIOD_CODE, build_consensus, normalize_plate


VEHICLE_VERIFICATION_MENU = [
    ["▶️ Начать проверку авто", "📊 Сводка проверки авто"],
    ["⬅️ Назад", "🏠 Главное меню"],
]

def consensus_action_menu(item):
    rows = []
    variants = get_plate_variants(item)

    valid_count = 0
    for i, variant in enumerate(variants[:3], start=1):
        if is_valid_ua_plate(variant["plate"]):
            rows.append([f"✅ {variant['plate']}"])
            valid_count += 1

    rows.append(["✍️ Ввести номер вручную"])
    rows.append(["⏭ Отложить", "📊 Сводка проверки авто"])
    rows.append(["⬅️ Назад", "🏠 Главное меню"])

    return rows


def kb(rows):
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def get_db_file():
    return paths.OSBB_TEST_DB_FILE if USE_TEST_DB else paths.OSBB_DB_FILE


def table_exists(cur, table_name):
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table_name,))
    return cur.fetchone() is not None


def table_columns(cur, table_name):
    cur.execute(f"PRAGMA table_info({table_name})")
    return [row[1] for row in cur.fetchall()]


def insert_operator_audit(cur, operator_id, action_type, table_name, row_id, field_name,
                          old_value, new_value, source_context, comment=""):
    if not table_exists(cur, "operator_audit_log"):
        return

    columns = table_columns(cur, "operator_audit_log")
    values = {
        "operator_id": str(operator_id or ""),
        "user_id": str(operator_id or ""),
        "actor_type": "operator",
        "action_type": action_type,
        "table_name": table_name,
        "row_id": str(row_id or ""),
        "field_name": field_name,
        "old_value": str(old_value or ""),
        "new_value": str(new_value or ""),
        "action_status": "applied",
        "review_status": "pending",
        "source_context": source_context,
        "comment": comment,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    insert_cols = [k for k in values if k in columns]
    if not insert_cols:
        return

    placeholders = ",".join("?" for _ in insert_cols)
    cur.execute(
        f"INSERT INTO operator_audit_log ({', '.join(insert_cols)}) VALUES ({placeholders})",
        tuple(values[k] for k in insert_cols),
    )


def get_tie_tasks():
    consensus = build_consensus(period_code=DEFAULT_PERIOD_CODE, include_single=False)

    tasks = []

    for item in consensus:
        if item.get("status") != "tie":
            continue

        # Операторский режим исправляет именно бумажную/main базу vehicles.
        # Если paper-записи нет, как в случаях tbot vs tbot,
        # это не задача оператора "какой номер правильный", а задача супервизора:
        # возможно два разных авто, возможно дубль в tbot.
        if not paper_fact_for_item(item):
            continue

        tasks.append(item)

    return tasks


def get_tasks_cached(user_states, user_id, refresh=False):
    state = user_states.setdefault(user_id, {})
    if refresh or "vehicle_consensus_ties" not in state:
        state["vehicle_consensus_ties"] = get_tie_tasks()
        state["vehicle_consensus_index"] = 0
    return state["vehicle_consensus_ties"]


def current_task(user_states, user_id):
    state = user_states.setdefault(user_id, {})
    tasks = get_tasks_cached(user_states, user_id)
    if not tasks:
        return None, 0, 0

    idx = state.get("vehicle_consensus_index", 0)
    idx = max(0, min(idx, len(tasks) - 1))
    state["vehicle_consensus_index"] = idx
    return tasks[idx], idx, len(tasks)


def source_label(source):
    return {
        "paper": "бумажная база",
        "tbot": "бот",
        "payments": "платёж",
        "telegram": "Telegram",
        "video": "видео",
    }.get(source or "", source or "-")


def is_valid_ua_plate(plate):
    plate = normalize_plate(plate)
    return re.fullmatch(r"[A-Z]{2}\d{4}[A-Z]{2}", plate) is not None


def digits_count(plate):
    return len(re.sub(r"\D", "", normalize_plate(plate)))


def plate_warning(plate):
    plate = normalize_plate(plate)

    if is_valid_ua_plate(plate):
        return ""

    dcount = digits_count(plate)

    if dcount == 5:
        return "⚠️ 5 цифр — номер некорректный"

    if dcount < 4:
        return "⚠️ мало цифр — запись повреждена"

    if len(plate) < 8:
        return "⚠️ неполный номер"

    return "⚠️ не похож на полный номер"


def get_plate_variants(item):
    variants = []
    for row in item.get("vote_rows") or []:
        plate = row.get("plate") or ""
        if not plate:
            continue
        sources = ", ".join(source_label(s) for s in row.get("sources") or [])
        models = sorted({f.get("model") for f in row.get("facts") or [] if f.get("model")})
        variants.append({
            "plate": plate,
            "sources": sources,
            "models": ", ".join(models),
            "facts": row.get("facts") or [],
        })
    return variants


def paper_fact_for_item(item):
    for row in item.get("vote_rows") or []:
        for fact in row.get("facts") or []:
            if fact.get("source") == "paper":
                return fact
    return None


def format_consensus_task_for_operator(item, index=None, total=None):
    lines = []
    lines.append(f"🚗 Проверка авто {index}/{total}" if index is not None and total is not None else "🚗 Проверка авто")

    apartments = item.get("apartments") or []
    models = item.get("models") or []
    lines.append(f"Квартира: {', '.join(apartments) if apartments else '-'}")
    if models:
        lines.append(f"Авто: {', '.join(models[:3])}")

    lines.append("")
    lines.append("Разночтения номера:")

    variants = get_plate_variants(item)
    valid_variants = []

    if not variants:
        lines.append("нет вариантов")
    else:
        for i, variant in enumerate(variants[:3], start=1):
            model_tail = f" | {variant['models']}" if variant["models"] else ""
            warn = plate_warning(variant["plate"])
            warn_tail = f"\n   {warn}" if warn else ""
            lines.append(f"{i}. {variant['plate']} — {variant['sources']}{model_tail}{warn_tail}")

            if is_valid_ua_plate(variant["plate"]):
                valid_variants.append(variant)

    lines.append("")

    if valid_variants:
        lines.append("Выберите правильный полный номер.")
    else:
        lines.append("⚠️ Среди вариантов нет корректного полного номера.")
        lines.append("Нужна ручная проверка / видео / первичный документ.")

    lines.append("Если уверенности нет — нажмите ⏭ Отложить.")
    return "\n".join(lines)


def format_dashboard(tasks):
    lines = [
        "🚗 Проверка авто",
        "",
        "Показываются только спорные случаи paper ↔ источник.",
        "Случаи tbot ↔ tbot скрыты: это могут быть разные авто.",
        f"Осталось задач: {len(tasks)}",
        "",
    ]
    if tasks:
        by_apt = []
        for item in tasks[:10]:
            apartments = item.get("apartments") or ["-"]
            by_apt.append(", ".join(apartments))
        lines.append("Первые квартиры:")
        lines.append(", ".join(by_apt))
    lines.append("")
    lines.append("Нажмите ▶️ Начать проверку авто")
    return "\n".join(lines)


def parse_variant_index(text):
    for n in range(1, 10):
        if f" {n}" in text or text.strip().endswith(str(n)):
            return n - 1
    return None


def update_paper_vehicle_plate(vehicle_id, new_plate, operator_id, source_context):
    db_file = get_db_file()
    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("SELECT id, license_plate, license_plate_normalized FROM vehicles WHERE id = ?", (vehicle_id,))
    vehicle = cur.fetchone()
    if not vehicle:
        conn.close()
        raise RuntimeError(f"vehicle_id not found: {vehicle_id}")

    old_plate = vehicle["license_plate_normalized"] or vehicle["license_plate"] or ""
    new_plate_norm = normalize_plate(new_plate)

    vehicle_cols = table_columns(cur, "vehicles")
    sets, params = [], []
    if "license_plate" in vehicle_cols:
        sets.append("license_plate = ?")
        params.append(new_plate_norm)
    if "license_plate_normalized" in vehicle_cols:
        sets.append("license_plate_normalized = ?")
        params.append(new_plate_norm)

    if not sets:
        conn.close()
        raise RuntimeError("vehicles table has no license plate columns")

    params.append(vehicle_id)
    cur.execute(f"UPDATE vehicles SET {', '.join(sets)} WHERE id = ?", tuple(params))

    insert_operator_audit(
        cur=cur,
        operator_id=operator_id,
        action_type="update_vehicle_plate_from_consensus_tie",
        table_name="vehicles",
        row_id=vehicle_id,
        field_name="license_plate",
        old_value=old_plate,
        new_value=new_plate_norm,
        source_context=source_context,
        comment="Operator selected correct plate from consensus tie",
    )

    conn.commit()
    conn.close()
    return {"vehicle_id": vehicle_id, "old_plate": old_plate, "new_plate": new_plate_norm}


def audit_no_change(operator_id, item, selected_plate, comment):
    db_file = get_db_file()
    conn = sqlite3.connect(db_file)
    cur = conn.cursor()

    paper = paper_fact_for_item(item)
    vehicle_id = paper.get("ref_id") if paper else ""

    insert_operator_audit(
        cur=cur,
        operator_id=operator_id,
        action_type="confirm_consensus_tie_no_change",
        table_name="vehicles",
        row_id=vehicle_id,
        field_name="license_plate",
        old_value=selected_plate,
        new_value=selected_plate,
        source_context=f"apt={','.join(item.get('apartments') or [])}; selected_plate={selected_plate}",
        comment=comment,
    )

    conn.commit()
    conn.close()


def recount_after_change(user_states, user_id):
    state = user_states.setdefault(user_id, {})
    old_count = len(state.get("vehicle_consensus_ties") or [])
    new_tasks = get_tie_tasks()
    new_count = len(new_tasks)
    state["vehicle_consensus_ties"] = new_tasks
    state["vehicle_consensus_index"] = 0
    return old_count, new_count, max(0, old_count - new_count)


async def show_vehicle_verification_dashboard(update: Update, user_states=None, user_id=None):
    if user_states is None:
        user_states = {}
    if user_id is None:
        user_id = update.effective_user.id if update.effective_user else 0

    state = user_states.setdefault(user_id, {})
    state["mode"] = "vehicle_verification_consensus"
    tasks = get_tasks_cached(user_states, user_id, refresh=True)

    await update.message.reply_text(format_dashboard(tasks), reply_markup=kb(VEHICLE_VERIFICATION_MENU))


async def show_current_vehicle_task(update: Update, user_states, user_id):
    task, idx, total = current_task(user_states, user_id)
    if not task:
        await update.message.reply_text("Спорных задач проверки авто нет.", reply_markup=kb(VEHICLE_VERIFICATION_MENU))
        return

    await update.message.reply_text(
        format_consensus_task_for_operator(task, idx + 1, total),
        reply_markup=kb(consensus_action_menu(task)),
    )


def parse_selected_plate(text):
    text = (text or "").strip()
    if not text.startswith("✅"):
        return ""
    return normalize_plate(text.replace("✅", "", 1).strip())


def find_variant_index_by_plate(item, selected_plate):
    selected_plate = normalize_plate(selected_plate)
    for idx, variant in enumerate(get_plate_variants(item)):
        if normalize_plate(variant["plate"]) == selected_plate:
            return idx
    return None


async def choose_variant(update: Update, user_states, user_id, variant_index):
    task, idx, total = current_task(user_states, user_id)
    if not task:
        await update.message.reply_text("Нет текущей задачи.")
        return

    variants = get_plate_variants(task)
    if variant_index is None or variant_index < 0 or variant_index >= len(variants):
        await update.message.reply_text("Такого варианта нет в текущей задаче.")
        return

    selected = variants[variant_index]
    selected_plate = normalize_plate(selected["plate"])
    paper = paper_fact_for_item(task)

    if not paper:
        audit_no_change(user_id, task, selected_plate, "Operator selected plate, but no paper vehicle exists to update")
        state = user_states.setdefault(user_id, {})
        state["vehicle_consensus_index"] = idx + 1
        await update.message.reply_text(
            "✅ Выбор зафиксирован.\n\n"
            "В бумажной базе нет автомобиля для автоматического исправления.\n"
            "Эта задача оставлена для супервизора."
        )
        await show_current_vehicle_task(update, user_states, user_id)
        return

    vehicle_id = paper.get("ref_id")
    old_plate = normalize_plate(paper.get("plate"))
    if not str(vehicle_id).isdigit():
        await update.message.reply_text("Не удалось определить vehicle_id для бумажной базы. Задача оставлена.")
        return

    if old_plate == selected_plate:
        audit_no_change(user_id, task, selected_plate, "Operator confirmed existing paper plate")
        old_count, new_count, resolved = recount_after_change(user_states, user_id)
        await update.message.reply_text(
            "✅ Номер подтверждён без изменения базы.\n\n"
            f"vehicle_id: {vehicle_id}\n"
            f"номер: {selected_plate}\n\n"
            f"Решено задач: {resolved}\n"
            f"Осталось задач: {new_count}"
        )
        await show_current_vehicle_task(update, user_states, user_id)
        return

    result = update_paper_vehicle_plate(
        vehicle_id=int(vehicle_id),
        new_plate=selected_plate,
        operator_id=user_id,
        source_context=(
            f"consensus_tie; apt={','.join(task.get('apartments') or [])}; "
            f"old={old_plate}; selected={selected_plate}; sources={selected['sources']}"
        ),
    )

    old_count, new_count, resolved = recount_after_change(user_states, user_id)
    await update.message.reply_text(
        "✅ Номер исправлен\n\n"
        f"vehicle_id: {result['vehicle_id']}\n"
        f"было: {result['old_plate']}\n"
        f"стало: {result['new_plate']}\n\n"
        f"Решено задач: {resolved}\n"
        f"Осталось задач: {new_count}"
    )
    await show_current_vehicle_task(update, user_states, user_id)


async def skip_task(update: Update, user_states, user_id):
    task, idx, total = current_task(user_states, user_id)
    state = user_states.setdefault(user_id, {})
    state["vehicle_consensus_index"] = idx + 1
    await show_current_vehicle_task(update, user_states, user_id)


async def handle_vehicle_verification_text(update: Update, user_states, user_id, text):
    normalized = (text or "").strip()

    if normalized in {"🚗 Проверка авто", "🚗 Верификация авто"}:
        await show_vehicle_verification_dashboard(update, user_states, user_id)
        return True

    state = user_states.setdefault(user_id, {})
    in_mode = state.get("mode") == "vehicle_verification_consensus"

    if not in_mode and normalized not in {"▶️ Начать проверку авто", "📊 Сводка проверки авто"}:
        return False

    if normalized == "📊 Сводка проверки авто":
        await show_vehicle_verification_dashboard(update, user_states, user_id)
        return True

    if normalized == "▶️ Начать проверку авто":
        state["mode"] = "vehicle_verification_consensus"
        await show_current_vehicle_task(update, user_states, user_id)
        return True

    if normalized in {"⏭ Отложить", "⏭ Пропустить"}:
        await skip_task(update, user_states, user_id)
        return True

    if normalized.startswith("✅"):
        task, idx, total = current_task(user_states, user_id)
        selected_plate = parse_selected_plate(normalized)
        variant_index = find_variant_index_by_plate(task, selected_plate) if task else None

        if not selected_plate or variant_index is None:
            await update.message.reply_text("Не понял выбранный номер.")
            return True

        if not is_valid_ua_plate(selected_plate):
            await update.message.reply_text(
                "Этот вариант не похож на корректный полный номер. "
                "Лучше отложить или ввести номер вручную."
            )
            return True

        await choose_variant(update, user_states, user_id, variant_index)
        return True

    if normalized == "✍️ Ввести номер вручную":
        await update.message.reply_text(
            "Ручной ввод номера добавим следующим шагом. Сейчас можно отложить задачу."
        )
        return True

    return False
