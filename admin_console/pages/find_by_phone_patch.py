# ==========================================
# ПАТЧ к G:\Programming\OSBB_cl\query_lib\queries.py
# ==========================================
# Добавить эту функцию в конец файла (после find_by_fio /
# перед last_parking_pattern — не принципиально, куда именно,
# главное — после существующего импорта "import re" в шапке файла,
# он уже есть, второй раз добавлять не нужно).


def find_by_phone(fragment: str, limit: int = 30):
    """
    Поиск жителя по фрагменту телефона.

    Источник телефона в базе НЕ ОДИН — их два, независимых:
      - persons.phone_raw          (из бумажной анкеты, импорт house registry)
      - contact_methods.contact_value  (более новый общий реестр контактов,
                                          поддерживает is_primary, несколько
                                          контактов на квартиру)
    Они не всегда синхронны, поэтому ищем в ОБОИХ и объединяем по
    apartment_id, а не выбираем "главный" источник заранее.

    Сравнение — только по цифрам (+380 / 0 / пробелы / тире не мешают
    совпадению), тем же принципом, что normalize_plate_fragment() для
    номеров авто.

    Возвращает список словарей ТОЙ ЖЕ ФОРМЫ, что find_by_fio — чтобы
    оба источника можно было единообразно отрисовать в одной таблице:
        {'фио', 'квартира', 'роль', 'телефон', 'авто': [{'номер','марка','режим'}, ...]}
    """
    def _digits(value: str) -> str:
        return re.sub(r"\D", "", value or "")

    conn = get_conn()
    try:
        cur = conn.cursor()
        needle = _digits(fragment)
        if not needle:
            return []

        matched: dict[int, dict] = {}  # apartment_id -> карточка

        # Источник 1: persons.phone_raw
        rows = cur.execute("""
            SELECT p.full_name, p.phone_raw, p.person_role,
                   a.apartment_number, a.id AS apartment_id
            FROM persons p
            JOIN apartments a ON a.id = p.apartment_id
            WHERE p.phone_raw IS NOT NULL AND TRIM(p.phone_raw) <> ''
        """).fetchall()
        for r in rows:
            if needle and needle in _digits(r["phone_raw"]):
                matched.setdefault(r["apartment_id"], {
                    "фио": r["full_name"],
                    "квартира": r["apartment_number"],
                    "роль": r["person_role"],
                    "телефон": r["phone_raw"],
                    "_apartment_id": r["apartment_id"],
                    "_источник": "persons.phone_raw",
                })

        # Источник 2: contact_methods.contact_value
        rows2 = cur.execute("""
            SELECT c.apartment_id, c.contact_value, a.apartment_number
            FROM contact_methods c
            JOIN apartments a ON a.id = c.apartment_id
            WHERE c.contact_value IS NOT NULL AND TRIM(c.contact_value) <> ''
        """).fetchall()
        for r in rows2:
            if not (needle and needle in _digits(r["contact_value"])):
                continue
            aid = r["apartment_id"]
            if aid in matched:
                continue  # источник 1 уже дал карточку для этой квартиры
            person = cur.execute(
                "SELECT full_name, person_role FROM persons WHERE apartment_id=? LIMIT 1",
                (aid,),
            ).fetchone()
            matched[aid] = {
                "фио": person["full_name"] if person else None,
                "квартира": r["apartment_number"],
                "роль": person["person_role"] if person else None,
                "телефон": r["contact_value"],
                "_apartment_id": aid,
                "_источник": "contact_methods.contact_value",
            }

        result = []
        for aid, m in list(matched.items())[:limit]:
            vehicles = cur.execute("""
                SELECT license_plate_normalized AS номер, car_model AS марка, parking_time AS режим
                FROM vehicles WHERE apartment_id = ?
            """, (aid,)).fetchall()
            m["авто"] = [dict(v) for v in vehicles]
            m.pop("_apartment_id", None)
            result.append(m)
        return result
    finally:
        conn.close()
