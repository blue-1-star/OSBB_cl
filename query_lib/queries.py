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