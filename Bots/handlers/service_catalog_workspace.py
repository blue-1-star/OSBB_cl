"""Telegram administration screen for the common OSBB service catalog."""

from __future__ import annotations

from pathlib import Path
import sys

from telegram import ReplyKeyboardMarkup, Update

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from access_control import has_permission
from service_catalog_admin_core import create_offer, describe_profile, list_offers, list_profiles, set_publication, change_price


MODULE = "service_catalog_admin"
ENTRY = "📚 Каталог услуг"
HOME = "🏠 Главное меню"
BACK = "⬅️ К каталогу"
STEP_BACK = "↩️ Назад на шаг"
ROUTE_ACCEPT = "✅ Этот маршрут подходит"
ROUTE_CHANGE = "🔁 Выбрать другой маршрут"
NEW = "➕ Новый вид товара / услуги"
PRICE = "💰 Изменить цену"
PUBLISH = "✅ Опубликовать"
UNPUBLISH = "⏸ Снять с публикации"


def kb(rows):
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def _state(states: dict, user_id: int, create: bool = False) -> dict | None:
    value = states.get(user_id)
    if isinstance(value, dict) and value.get("_module") == MODULE:
        return value
    if create:
        value = {"_module": MODULE}
        states[user_id] = value
        return value
    return None


def has_catalog_access(user_id: int | str) -> bool:
    return all(has_permission(user_id, resource, "MANAGE") for resource in (
        "service_catalog", "service_item_workflows", "service_price_versions"
    ))


def _profiles() -> dict[str, dict]:
    return {row["profile_code"]: row for row in list_profiles()}


def _matching_profiles(draft: dict) -> dict[str, dict]:
    """A known category code is an explicit constraint, not a free-text guess."""
    profiles = _profiles()
    category = str(draft.get("service_code") or "").strip().upper()
    matching = {code: row for code, row in profiles.items()
                if str(row["service_category"]).upper() == category}
    return matching or profiles


def _draft_summary(draft: dict) -> str:
    return (
        f"Категория: {draft.get('service_code', '—')} · {draft.get('catalog_name', '—')}\n"
        f"Позиция: {draft.get('item_code', '—')} · {draft.get('item_name', '—')}"
    )


async def _show_profile_picker(update: Update, state: dict) -> None:
    draft = state.setdefault("draft", {})
    profiles = _matching_profiles(draft)
    state.update(mode="new_profile", profiles=profiles)
    buttons = [[f"{p['profile_name']} · {code}"] for code, p in profiles.items()]
    category = str(draft.get("service_code") or "").strip().upper()
    filtered = all(str(p["service_category"]).upper() == category for p in profiles.values())
    explanation = (
        f"Для категории {category} показаны только подходящие маршруты."
        if filtered else
        "Для этой новой категории готового маршрута нет; показаны все настроенные варианты. "
        "Проверьте назначение маршрута перед продолжением."
    )
    await update.message.reply_text(
        f"📦 {draft.get('item_name', 'Новая позиция')} · {draft.get('item_code', '—')}\n"
        f"{explanation}\n\nВыберите маршрут заказа:",
        reply_markup=kb(buttons + [[STEP_BACK], [HOME]]),
    )


async def _show_profile_confirmation(update: Update, state: dict) -> None:
    draft = state["draft"]
    state["mode"] = "new_profile_confirm"
    await update.message.reply_text(
        f"📦 {draft['item_name']}\n\nВыбранный маршрут:\n"
        f"{describe_profile(draft['workflow_profile_code'])}\n\n"
        "Это нужный процесс для этой позиции?",
        reply_markup=kb([[ROUTE_ACCEPT], [ROUTE_CHANGE], [STEP_BACK], [HOME]]),
    )


def _orderable_offers() -> list[dict]:
    """Catalog editor is not a registry of historic monthly charges."""
    return [
        row for row in list_offers()
        if row.get("workflow_profile_code")
        and not str(row.get("service_item_code") or "").upper().startswith("TEST_")
    ]


async def show_catalog_workspace(update: Update, states: dict, user_id: int) -> None:
    if not has_catalog_access(user_id):
        await update.message.reply_text("⛔ Нет права управлять каталогом услуг.")
        return
    state = _state(states, user_id, create=True)
    state.clear(); state.update({"_module": MODULE, "mode": "home"})
    rows = _orderable_offers()
    buttons = [[NEW]]
    lines = [
        "📚 Каталог товаров и услуг", "",
        "Здесь только то, что житель может заказать. Парковка по месяцам и "
        "исторические начисления в этот каталог не добавляются.", "", "Позиции:"
    ]
    mapping = {}
    for row in rows:
        published = row["item_status"] == "active" and int(row["resident_request_enabled"] or 0) == 1
        label = f"{'🟢' if published else '⚪'} {row['service_item_name']} · {row['service_item_code']}"
        mapping[label] = row["service_item_code"]
        buttons.append([label])
        price = row["current_price"] if row["current_price"] is not None else row["amount_default"]
        lines.append(f"{'🟢' if published else '⚪'} {row['service_item_name']} — {price} {row['currency']}")
    state["offers"] = mapping
    buttons += [[HOME]]
    await update.message.reply_text("\n".join(lines), reply_markup=kb(buttons))


async def _show_card(update: Update, state: dict, code: str) -> None:
    row = next((r for r in _orderable_offers() if r["service_item_code"] == code), None)
    if not row:
        await update.message.reply_text("Позиция не найдена."); return
    state.update({"mode": "card", "item_code": code})
    published = row["item_status"] == "active" and int(row["resident_request_enabled"] or 0) == 1
    price = row["current_price"] if row["current_price"] is not None else row["amount_default"]
    body = (
        f"📦 {row['service_item_name']}\n\n"
        f"Код: {code}\nКатегория: {row['category'] or '—'}\n"
        f"Маршрут: {row['profile_name'] or row['workflow_profile_code']}\n"
        f"Цена: {price} {row['currency']}\n"
        f"Статус: {'опубликовано для жителей' if published else 'черновик'}"
    )
    action = UNPUBLISH if published else PUBLISH
    await update.message.reply_text(body, reply_markup=kb([[action, PRICE], [BACK], [HOME]]))


async def handle_service_catalog_text(update: Update, states: dict, user_id: int, message_text: str) -> bool:
    text = (message_text or "").strip()
    state = _state(states, user_id)
    if text == ENTRY:
        await show_catalog_workspace(update, states, user_id); return True
    if state is None:
        return False
    if not has_catalog_access(user_id):
        states.pop(user_id, None); await update.message.reply_text("⛔ Нет права управлять каталогом услуг."); return True
    if text == HOME:
        states.pop(user_id, None); return False
    mode = state.get("mode")
    if text in {STEP_BACK, BACK}:
        previous = {
            "new_catalog_name": ("new_service_code", "Код категории, например REMOTE:"),
            "new_item_code": ("new_catalog_name", "Название категории, например Пульты:"),
            "new_item_name": ("new_item_code", "Код позиции, например REMOTE_NEW:"),
            "new_profile": ("new_item_name", "Название для жителя, например Новый пульт:"),
        }.get(mode)
        if previous:
            state["mode"] = previous[0]
            await update.message.reply_text(
                f"Возвращаемся на предыдущий шаг.\n{_draft_summary(state.get('draft', {}))}\n\n{previous[1]}",
                reply_markup=kb([[STEP_BACK], [HOME]]),
            )
            return True
        if mode == "new_profile_confirm":
            await _show_profile_picker(update, state); return True
        if mode == "new_price":
            await _show_profile_confirmation(update, state); return True
        if mode == "price":
            await _show_card(update, state, state["item_code"]); return True
        await show_catalog_workspace(update, states, user_id); return True
    if mode == "home":
        if text == NEW:
            state.update({"mode": "new_service_code", "draft": {}})
            await update.message.reply_text("Это создание нового вида товара/услуги, а не очередного месяца начисления.\n\nКод категории, например REMOTE:", reply_markup=kb([[BACK], [HOME]])); return True
        code = (state.get("offers") or {}).get(text)
        if code:
            await _show_card(update, state, code); return True
        await update.message.reply_text("Выберите кнопку каталога."); return True
    if mode == "card":
        code = state["item_code"]
        if text in {PUBLISH, UNPUBLISH}:
            try:
                set_publication(actor_id=user_id, item_code=code, published=text == PUBLISH)
                await update.message.reply_text("Состояние публикации изменено.")
            except Exception as exc:
                await update.message.reply_text(f"⚠️ {exc}")
            await _show_card(update, state, code); return True
        if text == PRICE:
            state["mode"] = "price"; await update.message.reply_text("Введите новую цену в грн, например 500:", reply_markup=kb([[BACK], [HOME]])); return True
        await update.message.reply_text("Выберите действие кнопкой."); return True
    draft = state.setdefault("draft", {})
    if mode == "new_service_code":
        draft["service_code"] = text; state["mode"] = "new_catalog_name"; await update.message.reply_text("Название категории, например Пульты:"); return True
    if mode == "new_catalog_name":
        draft["catalog_name"] = text; state["mode"] = "new_item_code"; await update.message.reply_text("Код позиции, например REMOTE_NEW:"); return True
    if mode == "new_item_code":
        draft["item_code"] = text; state["mode"] = "new_item_name"; await update.message.reply_text("Название для жителя, например Новый пульт:"); return True
    if mode == "new_item_name":
        draft["item_name"] = text
        await _show_profile_picker(update, state)
        return True
    if mode == "new_profile":
        profiles = state.get("profiles") or {}
        selected = next((code for code, p in profiles.items() if text == f"{p['profile_name']} · {code}"), None)
        if not selected:
            await update.message.reply_text("Выберите маршрут заказа кнопкой."); return True
        draft["workflow_profile_code"] = selected
        draft["category"] = profiles[selected]["service_category"]
        await _show_profile_confirmation(update, state)
        return True
    if mode == "new_profile_confirm":
        if text == ROUTE_CHANGE:
            await _show_profile_picker(update, state)
            return True
        if text == ROUTE_ACCEPT:
            state["mode"] = "new_price"
            await update.message.reply_text(
                f"📦 {draft['item_name']} · {draft['item_code']}\n"
                f"Маршрут подтверждён: {state['profiles'][draft['workflow_profile_code']]['profile_name']}.\n\n"
                "Введите цену в грн, например 500:",
                reply_markup=kb([[STEP_BACK], [HOME]]),
            )
            return True
        await update.message.reply_text("Подтвердите маршрут или выберите другой.",
                                        reply_markup=kb([[ROUTE_ACCEPT], [ROUTE_CHANGE], [STEP_BACK], [HOME]]))
        return True
    if mode in {"new_price", "price"}:
        try:
            price = float(text.replace(",", "."))
        except ValueError:
            price = -1
        if price < 0:
            await update.message.reply_text("Введите неотрицательное число, например 500."); return True
        try:
            if mode == "price":
                change_price(actor_id=user_id, item_code=state["item_code"], price=price, note="Изменено в Telegram-каталоге")
                await update.message.reply_text("Новая версия цены сохранена. Старые заказы не изменены.")
                await _show_card(update, state, state["item_code"]); return True
            created = create_offer(actor_id=user_id, price=price, currency="UAH", description="",
                                   resident_request_enabled=False, **draft)
            await update.message.reply_text(f"Черновик {created['service_item_code']} создан. Проверьте и опубликуйте его.")
            await _show_card(update, state, created["service_item_code"]); return True
        except Exception as exc:
            await update.message.reply_text(f"⚠️ {exc}"); return True
    await show_catalog_workspace(update, states, user_id)
    return True
