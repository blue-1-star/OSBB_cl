"""Administrative catalogue of services and physical/digital offers."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from service_catalog_admin_core import change_price, create_offer, describe_profile, list_offers, list_profiles, set_publication


ACTOR = "STREAMLIT_CATALOG_MANAGER"
st.set_page_config(page_title="Каталог услуг", page_icon="📚", layout="wide")
st.title("📚 Каталог товаров и услуг")
st.caption("Черновики, публикация для жителей и версии цен. Парковочные месяцы и исторические начисления сюда не добавляются.")
st.warning("Это локальная админ-консоль без входа по Telegram-роли. В боте этот раздел доступен только роли SERVICE_CATALOG_MANAGER.")

profiles = list_profiles()
profile_labels = {p["profile_code"]: f"{p['profile_name']} · {p['service_category']}" for p in profiles}


def reset_route_confirmation() -> None:
    st.session_state["catalog_new_route_confirmed"] = False


with st.expander("➕ Создать новый вид товара / услуги", expanded=False):
    st.caption("Это не создание следующего месяца парковки. Новая позиция создаётся черновиком и публикуется отдельно.")
    st.markdown("**1. Что создаём**")
    c1, c2 = st.columns(2)
    with c1:
        service_code = st.text_input("Код категории", placeholder="REMOTE", key="catalog_new_service_code")
        catalog_name = st.text_input("Название категории", placeholder="Пульты", key="catalog_new_catalog_name")
    with c2:
        item_code = st.text_input("Код позиции", placeholder="REMOTE_NEW", key="catalog_new_item_code")
        item_name = st.text_input("Название для жителя", placeholder="Новый пульт", key="catalog_new_item_name")

    st.markdown("**2. Как исполняется заказ**")
    category_code = service_code.strip().upper()
    matching = [p for p in profiles if str(p["service_category"]).upper() == category_code]
    available_profiles = matching or (profiles if category_code else [])
    available_codes = [p["profile_code"] for p in available_profiles]
    if st.session_state.get("catalog_new_profile") not in available_codes:
        st.session_state["catalog_new_profile"] = None
        st.session_state["catalog_new_route_confirmed"] = False
    if matching:
        st.caption(f"Для {category_code} показаны только подходящие маршруты.")
    elif category_code:
        st.warning(
            f"Для кода {category_code} пока нет отдельного маршрута. Показаны все настроенные варианты; "
            "выберите подходящий тип исполнения осознанно."
        )
    else:
        st.caption("Сначала введите код категории; например, REMOTE покажет только маршруты пультов.")
    profile_code = st.selectbox(
        "Маршрут заказа", available_codes, index=None, placeholder="Выберите маршрут",
        format_func=lambda code: profile_labels.get(code, code), key="catalog_new_profile",
        on_change=reset_route_confirmation,
        help="После выбора покажем реальные шаги маршрута; телефонный доступ не подставляется автоматически.",
        disabled=not available_codes,
    )
    selected_profile = next((p for p in profiles if p["profile_code"] == profile_code), None)
    category = selected_profile["service_category"] if selected_profile else ""
    if profile_code:
        st.info(describe_profile(profile_code))
    confirmed = st.checkbox(
        "Этот маршрут подходит для создаваемой позиции",
        key="catalog_new_route_confirmed", disabled=not profile_code,
    )

    st.markdown("**3. Цена и создание черновика**")
    price = st.number_input("Цена", min_value=0.0, step=1.0, format="%.2f",
                            key="catalog_new_price", disabled=not confirmed)
    currency = st.selectbox("Валюта", ["UAH", "EUR", "USD"],
                            key="catalog_new_currency", disabled=not confirmed)
    description = st.text_area("Описание / примечание", placeholder="Что получает житель и важные условия.",
                               key="catalog_new_description", disabled=not confirmed)
    ready = bool(category_code and catalog_name.strip() and item_code.strip()
                 and item_name.strip() and profile_code and confirmed)
    if st.session_state.get("catalog_new_created_code") == item_code.strip().upper() and item_code.strip():
        ready = False
        st.success(f"Черновик {item_code.strip().upper()} уже создан. Он показан ниже; повторно создавать его не нужно.")
    submitted = st.button("Создать черновик", type="primary", disabled=not ready,
                          key="catalog_new_create")
    if submitted:
        try:
            created = create_offer(
                actor_id=ACTOR, service_code=service_code, catalog_name=catalog_name,
                category=category, item_code=item_code, item_name=item_name,
                workflow_profile_code=profile_code, price=price, currency=currency,
                description=description,
            )
            st.success(f"Черновик {created['service_item_code']} создан. Опубликуйте его после проверки.")
            st.session_state["catalog_new_created_code"] = created["service_item_code"]
        except Exception as exc:
            st.error(str(exc))

offers = [
    row for row in list_offers()
    if row.get("workflow_profile_code")
    and not str(row.get("service_item_code") or "").upper().startswith("TEST_")
]
if not offers:
    st.info("Заказываемые товары и услуги ещё не созданы.")
    st.stop()

st.markdown("### Позиции")


def display_price(row: dict) -> str:
    value = row["current_price"] if row["current_price"] is not None else row["amount_default"]
    return "—" if value is None else f"{float(value):.2f} {row['currency'] or 'UAH'}"


table = pd.DataFrame([{
    "Код": r["service_item_code"], "Категория": r["category"] or "—",
    "Позиция": r["service_item_name"], "Маршрут": r["profile_name"] or r["workflow_profile_code"],
    "Цена": display_price(r),
    "Статус": "Опубликовано" if r["item_status"] == "active" and r["resident_request_enabled"] else "Черновик",
    "Цена с": r["price_since"] or "—",
} for r in offers])
st.dataframe(table, hide_index=True, use_container_width=True)

options = {r["service_item_code"]: f"{r['service_item_name']} · {r['service_item_code']}" for r in offers}
selected_code = st.selectbox("Открыть позицию", list(options), format_func=options.get)
selected = next(r for r in offers if r["service_item_code"] == selected_code)

st.markdown(f"### {selected['service_item_name']}")
left, right = st.columns(2)
with left:
    st.write(f"**Категория:** {selected['catalog_name'] or selected['service_code']} (`{selected['category'] or '—'}`)")
    st.write(f"**Маршрут:** {selected['profile_name'] or selected['workflow_profile_code']}")
    st.write(f"**Описание:** {selected['description'] or '—'}")
with right:
    published = selected["item_status"] == "active" and int(selected["resident_request_enabled"] or 0) == 1
    st.write(f"**Состояние:** {'опубликовано для жителей' if published else 'черновик / скрыто'}")
    st.write(f"**Текущая цена:** {display_price(selected)}")
    st.write(f"**Действует с:** {selected['price_since'] or '—'}")

action_col, price_col = st.columns(2)
with action_col:
    label = "Снять с публикации" if published else "Опубликовать для жителей"
    if st.button(label, type="primary" if not published else "secondary", use_container_width=True):
        try:
            set_publication(actor_id=ACTOR, item_code=selected_code, published=not published)
            st.success("Состояние публикации изменено.")
            st.rerun()
        except Exception as exc:
            st.error(str(exc))
with price_col:
    with st.form("change_price"):
        new_price = st.number_input("Новая цена", min_value=0.0, value=float(selected["current_price"] if selected["current_price"] is not None else selected["amount_default"] or 0), step=1.0, format="%.2f")
        note = st.text_input("Основание изменения", placeholder="Решение правления / дата")
        if st.form_submit_button("Создать новую версию цены", use_container_width=True):
            try:
                change_price(actor_id=ACTOR, item_code=selected_code, price=new_price,
                             currency=selected["currency"], note=note)
                st.success("Новая цена сохранена; старые заказы не изменены.")
                st.rerun()
            except Exception as exc:
                st.error(str(exc))
