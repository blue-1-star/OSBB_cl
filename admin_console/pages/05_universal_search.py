# G:\Programming\OSBB_cl\admin_console\pages\05_universal_search.py
"""
Универсальный поиск: ФИО / номер авто / квартира / телефон — одним полем.

Ввод не типизируется вручную (не нужен переключатель "что я ввожу") —
пробуем все четыре источника параллельно и объединяем результат по
apartment_id. Опирается на уже существующие функции, не дублирует их:

    query_lib.queries.find_by_fio             — ФИО (с фолдингом укр/рус букв)
    query_lib.queries.find_by_plate_fragment   — номер авто (фрагмент)
    query_lib.queries.find_by_phone            — телефон (НОВОЕ — см.
                                                  find_by_phone_patch.py,
                                                  добавить в queries.py)
    cashier_search.apartment_by_number         — точный номер квартиры
    cashier_search.vehicles_for_apartment      — авто квартиры

Если по любому из четырёх путей находится квартира — показываем её
полную карточку (жильцы + авто), а не просто строку результата.
"""

import sys
from pathlib import Path

STREAMLIT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(STREAMLIT_ROOT))

import streamlit as st
from utils.db import get_conn  # импорт первым — вставляет PROJECT_ROOT (OSBB_cl) в sys.path

# cashier_search.py физически лежит НЕ в корне OSBB_cl (в отличие от
# cashier_v2_core.py и query_lib/), а вложен в tools/cashier_v2_telegram/.
# Добавляем эту папку в sys.path отдельно — иначе ModuleNotFoundError.
PROJECT_ROOT = STREAMLIT_ROOT.parent  # OSBB_cl
TOOLS_CASHIER_DIR = PROJECT_ROOT / "tools" / "cashier_v2_telegram"
if str(TOOLS_CASHIER_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_CASHIER_DIR))

# После utils.db (и добавления TOOLS_CASHIER_DIR выше) — оба модуля доступны
from query_lib.queries import find_by_fio, find_by_plate_fragment, find_by_phone
from cashier_search import apartment_by_number, vehicles_for_apartment


st.set_page_config(page_title="Поиск жильца", layout="wide")
st.title("🔎 Поиск: ФИО / авто / квартира / телефон")

query = st.text_input(
    "Введите что угодно",
    placeholder="Іваненко, або AA1234BB, або 105, або 0671234567",
    help="Не нужно указывать, что именно вы вводите — система попробует все варианты",
)

if not query.strip():
    st.info("ℹ️ Введите ФИО, номер авто, номер квартиры или телефон")
    st.stop()


# ==========================================
# ПОИСК ПО КВАРТИРЕ (точный номер) — отдельная ветка, у неё другая форма
# результата, чем у трёх остальных (apartment_by_number ожидает open conn)
# ==========================================

def apartment_matches(q: str) -> list[dict]:
    conn = get_conn()
    try:
        out = []
        for apt in apartment_by_number(conn, q):
            persons = conn.execute(
                "SELECT full_name, phone_raw, person_role FROM persons WHERE apartment_id=?",
                (apt["id"],),
            ).fetchall()
            vehicles = vehicles_for_apartment(conn, apt["id"])
            out.append({
                "apartment_id": apt["id"],
                "квартира": apt.get("apartment_number"),
                "жильцы": [dict(p) for p in persons],
                "авто": [dict(v) for v in vehicles],
                "_source": "квартира (точно)",
            })
        return out
    finally:
        conn.close()


# ==========================================
# ЗАПУСК ВСЕХ ЧЕТЫРЁХ ПОИСКОВ
# ==========================================

with st.spinner("Ищу по ФИО, авто, квартире и телефону..."):
    by_apartment = apartment_matches(query)
    by_fio = find_by_fio(query)
    by_plate = find_by_plate_fragment(query)
    by_phone = find_by_phone(query)

total_hits = len(by_apartment) + len(by_fio) + len(by_plate) + len(by_phone)

if total_hits == 0:
    st.warning("Ничего не найдено ни по одному из четырёх типов поиска.")
    st.stop()

st.caption(
    f"Найдено: квартира — {len(by_apartment)}, ФИО — {len(by_fio)}, "
    f"авто — {len(by_plate)}, телефон — {len(by_phone)}"
)

st.divider()


# ==========================================
# ОБЪЕДИНЕНИЕ ПО КВАРТИРЕ + ОТРИСОВКА КАРТОЧЕК
# ==========================================
# by_apartment уже даёт квартиру напрямую. by_fio / by_phone дают
# 'квартира' (номер) без apartment_id — сопоставляем по номеру квартиры,
# а не по apartment_id, т.к. у них его просто нет в возвращаемой форме.
# by_plate (find_by_plate_fragment) тоже возвращает 'квартира' по номеру.

apartments_by_number: dict[str, dict] = {}

for a in by_apartment:
    apartments_by_number[str(a["квартира"])] = a

for m in by_fio + by_phone:
    num = str(m.get("квартира") or "")
    if not num:
        continue
    if num not in apartments_by_number:
        apartments_by_number[num] = {
            "apartment_id": None,
            "квартира": num,
            "жильцы": [{"full_name": m.get("фио"), "phone_raw": m.get("телефон"), "person_role": m.get("роль")}],
            "авто": m.get("авто") or [],
            "_source": m.get("_источник") or "ФИО/телефон",
        }
    else:
        # квартира уже найдена точным номером — просто отметим, что
        # совпадение подтвердилось ещё и через ФИО/телефон
        apartments_by_number[num].setdefault("_also_matched_via", []).append(m.get("фио") or m.get("телефон"))

for row in by_plate:
    # find_by_plate_fragment может вернуть sqlite3.Row (доступ по имени)
    # или обычный tuple (если query_lib.core.get_conn() не выставляет
    # row_factory) — не гадаем, а безопасно приводим к dict в обоих случаях.
    try:
        row_d = dict(row)
    except (TypeError, ValueError):
        cols = ["квартира", "номер", "марка", "фио", "телефон"]
        row_d = dict(zip(cols, row))

    num = str(row_d.get("квартира") or "")
    if not num:
        continue
    if num not in apartments_by_number:
        apartments_by_number[num] = {
            "apartment_id": None,
            "квартира": num,
            "жильцы": [{"full_name": row_d.get("фио"), "phone_raw": row_d.get("телефон"), "person_role": None}],
            "авто": [{"номер": row_d.get("номер"), "марка": row_d.get("марка"), "режим": None}],
            "_source": "номер авто",
        }


st.subheader(f"📋 Карточки квартир ({len(apartments_by_number)})")

for num, card in sorted(apartments_by_number.items()):
    with st.container(border=True):
        st.markdown(f"### 🏠 Квартира {num}")
        st.caption(f"Найдено через: {card.get('_source', '—')}")

        col1, col2 = st.columns(2)

        with col1:
            st.markdown("**👤 Жильцы**")
            residents = [r for r in card.get("жильцы", []) if r.get("full_name")]
            if residents:
                for r in residents:
                    phone = r.get("phone_raw") or "—"
                    role = r.get("person_role") or ""
                    st.markdown(f"- **{r['full_name']}** {f'({role})' if role else ''} · 📞 {phone}")
            else:
                st.caption("Нет данных о жильцах")

        with col2:
            st.markdown("**🚗 Автомобили**")
            vehicles = card.get("авто", [])
            if vehicles:
                for v in vehicles:
                    plate = v.get("номер") or "—"
                    model = v.get("марка") or "—"
                    mode = v.get("режим") or "—"
                    st.markdown(f"- **{plate}** · {model} · режим: {mode}")
            else:
                st.caption("Нет автомобилей")

        if st.button(f"↗️ Открыть полную карточку квартиры {num}", key=f"open_{num}"):
            st.session_state["_jump_apartment_number"] = num
            st.switch_page("pages/01_apartment_card.py")