from pathlib import Path
import sys
import sqlite3
import argparse
from datetime import datetime

OSBB_ROOT = Path(__file__).resolve().parent
PY_ROOT = OSBB_ROOT.parent

if str(PY_ROOT) not in sys.path:
    sys.path.insert(0, str(PY_ROOT))

from config import paths, USE_TEST_DB

try:
    from audit_logger import audit_log
except Exception:
    audit_log = None


# Утверждённый чистый каталог.
#
# ВАЖНО:
# BARRIER_PHONE — ежемесячный платный сервис с дополнительным признаком
# управляемого доступа. Он начисляется для зарегистрированного номера,
# если отсутствует долг по PARKING_DAY/PARKING_NIGHT.
#
# Конкретные месячные статьи создаются для PARKING_DAY, PARKING_NIGHT
# и BARRIER_PHONE: 05_26_Day, 05_26_Night, 05_26_BarrierPhone и т. п.
CANONICAL_SERVICES = [
    {
        "service_code": "PARKING_DAY",
        "service_group": "MONTHLY",
        "service_name": "Парковка Day",
        "unit": "service",
        "service_type": "MONTHLY",
        "category": "PARKING",
        "is_monthly": 1,
        "is_fundraising": 0,
        "is_commercial": 0,
        "is_access_control": 0,
        "is_cash_collectable": 1,
        "is_active": 1,
        "comment": "Ежемесячная парковка, дневной тариф.",
    },
    {
        "service_code": "PARKING_NIGHT",
        "service_group": "MONTHLY",
        "service_name": "Парковка Night",
        "unit": "service",
        "service_type": "MONTHLY",
        "category": "PARKING",
        "is_monthly": 1,
        "is_fundraising": 0,
        "is_commercial": 0,
        "is_access_control": 0,
        "is_cash_collectable": 1,
        "is_active": 1,
        "comment": "Ежемесячная парковка, ночной/суточный тариф.",
    },
    {
        "service_code": "BARRIER_PHONE",
        "service_group": "MONTHLY",
        "service_name": "Телефонный доступ к шлагбауму",
        "unit": "service",
        "service_type": "MONTHLY",
        "category": "ACCESS",
        "is_monthly": 1,
        "is_fundraising": 0,
        "is_commercial": 0,
        "is_access_control": 1,
        "is_cash_collectable": 1,
        "is_active": 1,
        "comment": (
            "Ежемесячный платный сервис для зарегистрированного номера телефона. "
            "Начисляется автоматически только при отсутствии долга по PARKING_DAY/PARKING_NIGHT. "
            "Доступ может быть отключён при появлении такого долга."
        ),
    },
    {
        "service_code": "BARRIER_PHONE_CONNECT",
        "service_group": "ACCESS_CONTROL",
        "service_name": "Подключение / повторное подключение телефонного доступа",
        "unit": "service",
        "service_type": "ONE_TIME",
        "category": "ACCESS",
        "is_monthly": 0,
        "is_fundraising": 0,
        "is_commercial": 0,
        "is_access_control": 1,
        "is_cash_collectable": 1,
        "is_active": 1,
        "comment": (
            "Разовая услуга подключения или повторного подключения номера к GSM-модулю. "
            "Тариф: 200 грн. После отключения номер должен пройти эту услугу заново."
        ),
    },
    {
        "service_code": "IMPROVEMENT",
        "service_group": "FUNDRAISING",
        "service_name": "Благоустройство",
        "unit": "service",
        "service_type": "FUNDRAISING",
        "category": "IMPROVEMENT",
        "is_monthly": 0,
        "is_fundraising": 1,
        "is_commercial": 0,
        "is_access_control": 0,
        "is_cash_collectable": 1,
        "is_active": 1,
        "comment": (
            "Разметка, асфальтирование, бытовые условия охранников "
            "и иные работы по благоустройству."
        ),
    },
    {
        "service_code": "PARKING_EQUIPMENT",
        "service_group": "FUNDRAISING",
        "service_name": "Оборудование парковки",
        "unit": "service",
        "service_type": "FUNDRAISING",
        "category": "EQUIPMENT",
        "is_monthly": 0,
        "is_fundraising": 1,
        "is_commercial": 0,
        "is_access_control": 0,
        "is_cash_collectable": 1,
        "is_active": 1,
        "comment": (
            "Камеры, видеорегистратор, распознавание номеров, "
            "сеть и иное оборудование парковки."
        ),
    },
    {
        "service_code": "BARRIER_REPAIR",
        "service_group": "FUNDRAISING",
        "service_name": "Ремонт / сбор на шлагбаум",
        "unit": "service",
        "service_type": "FUNDRAISING",
        "category": "BARRIER",
        "is_monthly": 0,
        "is_fundraising": 1,
        "is_commercial": 0,
        "is_access_control": 0,
        "is_cash_collectable": 1,
        "is_active": 1,
        "comment": (
            "Ремонт, обслуживание и детали шлагбаума. "
            "Буква Ш в старой Охорона.xlsx означает именно этот сбор."
        ),
    },
    {
        "service_code": "GUEST_PARKING",
        "service_group": "COMMERCIAL",
        "service_name": "Гостевая парковка",
        "unit": "service",
        "service_type": "COMMERCIAL",
        "category": "PARKING",
        "is_monthly": 0,
        "is_fundraising": 0,
        "is_commercial": 1,
        "is_access_control": 0,
        "is_cash_collectable": 1,
        "is_active": 1,
        "comment": (
            "Коммерческий сервис гостевой парковки. "
            "Тарифы и правила задаются отдельными статьями service_items."
        ),
    },
    {
        "service_code": "PARK_PLACE_RENT",
        "service_group": "COMMERCIAL",
        "service_name": "Аренда паркоместа",
        "unit": "service",
        "service_type": "COMMERCIAL",
        "category": "PARKING_ASSET",
        "is_monthly": 0,
        "is_fundraising": 0,
        "is_commercial": 1,
        "is_access_control": 0,
        "is_cash_collectable": 1,
        "is_active": 1,
        "comment": "Коммерческая аренда паркоместа.",
    },
    {
        "service_code": "PARK_PLACE_SALE",
        "service_group": "COMMERCIAL",
        "service_name": "Продажа паркоместа",
        "unit": "service",
        "service_type": "COMMERCIAL",
        "category": "PARKING_ASSET",
        "is_monthly": 0,
        "is_fundraising": 0,
        "is_commercial": 1,
        "is_access_control": 0,
        "is_cash_collectable": 1,
        "is_active": 1,
        "comment": (
            "Коммерческая продажа паркоместа. "
            "Детальный учёт мест будет отдельным модулем."
        ),
    },
]

# Единственная стартовая статья, потому что её тариф уже утверждён.
# Месячные статьи парковки и BARRIER_PHONE создаются генератором по периоду.
INITIAL_SERVICE_ITEMS = [
    {
        "service_item_code": "01_BarrierPhoneConnect",
        "service_code": "BARRIER_PHONE_CONNECT",
        "service_item_name": "Подключение / повторное подключение телефонного доступа",
        "service_type": "ONE_TIME",
        "period_code": None,
        "sequence_no": 1,
        "amount_default": 200,
        "currency": "UAH",
        "date_from": None,
        "date_to": None,
        "status": "active",
        "is_active": 1,
        "description": (
            "Оплата подключения номера к GSM-модулю. "
            "Повторно требуется после любого отключения."
        ),
        "comment": "Тариф 200 грн.",
        "created_at": None,
        "updated_at": None,
    }
]



def get_db_file():
    return paths.OSBB_TEST_DB_FILE if USE_TEST_DB else paths.OSBB_DB_FILE


def now_db():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def table_exists(cur, table_name):
    cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    )
    return cur.fetchone() is not None


def table_columns(cur, table_name):
    cur.execute(f"PRAGMA table_info({table_name})")
    return [row[1] for row in cur.fetchall()]


def required_field_defaults(cur, table_name):
    """
    Старый service_catalog уже мог иметь обязательные столбцы
    service_group, unit или другие. Динамически даём им безопасные значения.
    """
    cur.execute(f"PRAGMA table_info({table_name})")
    defaults = {}

    for cid, name, col_type, notnull, default_value, pk in cur.fetchall():
        if pk or not notnull or default_value is not None:
            continue

        name_upper = (name or "").upper()
        type_upper = (col_type or "").upper()

        if name == "service_group":
            defaults[name] = "GENERAL"
        elif name == "unit":
            defaults[name] = "service"
        elif name in {"service_name", "name", "title"}:
            defaults[name] = "Без названия"
        elif name in {"service_code", "code"}:
            defaults[name] = "UNKNOWN"
        elif "ACTIVE" in name_upper:
            defaults[name] = 1
        elif any(token in name_upper for token in ("AMOUNT", "PRICE", "SUM", "BALANCE")):
            defaults[name] = 0
        elif name_upper.endswith("_AT") or "DATE" in name_upper or "TIME" in name_upper:
            defaults[name] = now_db()
        elif "INT" in type_upper:
            defaults[name] = 0
        elif any(token in type_upper for token in ("REAL", "NUM", "DEC")):
            defaults[name] = 0
        else:
            defaults[name] = ""

    return defaults


def insert_dynamic(cur, table_name, values):
    columns = table_columns(cur, table_name)
    safe_values = dict(values)

    for column, default_value in required_field_defaults(cur, table_name).items():
        safe_values.setdefault(column, default_value)

    insert_columns = [name for name in safe_values if name in columns]
    placeholders = ", ".join("?" for _ in insert_columns)

    cur.execute(
        f"INSERT INTO {table_name} ({', '.join(insert_columns)}) VALUES ({placeholders})",
        tuple(safe_values[name] for name in insert_columns),
    )
    return cur.lastrowid


def update_service_dynamic(cur, service_code, values):
    columns = table_columns(cur, "service_catalog")
    set_parts = []
    params = []

    for key, value in values.items():
        if key in columns and key != "service_code":
            set_parts.append(f"{key} = ?")
            params.append(value)

    if not set_parts:
        return False

    params.append(service_code)
    cur.execute(
        f"UPDATE service_catalog SET {', '.join(set_parts)} WHERE service_code = ?",
        tuple(params),
    )
    return cur.rowcount > 0


def upsert_service_item(cur, values):
    if not table_exists(cur, "service_items"):
        return False

    cur.execute(
        "SELECT id FROM service_items WHERE service_item_code = ?",
        (values["service_item_code"],),
    )
    existing = cur.fetchone()

    actual_values = dict(values)
    actual_values["created_at"] = now_db()
    actual_values["updated_at"] = now_db()

    if existing:
        columns = table_columns(cur, "service_items")
        set_parts, params = [], []
        for key, value in actual_values.items():
            if key in columns and key not in {"service_item_code", "created_at"}:
                set_parts.append(f"{key} = ?")
                params.append(value)
        params.append(actual_values["service_item_code"])
        cur.execute(
            f"UPDATE service_items SET {', '.join(set_parts)} "
            "WHERE service_item_code = ?",
            tuple(params),
        )
        return False

    insert_dynamic(cur, "service_items", actual_values)
    return True


def get_references_to_service_codes(cur, service_codes):
    result = {}

    if not service_codes:
        return result

    placeholders = ", ".join("?" for _ in service_codes)

    for table_name in ["charges", "payments", "cashbox_operations", "payment_allocations"]:
        if not table_exists(cur, table_name):
            continue

        for column_name in ["service_code", "base_service_code"]:
            if column_name not in table_columns(cur, table_name):
                continue

            cur.execute(
                f"SELECT COUNT(*) FROM {table_name} "
                f"WHERE {column_name} IN ({placeholders})",
                tuple(service_codes),
            )
            count = cur.fetchone()[0]
            if count:
                result[f"{table_name}.{column_name}"] = count

    return result


def get_service_item_references(cur):
    result = {}

    for table_name in ["charges", "payments", "cashbox_operations", "payment_allocations"]:
        if not table_exists(cur, table_name):
            continue
        if "service_item_code" not in table_columns(cur, table_name):
            continue

        cur.execute(
            f"SELECT COUNT(*) FROM {table_name} "
            "WHERE service_item_code IS NOT NULL AND TRIM(service_item_code) <> ''"
        )
        count = cur.fetchone()[0]
        if count:
            result[f"{table_name}.service_item_code"] = count

    return result


def count_rows(cur, table_name):
    if not table_exists(cur, table_name):
        return None
    cur.execute(f"SELECT COUNT(*) FROM {table_name}")
    return cur.fetchone()[0]


def reset_sqlite_sequence(cur, table_name):
    try:
        cur.execute("DELETE FROM sqlite_sequence WHERE name = ?", (table_name,))
    except sqlite3.OperationalError:
        # Таблица sqlite_sequence может отсутствовать в редких конфигурациях.
        pass


def write_report(lines):
    export_dir = paths.OSBB_EXPORTS_DIR / "cashier"
    export_dir.mkdir(parents=True, exist_ok=True)
    report_file = export_dir / (
        f"service_catalog_cleanup_{datetime.now():%Y-%m-%d_%H-%M-%S}.txt"
    )
    report_file.write_text("\n".join(lines), encoding="utf-8")
    return report_file


def clean_catalog(apply=False):
    conn = sqlite3.connect(get_db_file())
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    if not table_exists(cur, "service_catalog"):
        raise RuntimeError("Не найдена таблица service_catalog.")

    cur.execute("SELECT service_code FROM service_catalog ORDER BY id")
    old_codes = [row[0] for row in cur.fetchall()]

    canonical_codes = {service["service_code"] for service in CANONICAL_SERVICES}
    codes_to_delete = sorted(set(old_codes) - canonical_codes)

    service_item_count = count_rows(cur, "service_items") or 0
    barrier_access_count = count_rows(cur, "barrier_phone_access") or 0

    old_code_refs = get_references_to_service_codes(cur, codes_to_delete)
    service_item_refs = get_service_item_references(cur)

    lines = [
        "=" * 96,
        "ОЧИСТКА КАТАЛОГА СЕРВИСОВ",
        "=" * 96,
        f"DB: {get_db_file()}",
        f"MODE: {'TEST/WORK' if USE_TEST_DB else 'PROD'}",
        f"Apply: {apply}",
        "",
        f"Текущие коды ({len(old_codes)}): {', '.join(old_codes) if old_codes else '-'}",
        (
            f"Коды к удалению ({len(codes_to_delete)}): "
            f"{', '.join(codes_to_delete) if codes_to_delete else '-'}"
        ),
        f"Статей service_items к очистке: {service_item_count}",
        (
            "Телефонных доступов barrier_phone_access: "
            f"{barrier_access_count} (не удаляются данным модулем)"
        ),
        "",
        "Фиксированная формулировка:",
        "  BARRIER_PHONE = Телефонный доступ к шлагбауму",
        "  Тип: MONTHLY + признак is_access_control=1.",
        "  Начисляется при регистрации номера и отсутствии долга по парковке.",
        "",
    ]

    if old_code_refs or service_item_refs:
        lines.append("ОЧИСТКА ОТМЕНЕНА: в финансовых таблицах найдены ссылки.")
        for name, count in {**old_code_refs, **service_item_refs}.items():
            lines.append(f"  {name}: {count}")
        lines.append("")
        lines.append(
            "Сначала нужно перенести или классифицировать эти данные; ничего не изменено."
        )

        report_file = write_report(lines)
        print("\n".join(lines))
        print("\nОтчёт:", report_file)
        conn.close()
        return 2

    if not apply:
        lines.extend([
            "DRY RUN: база не изменялась.",
            "",
            "Будет сделано:",
            "  1. Очистка всех текущих service_items — они пока не используются.",
            "  2. Удаление старых кодов, включая BARRIER_CALL и GUEST_NIGHT.",
            "  3. Создание/обновление десяти утверждённых сервисов.",
            "  4. BARRIER_PHONE: ежемесячный телефонный доступ к шлагбауму.",
            "  5. BARRIER_PHONE_CONNECT: разовое/повторное подключение, тариф 200 грн.",
            "  6. Создание стартовой статьи 01_BarrierPhoneConnect.",
        ])

        report_file = write_report(lines)
        print("\n".join(lines))
        print("\nОтчёт:", report_file)
        conn.close()
        return 0

    try:
        # Пока service_items не имеют ссылок — начинаем их с чистого листа.
        if table_exists(cur, "service_items"):
            cur.execute("DELETE FROM service_items")
            reset_sqlite_sequence(cur, "service_items")

        # Обновляем либо добавляем окончательные базовые сервисы.
        for service in CANONICAL_SERVICES:
            cur.execute(
                "SELECT id FROM service_catalog WHERE service_code = ?",
                (service["service_code"],),
            )
            exists = cur.fetchone() is not None

            values = dict(service)
            values["created_at"] = now_db()
            values["updated_at"] = now_db()

            if exists:
                update_service_dynamic(cur, service["service_code"], values)
            else:
                insert_dynamic(cur, "service_catalog", values)

        # Добавляем единственную уже утверждённую разовую статью.
        for item in INITIAL_SERVICE_ITEMS:
            upsert_service_item(cur, item)

        # Удаляем всё, что не входит в утверждённый каталог.
        # Проверка ссылок уже была выполнена выше.
        if codes_to_delete:
            placeholders = ", ".join("?" for _ in codes_to_delete)
            cur.execute(
                f"DELETE FROM service_catalog WHERE service_code IN ({placeholders})",
                tuple(codes_to_delete),
            )

        # Системное событие аудита полезно, но не должно отменять очистку,
        # если окружение временно не может записать аудит.
        if audit_log is not None:
            try:
                audit_log(
                    conn=conn,
                    actor_type="system",
                    operator_id="system",
                    user_id="system",
                    action_type="service_catalog_cleaned",
                    table_name="service_catalog",
                    row_id="",
                    field_name="",
                    old_value=", ".join(old_codes),
                    new_value=", ".join(sorted(canonical_codes)),
                    source_context="reset_service_catalog_v2.py",
                    comment=(
                        "Каталог сервисов очищен до утверждённого набора. "
                        "Зафиксированы BARRIER_PHONE (ежемесячный телефонный доступ) и "
                        "BARRIER_PHONE_CONNECT (подключение/повторное подключение, 200 грн)."
                    ),
                    extra={
                        "deleted_codes": codes_to_delete,
                        "cleared_service_items": service_item_count,
                    },
                    commit=False,
                )
            except Exception as exc:
                print("WARNING: запись аудита не создана:", exc)

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    lines.extend([
        "ОЧИСТКА ВЫПОЛНЕНА.",
        "",
        "Стартовая статья:",
        "  01_BarrierPhoneConnect | BARRIER_PHONE_CONNECT | 200 грн",
        "",
        "Итоговый каталог:",
    ])
    for service in CANONICAL_SERVICES:
        lines.append(
            f"  {service['service_code']:20} | "
            f"{service['service_type']:14} | {service['service_name']}"
        )

    report_file = write_report(lines)
    print("\n".join(lines))
    print("\nОтчёт:", report_file)
    return 0


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Очистить новый каталог сервисов и зафиксировать "
            "BARRIER_PHONE и BARRIER_PHONE_CONNECT как окончательные сервисы телефонного доступа."
        )
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Фактически применить очистку. Без ключа выполняется dry-run.",
    )
    args = parser.parse_args()
    raise SystemExit(clean_catalog(apply=args.apply))


if __name__ == "__main__":
    main()
