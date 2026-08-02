import re
from .core import get_conn


def find_by_plate_fragment(fragment: str, limit: int = 50):
    """
    1. Поиск по фрагменту номера.
    Возвращает: квартира, полный номер, марка, ФИО, телефон
    """
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT 
            a.apartment_number AS квартира,
            v.license_plate AS номер,
            v.car_model AS марка,
            ra.telegram_first_name || ' ' || ra.telegram_last_name AS фио,
            c.contact_value AS телефон
        FROM vehicles v
        LEFT JOIN apartments a ON a.id = v.apartment_id
        LEFT JOIN resident_accounts ra ON ra.apartment_id = a.id
        LEFT JOIN contact_methods c ON c.apartment_id = a.id AND c.is_primary = 1
        WHERE v.license_plate LIKE ?
           OR v.license_plate_normalized LIKE ?
        GROUP BY v.id
        LIMIT ?
    """, (f'%{fragment}%', f'%{fragment}%', limit))

    rows = cur.fetchall()
    conn.close()
    return rows


def apartments_with_multiple_cars(min_count: int = 2):
    """
    2. Квартиры с количеством автомобилей >= min_count
    """
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT 
            a.apartment_number AS квартира,
            COUNT(v.id) AS количество_авто
        FROM apartments a
        JOIN vehicles v ON v.apartment_id = a.id
        GROUP BY a.id
        HAVING COUNT(v.id) >= ?
        ORDER BY количество_авто DESC
    """, (min_count,))

    rows = cur.fetchall()
    conn.close()
    return rows


def apartments_with_missing_parking_mode():
    """
    3. Квартиры, у которых есть авто без указанного режима парковки (parking_time IS NULL)
    """
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT DISTINCT
            a.apartment_number AS квартира,
            v.license_plate AS номер,
            v.car_model AS марка,
            v.parking_time AS режим
        FROM apartments a
        JOIN vehicles v ON v.apartment_id = a.id
        WHERE v.parking_time IS NULL OR TRIM(v.parking_time) = ''
        ORDER BY a.apartment_number
    """)

    rows = cur.fetchall()
    conn.close()
    return rows


def parking_debtors(period_code: str = None):
    """
    4. Список должников по парковке с ФИО жильцов.
    """
    conn = get_conn()
    cur = conn.cursor()

    sql = """
        SELECT 
            c.apartment_number AS квартира,
            COALESCE(
                ra.telegram_first_name || ' ' || ra.telegram_last_name,
                ra.telegram_username,
                'Неизвестно'
            ) AS фио,
            SUM(c.amount) AS начислено,
            COALESCE(SUM(pa.amount), 0) AS оплачено,
            SUM(c.amount) - COALESCE(SUM(pa.amount), 0) AS задолженность
        FROM charges c
        LEFT JOIN apartments a ON a.apartment_number = c.apartment_number
        LEFT JOIN resident_accounts ra ON ra.apartment_id = a.id
        LEFT JOIN payment_allocations pa ON pa.charge_id = c.id
        WHERE c.service_code IN ('PARKING_DAY', 'PARKING_NIGHT')
    """

    params = []

    if period_code:
        sql += " AND c.period_code = ?"
        params.append(period_code)

    sql += """
        GROUP BY c.apartment_number
        HAVING SUM(c.amount) - COALESCE(SUM(pa.amount), 0) > 0.01
        ORDER BY задолженность DESC
    """

    cur.execute(sql, params)
    rows = cur.fetchall()
    conn.close()
    return rows


def non_standard_plates():
    """
    5. Автомобили с номерами вне шаблона AA1234BB
    """
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT 
            a.apartment_number AS квартира,
            v.license_plate AS номер,
            v.car_model AS марка,
            v.license_plate_normalized AS номер_норм
        FROM vehicles v
        LEFT JOIN apartments a ON a.id = v.apartment_id
        ORDER BY a.apartment_number
    """)

    rows = cur.fetchall()
    conn.close()

    pattern = re.compile(r'^[A-Z]{2}\d{4}[A-Z]{2}$')
    result = []
    for row in rows:
        plate = row['номер_норм'] or row['номер']
        if plate and not pattern.match(plate.upper()):
            result.append({
                'квартира': row['квартира'],
                'номер': row['номер'],
                'марка': row['марка'],
            })

    return result
def vehicles_by_apartment(apartment_number: str):
    """
    6. Получить все автомобили по номеру квартиры.
    Возвращает: номер авто, марка, режим парковки (D/N/NULL)
    """
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT 
            v.license_plate AS номер,
            v.car_model AS марка,
            v.parking_time AS режим
        FROM vehicles v
        JOIN apartments a ON a.id = v.apartment_id
        WHERE a.apartment_number = ?
        ORDER BY v.id
    """, (apartment_number,))

    rows = cur.fetchall()
    conn.close()
    return rows

def last_payments(limit: int = 20):
    """
    Последние поступления в кассу.

    Если операция не связана с автомобилем, но у квартиры в базе
    ровно один автомобиль, он возвращается как подсказка кассиру.

    Возвращает:
        дата,
        квартира,
        номер,
        авто_статус,
        режим,
        квитанция,
        сумма
    """
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        WITH apartment_vehicles AS (
            SELECT
                apartment_id,
                COUNT(*) AS vehicle_count,
                MAX(id) AS single_vehicle_id
            FROM vehicles
            GROUP BY apartment_id
        )
        SELECT
            o.operation_date AS дата,
            o.apartment_number AS квартира,

            COALESCE(
                direct_vehicle.license_plate_normalized,
                direct_vehicle.license_plate,
                CASE
                    WHEN av.vehicle_count = 1 THEN
                        COALESCE(
                            suggested_vehicle.license_plate_normalized,
                            suggested_vehicle.license_plate,
                            ''
                        )
                    ELSE ''
                END
            ) AS номер,

            CASE
                WHEN o.vehicle_id IS NOT NULL THEN 'УКАЗАНО'
                WHEN av.vehicle_count = 1 THEN 'ПРЕДЛОЖИТЬ'
                WHEN av.vehicle_count > 1 THEN 'НЕОДНОЗНАЧНО'
                ELSE 'АВТО НЕ НАЙДЕНО'
            END AS авто_статус,

            CASE o.base_service_code
                WHEN 'PARKING_DAY'   THEN 'День'
                WHEN 'PARKING_NIGHT' THEN 'Ночь'
                ELSE o.base_service_code
            END AS режим,

            substr(COALESCE(o.cashier_receipt_id, ''), -8) AS квитанция,
            o.amount AS сумма

        FROM cashbox_operations o

        LEFT JOIN vehicles direct_vehicle
               ON direct_vehicle.id = o.vehicle_id

        LEFT JOIN apartments a
               ON a.apartment_number = o.apartment_number

        LEFT JOIN apartment_vehicles av
               ON av.apartment_id = a.id

        LEFT JOIN vehicles suggested_vehicle
               ON suggested_vehicle.id = av.single_vehicle_id
              AND av.vehicle_count = 1

        WHERE lower(o.direction) = 'in'

        ORDER BY
            o.operation_date DESC,
            o.id DESC

        LIMIT ?
    """, (limit,))

    rows = cur.fetchall()
    conn.close()
    return rows
def set_tariff(
    service_code: str,
    amount: float = None,
    valid_from: str = None,
    comment: str = None,
    currency: str = "UAH",
):
    """
    Добавляет новую запись действующего тарифа в service_tariffs.

    - Если amount не указан — берёт сумму из последнего (по valid_from)
      тарифа для этого же service_code. Если истории вообще нет и сумма
      не передана явно — бросает ValueError (лучше явная ошибка, чем
      тихо создать тариф с NULL).
    - Автоматически закрывает предыдущую открытую запись (valid_to)
      днём раньше нового valid_from — чтобы периоды не перекрывались
      и история оставалась чистой.
    - valid_from по умолчанию — сегодня.

    Возвращает id новой записи.
    """
    from datetime import date, datetime, timedelta

    conn = get_conn()
    cur = conn.cursor()

    valid_from = valid_from or date.today().strftime("%Y-%m-%d")

    cur.execute(
        """
        SELECT amount, valid_from FROM service_tariffs
        WHERE service_code = ?
        ORDER BY valid_from DESC LIMIT 1
        """,
        (service_code,),
    )
    prev = cur.fetchone()

    if amount is None:
        if prev is None:
            conn.close()
            raise ValueError(
                f"Нет предыдущего тарифа для {service_code} — сумму нужно указать явно"
            )
        amount = prev["amount"]

    if prev is not None:
        prev_day = datetime.strptime(prev["valid_from"], "%Y-%m-%d").date()
        new_day = datetime.strptime(valid_from, "%Y-%m-%d").date()
        if new_day > prev_day:
            close_day = (new_day - timedelta(days=1)).strftime("%Y-%m-%d")
            cur.execute(
                """
                UPDATE service_tariffs
                SET valid_to = ?
                WHERE service_code = ? AND valid_from = ? AND valid_to IS NULL
                """,
                (close_day, service_code, prev["valid_from"]),
            )

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cur.execute(
        """
        INSERT INTO service_tariffs
            (service_code, amount, currency, valid_from, valid_to, is_active, comment, created_at)
        VALUES (?, ?, ?, ?, NULL, 1, ?, ?)
        """,
        (service_code, amount, currency, valid_from, comment or "", now),
    )


    conn.commit()
    new_id = cur.lastrowid
    conn.close()
    return new_id

def now_db() -> str:
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def ensure_verification_journal(con) -> None:
    con.execute("""
        CREATE TABLE IF NOT EXISTS verification_journal (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            apartment_number TEXT,
            issue_type TEXT NOT NULL,
            description TEXT,
            related_payment_id INTEGER,
            related_receipt_id INTEGER,
            raised_by TEXT,
            assigned_role TEXT,
            concerns_field TEXT,
            status TEXT NOT NULL DEFAULT 'OPEN',
            resolved_at TEXT,
            resolved_by TEXT,
            resolution_note TEXT
        )
    """)
    con.commit()


def log_verification_task(
    apartment_number: str = None,
    issue_type: str = "OTHER",
    description: str = "",
    related_payment_id: int = None,
    related_receipt_id: int = None,
    raised_by: str = None,
    assigned_role: str = None,
    concerns_field: str = None,
) -> int:
    """Заносит вопрос в журнал согласования, не трогая сам платёж.
    issue_type: 'VEHICLE_UNLINKED' | 'MISSING_VEHICLE' | 'CHECK_PLATE' |
                'MISSING_PARKING_MODE' | 'AMOUNT_MISMATCH' | 'OTHER'
    concerns_field: 'vehicle_plate' | 'parking_mode' | 'apartment_number' |
                    'full_name' | 'phone' | 'amount' | 'other' — короткая,
                    структурированная пометка "чего касается", отдельно от
                    свободного текста description.
    assigned_role: 'GUARD' | 'CASHIER' | None (None = виден только админу,
                   как и любая запись, но не всплывает у остальных)."""
    conn = get_conn()
    ensure_verification_journal(conn)
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO verification_journal (
            created_at, apartment_number, issue_type, description,
            related_payment_id, related_receipt_id, raised_by,
            assigned_role, concerns_field, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN')
    """, (
        now_db(), apartment_number, issue_type, description,
        related_payment_id, related_receipt_id, raised_by, assigned_role,
        concerns_field,
    ))
    conn.commit()
    task_id = cur.lastrowid
    conn.close()
    return task_id


def list_open_verification_tasks(assigned_role=None, limit=30):
    conn = get_conn()
    ensure_verification_journal(conn)
    cur = conn.cursor()
    if assigned_role:
        cur.execute("""
            SELECT * FROM verification_journal
            WHERE status = 'OPEN' AND (assigned_role = ? OR assigned_role IS NULL)
            ORDER BY created_at DESC LIMIT ?
        """, (assigned_role, limit))
    else:
        cur.execute("SELECT * FROM verification_journal WHERE status='OPEN' ORDER BY created_at DESC LIMIT ?", (limit,))
    rows = cur.fetchall()
    conn.close()
    return rows


def resolve_verification_task(task_id, resolved_by, resolution_note=""):
    conn = get_conn()
    ensure_verification_journal(conn)
    cur = conn.cursor()
    cur.execute("""
        UPDATE verification_journal
        SET status='RESOLVED', resolved_at=?, resolved_by=?, resolution_note=?
        WHERE id=? AND status='OPEN'
    """, (now_db(), resolved_by, resolution_note, task_id))
    changed = cur.rowcount
    conn.commit()
    conn.close()
    return changed > 0

def add_service_catalog_entry(
    service_code: str,
    service_group: str,
    service_name: str,
    unit: str,
    service_type: str = None,
    category: str = None,
    is_monthly: int = 1,
    is_fundraising: int = 0,
    is_commercial: int = 0,
    is_access_control: int = 0,
    is_cash_collectable: int = 1,
    access_policy_enabled: int = 0,
    access_policy_scope: str = 'NONE',
    access_policy_mode: str = 'NONE',
    manual_review_required: int = 0,
):
    """
    Идемпотентно добавляет запись в service_catalog. Если service_code
    уже есть — ничего не меняет, просто возвращает существующий id
    (created=False). Значения по умолчанию соответствуют обычной
    кассовой ежемесячной услуге (как PARKING_DAY/PARKING_NIGHT) —
    поменяйте is_monthly/is_cash_collectable и т.п., если услуга
    другого рода.

    Возвращает (id, created) — created=True, если строка реально
    только что вставлена, False — если уже существовала.
    """
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT id FROM service_catalog WHERE service_code = ?", (service_code,))
    existing = cur.fetchone()
    if existing:
        conn.close()
        return existing["id"], False

    cur.execute(
        """
        INSERT INTO service_catalog (
            service_code, service_group, service_name, unit,
            is_active, service_type, category,
            is_monthly, is_fundraising, is_commercial, is_access_control,
            is_cash_collectable, access_policy_enabled,
            access_policy_scope, access_policy_mode,
            manual_review_required
        ) VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            service_code, service_group, service_name, unit,
            service_type, category,
            is_monthly, is_fundraising, is_commercial, is_access_control,
            is_cash_collectable, access_policy_enabled,
            access_policy_scope, access_policy_mode,
            manual_review_required,
        ),
    )
    conn.commit()
    new_id = cur.lastrowid
    conn.close()
    return new_id, True

def _fold_uk_ru(text: str) -> str:
    """
    Сворачивает вариативные украинские/русские буквы к одной форме —
    для сравнения ФИО, независимо от того, каким языком записано.
    Стріха / Стриха и т.п. считаются одним и тем же. Только для
    ПОИСКА — не для хранения/отображения, оригинал в базе не трогается.
    """
    if not text:
        return ""
    text = text.lower()
    replacements = {
        "і": "и", "ї": "и", "є": "е", "ґ": "г", "ы": "и", "э": "е",
    }
    for src, dst in replacements.items():
        text = text.replace(src, dst)
    return text


def find_by_fio(fragment: str, limit: int = 30):
    """
    Поиск жителя по фрагменту ФИО в РЕАЛЬНЫХ данных дома — таблица
    persons (заполнена из бумажной анкеты/паркинг-бота через
    import_house_registry.py, 343 строки на весь дом). Учитывает
    украинское/русское написание (Стріха/Стриха — одно и то же).

    (Не resident_accounts.telegram_first_name/last_name — там только
    самоописание тех, кто писал боту, почти пусто.)

    Возвращает список словарей:
        {'фио', 'квартира', 'роль', 'телефон', 'авто': [{'номер','марка','режим'}, ...]}
    """
    conn = get_conn()
    try:
        cur = conn.cursor()
        rows = cur.execute("""
            SELECT p.id, p.full_name, p.phone_raw, p.ownership_type, p.person_role,
                   a.apartment_number, a.id AS apartment_id
            FROM persons p
            JOIN apartments a ON a.id = p.apartment_id
        """).fetchall()

        needle = _fold_uk_ru(fragment)
        if not needle:
            return []

        matched = [dict(r) for r in rows if r["full_name"] and needle in _fold_uk_ru(r["full_name"])]

        result = []
        for m in matched[:limit]:
            vehicles = cur.execute("""
                SELECT
                    license_plate_normalized AS номер,
                    car_model AS марка,
                    parking_time AS режим
                FROM vehicles
                WHERE apartment_id = ?
            """, (m["apartment_id"],)).fetchall()
            result.append({
                "фио": m["full_name"],
                "квартира": m["apartment_number"],
                "роль": m["person_role"],
                "телефон": m["phone_raw"],
                "авто": [dict(v) for v in vehicles],
            })
        return result
    finally:
        conn.close()

def last_parking_pattern(apartment_number: str):
    """
    Последний период, за который квартира платила именно за парковку
    (base_service_code LIKE 'PARKING_%'), со всеми строками этого
    периода — сколько бы авто/платежей в него ни входило. Группировка
    идёт по КВАРТИРЕ, не по конкретному авто/ФИО — находится независимо
    от того, как искали плательщика.

    Если у какой-то из прошлых строк есть ОТКРЫТАЯ (нерешённая) запись
    в verification_journal — она возвращается вместе со строкой
    (carry_forward_flags), чтобы при повторе паттерна перенести ту же
    пометку "на проверку" на новый платёж, а не молча её потерять.
    Действует принцип: сомнение в паттерне не отменяет приём денег —
    деньги принимаются с той же честной пометкой, что и в прошлый раз.

    Возвращает None, если у квартиры вообще не было парковочных
    платежей. Иначе:
        {'period': '2026-07', 'rows': [
            {'payment_id', 'amount', 'base_service_code', 'plate',
             'carry_forward_flags': [{'issue_type','description','concerns_field'}, ...]},
            ...
        ]}
    """
    conn = get_conn()
    try:
        cur = conn.cursor()
        row = cur.execute("""
            SELECT period_code FROM payments
            WHERE apartment_number = ? AND base_service_code LIKE 'PARKING_%'
            ORDER BY id DESC LIMIT 1
        """, (apartment_number,)).fetchone()
        if not row:
            return None
        last_period = row["period_code"]

        rows = cur.execute("""
            SELECT p.id AS payment_id, p.amount, p.base_service_code,
                   v.license_plate_normalized AS plate
            FROM payments p
            LEFT JOIN vehicles v ON v.id = p.vehicle_id
            WHERE p.apartment_number = ? AND p.period_code = ? AND p.base_service_code LIKE 'PARKING_%'
            ORDER BY p.id
        """, (apartment_number, last_period)).fetchall()

        result_rows = []
        for r in rows:
            r = dict(r)
            open_flags = cur.execute("""
                SELECT issue_type, description, concerns_field
                FROM verification_journal
                WHERE related_payment_id = ? AND status = 'OPEN'
            """, (r["payment_id"],)).fetchall()
            r["carry_forward_flags"] = [dict(f) for f in open_flags]
            result_rows.append(r)

        return {"period": last_period, "rows": result_rows}
    finally:
        conn.close()

def vehicle_payment_history(vehicle_id: int, service_prefix: str = "PARKING"):
    """
    Полная история платежей одного авто (по умолчанию — только за
    парковку), в хронологическом порядке по period_code. Строительный
    блок для будущего автоматического определения следующего периода
    (нужна вся история, не только последняя запись — чтобы видеть
    пропуски, а не просто "последний + 1").

    Возвращает список словарей: id, payment_date, amount, period_code,
    base_service_code, cashbox_code — по возрастанию period_code.
    """
    conn = get_conn()
    try:
        cur = conn.cursor()
        rows = cur.execute(
            """
            SELECT id, payment_date, amount, period_code, base_service_code, cashbox_code
            FROM payments
            WHERE vehicle_id = ? AND base_service_code LIKE ?
            ORDER BY period_code, id
            """,
            (vehicle_id, f"{service_prefix}%"),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()

def _add_month(period_code: str) -> str:
    year, month = map(int, period_code.split("-"))
    month += 1
    if month > 12:
        month = 1
        year += 1
    return f"{year:04d}-{month:02d}"


def _expand_period_code(period_code):
    """Разворачивает обычный 'ГГГГ-ММ' как есть; составной исторический
    'ГГГГ-ММ_ГГГГ-ММ' (платёж сразу за два месяца, старый импорт) —
    в оба месяца по отдельности, раз оба были оплачены. Совсем
    нестандартный формат — пропускается (не участвует в поиске
    пропуска, но и не ломает разбор)."""
    import re
    if not period_code:
        return []
    if "_" in period_code:
        return [p for p in period_code.split("_") if re.match(r"^\d{4}-\d{2}$", p)]
    if re.match(r"^\d{4}-\d{2}$", period_code):
        return [period_code]
    return []


def next_expected_period(vehicle_id: int, service_prefix: str = "PARKING"):
    """
    "Первая незаполненная ячейка" вместо простого "последний период + 1".
    Конвенция ОСББ: платёж — аванс за СЛЕДУЮЩИЙ месяц (решение собрания).

    Проходит всю историю платежей авто (не только последнюю запись) —
    если где-то в середине есть пропуск, возвращает именно пропущенный
    период. Составные исторические периоды ("2026-05_2026-06" — платёж
    сразу за два месяца) разворачиваются в оба месяца, не ломают разбор.

    Возвращает (period_code, is_gap).
    """
    history = vehicle_payment_history(vehicle_id, service_prefix)
    if not history:
        return None, False

    paid_periods = set()
    for h in history:
        paid_periods.update(_expand_period_code(h["period_code"]))
    if not paid_periods:
        return None, False

    periods_sorted = sorted(paid_periods)
    earliest, latest = periods_sorted[0], periods_sorted[-1]

    cursor = earliest
    while cursor <= latest:
        if cursor not in paid_periods:
            return cursor, True
        cursor = _add_month(cursor)

    return _add_month(latest), False

def add_cashbox_operation_type(operation_type: str, expected_direction: str, description: str = None):
    """
    Идемпотентно регистрирует новый тип операции кассы (cashbox_operations.
    operation_type). Не блокирует ничего в самой cashbox_operations (там
    нет CHECK) — это справочник для порядка, не жёсткое ограничение.
    Возвращает (created: bool).
    """
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM cashbox_operation_types WHERE operation_type = ?", (operation_type,))
    if cur.fetchone():
        conn.close()
        return False
    cur.execute(
        "INSERT INTO cashbox_operation_types (operation_type, expected_direction, description) VALUES (?, ?, ?)",
        (operation_type, expected_direction, description),
    )
    conn.commit()
    conn.close()
    return True


def list_cashbox_operation_types():
    """Список всех зарегистрированных типов операций кассы."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT operation_type, expected_direction, description, is_active FROM cashbox_operation_types ORDER BY operation_type"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


BILLING_CONVENTION = "advance"  # "advance" (аванс за следующий месяц) | "arrears" (по факту за этот же). Решение собрания ОСББ: "advance".

def current_due_period(as_of_date=None, convention: str = None) -> str:
    """
    Период, который должен быть УЖЕ оплачен на указанную (или
    сегодняшнюю) дату, согласно конвенции. Единственное место, где
    направление конвенции реально влияет на результат — поиск
    пропусков в истории (next_expected_period) от него не зависит,
    только эта функция и is_overdue().
    """
    from datetime import date
    convention = convention or BILLING_CONVENTION
    d = as_of_date or date.today()
    period = f"{d.year:04d}-{d.month:02d}"
    if convention == "arrears":
        return _subtract_month(period)
    return period


def is_overdue(next_expected: str | None, as_of_date=None, convention: str = None) -> bool:
    """
    True, если ожидаемый следующий период (из next_expected_period())
    уже наступил или прошёл относительно текущей даты и конвенции —
    то есть авто должно было заплатить, но пока не заплатило.
    """
    if next_expected is None:
        return False
    due = current_due_period(as_of_date, convention)
    return next_expected <= due

def resolve_group_vehicle_assignment(apartment_number: str, period_code: str):
    """
    Проверяет, не закрывает ли текущий набор платежей БЕЗ vehicle_id
    (у квартиры, за конкретный период) полный набор тарифов всех её
    известных авто — если количество платежей и их суммы (как
    мультимножество) точно совпадают с тарифами, разрешает vehicle_id
    для ВСЕХ сразу (не гадая, какая строка какому авто соответствует —
    при совпадающих тарифах это не имеет значения, все машины покрыты).

    Вызывается сразу после подтверждения любого платежа за парковку —
    "главная дорога" разработки, не отдельный отчёт задним числом.
    Правит синхронно payments и связанную cashbox_operations.

    Возвращает список разрешённых {'payment_id', 'vehicle_id', 'plate'}
    (пусто, если группа не сложилась полностью — ничего не меняет).
    """
    from collections import Counter

    conn = get_conn()
    try:
        cur = conn.cursor()
        unresolved = cur.execute("""
            SELECT id, amount, cashbox_operation_id FROM payments
            WHERE apartment_number = ? AND period_code = ? AND vehicle_id IS NULL
        """, (apartment_number, period_code)).fetchall()
        if not unresolved:
            return []

        tariff_by_mode = {}
        for mode, code in (("Day", "PARKING_DAY"), ("Night", "PARKING_NIGHT")):
            row = cur.execute(
                "SELECT amount FROM service_tariffs WHERE service_code=? AND is_active=1 ORDER BY valid_from DESC LIMIT 1",
                (code,),
            ).fetchone()
            tariff_by_mode[mode] = row[0] if row else None

        vehicles = cur.execute("""
            SELECT v.id, COALESCE(v.license_plate_normalized, v.license_plate) AS plate, v.parking_time
            FROM vehicles v JOIN apartments a ON a.id = v.apartment_id
            WHERE a.apartment_number = ?
        """, (apartment_number,)).fetchall()

        # Машины, УЖЕ получившие платёж за этот период через прямую
        # привязку — исключаем из пула, иначе задвоим уже решённый случай
        # (например: одна машина оплачена обычным выбором, вторая ещё нет).
        already_paid_vehicle_ids = {
            row["vehicle_id"] for row in cur.execute(
                "SELECT vehicle_id FROM payments WHERE apartment_number=? AND period_code=? AND vehicle_id IS NOT NULL",
                (apartment_number, period_code),
            ).fetchall()
        }

        vehicle_tariffs = [
            (v["id"], v["plate"], tariff_by_mode.get(v["parking_time"]))
            for v in vehicles
            if v["plate"] and tariff_by_mode.get(v["parking_time"]) and v["id"] not in already_paid_vehicle_ids
        ]

        amounts = [float(p["amount"]) for p in unresolved]
        if len(amounts) != len(vehicle_tariffs) or not vehicle_tariffs:
            return []
        if Counter(amounts) != Counter(t for _, _, t in vehicle_tariffs):
            return []

        vehicle_tariffs_sorted = sorted(vehicle_tariffs, key=lambda x: x[1])
        resolved = []
        for p, (vid, plate, tariff) in zip(unresolved, vehicle_tariffs_sorted):
            cur.execute("UPDATE payments SET vehicle_id=? WHERE id=?", (vid, p["id"]))
            if p["cashbox_operation_id"]:
                cur.execute("UPDATE cashbox_operations SET vehicle_id=? WHERE id=?", (vid, p["cashbox_operation_id"]))
            resolved.append({"payment_id": p["id"], "vehicle_id": vid, "plate": plate})
        conn.commit()
        return resolved
    finally:
        conn.close()