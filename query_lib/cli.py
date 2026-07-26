#!/usr/bin/env python
"""
Справочно-аналитические запросы к БД OSBB

Примеры:
  python -m query_lib.cli find 8092
  python -m query_lib.cli cars --min 2
  python -m query_lib.cli missing-mode
  python -m query_lib.cli debtors
  python -m query_lib.cli non-standard
  python -m query_lib.cli apartment 111
  python -m query_lib.cli last
  python -m query_lib.cli last 10
  python -m query_lib.cli tariff-set PARKING_DAY
  python -m query_lib.cli verify-log OTHER 31 "текст находки"
  python -m query_lib.cli verify-list
  python -m query_lib.cli service-add КОД ГРУППА "Название" единица --category PARKING --needs-review
  python -m query_lib.cli fio Стриха
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from query_lib.queries import (
    find_by_plate_fragment,
    apartments_with_multiple_cars,
    apartments_with_missing_parking_mode,
    parking_debtors,
    non_standard_plates,
    vehicles_by_apartment,
    last_payments,
    set_tariff,
    log_verification_task,
    list_open_verification_tasks,
    add_service_catalog_entry,
    find_by_fio,
)


def print_table(rows, headers=None):
    """Универсальный вывод таблицы"""
    if not rows:
        print("Нет данных")
        return

    # Если rows — список объектов sqlite3.Row
    if hasattr(rows[0], 'keys'):
        # Если заголовки не переданы — берём их из первой строки
        if headers is None:
            headers = list(rows[0].keys())
        # Печатаем заголовки
        print(" | ".join(str(h) for h in headers))
        print("-" * 60)
        for row in rows:
            print(" | ".join(str(row[h]) for h in headers))
        return

    # Если rows — список словарей
    if isinstance(rows[0], dict):
        if headers is None:
            headers = list(rows[0].keys())
        print(" | ".join(str(h) for h in headers))
        print("-" * 60)
        for row in rows:
            print(" | ".join(str(row.get(h, "")) for h in headers))
        return

    # Если rows — список кортежей
    if isinstance(rows[0], (tuple, list)):
        if headers is None:
            headers = [f"col_{i}" for i in range(len(rows[0]))]
        print(" | ".join(str(h) for h in headers))
        print("-" * 60)
        for row in rows:
            print(" | ".join(str(x) for x in row))
        return

    # fallback
    for row in rows:
        print(row)


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return

    cmd = args[0]

    if cmd == "find" and len(args) > 1:
        fragment = args[1]
        rows = find_by_plate_fragment(fragment)
        print_table(rows)

    elif cmd == "cars":
        min_count = 2
        if "--min" in args:
            idx = args.index("--min")
            if idx + 1 < len(args):
                min_count = int(args[idx + 1])
        rows = apartments_with_multiple_cars(min_count)
        print_table(rows)

    elif cmd == "missing-mode":
        rows = apartments_with_missing_parking_mode()
        print_table(rows)

    elif cmd == "debtors":
        period = args[1] if len(args) > 1 else None
        rows = parking_debtors(period)
        print_table(rows)

    elif cmd == "non-standard":
        rows = non_standard_plates()
        print_table(rows)

    elif cmd == "apartment":
        if len(args) < 2:
            print("Укажите номер квартиры: python -m query_lib.cli apartment 111")
            return
        apt = args[1]
        rows = vehicles_by_apartment(apt)
        print_table(rows)

    elif cmd == "last":
        limit = 20
        if len(args) > 1:
            try:
                limit = int(args[1])
            except ValueError:
                pass
        rows = last_payments(limit)
        print_table(rows)

    elif cmd == "tariff-set":
        if len(args) < 2:
            print("Использование: tariff-set SERVICE_CODE [СУММА] [ГГГГ-ММ-ДД] [комментарий...]")
            print("Пример: tariff-set PARKING_DAY 250 2026-09-01 повышение тарифа")
            print("Если сумму не указать — возьмётся из предыдущего периода этого же кода услуги.")
            return

        import re
        service_code = args[1]
        amount = None
        valid_from = None
        comment_parts = []

        for token in args[2:]:
            if amount is None and re.match(r'^\d+(\.\d+)?$', token):
                amount = float(token)
            elif valid_from is None and re.match(r'^\d{4}-\d{2}-\d{2}$', token):
                valid_from = token
            else:
                comment_parts.append(token)

        comment = " ".join(comment_parts) if comment_parts else None

        try:
            new_id = set_tariff(service_code, amount=amount, valid_from=valid_from, comment=comment)
            print(f"Добавлен тариф id={new_id}: {service_code} = {amount if amount is not None else '(взято из предыдущего периода)'}, действует с {valid_from or '(сегодня)'}")
        except ValueError as e:
            print(f"Ошибка: {e}")

    elif cmd == "verify-log":
        if len(args) < 3:
            print("Использование: verify-log ISSUE_TYPE НОМЕР_КВАРТИРЫ текст...")
            print("issue_type: VEHICLE_UNLINKED | MISSING_VEHICLE | CHECK_PLATE | MISSING_PARKING_MODE | AMOUNT_MISMATCH | OTHER")
            return
        issue_type = args[1]
        apartment_number = args[2]
        description = " ".join(args[3:]) if len(args) > 3 else ""
        task_id = log_verification_task(
            apartment_number=apartment_number,
            issue_type=issue_type,
            description=description,
            raised_by="cli",
        )
        print(f"Записано в журнал согласования: id={task_id}")

    elif cmd == "verify-list":
        rows = list_open_verification_tasks()
        print_table(rows)

    elif cmd == "service-add":
        if len(args) < 5:
            print("Использование: service-add КОД ГРУППА \"НАЗВАНИЕ\" ЕДИНИЦА [--category КАТЕГОРИЯ] [--type ТИП] [--needs-review]")
            print("Пример: service-add PARKING_UNSPECIFIED MONTHLY \"Парковка (режим не определён)\" service --category PARKING --type MONTHLY --needs-review")
            return
        service_code, service_group, service_name, unit = args[1], args[2], args[3], args[4]
        rest = args[5:]
        category = None
        service_type = None
        needs_review = 0
        i = 0
        while i < len(rest):
            if rest[i] == "--category" and i + 1 < len(rest):
                category = rest[i + 1]; i += 2
            elif rest[i] == "--type" and i + 1 < len(rest):
                service_type = rest[i + 1]; i += 2
            elif rest[i] == "--needs-review":
                needs_review = 1; i += 1
            else:
                i += 1
        new_id, created = add_service_catalog_entry(
            service_code, service_group, service_name, unit,
            service_type=service_type, category=category,
            manual_review_required=needs_review,
        )
        if created:
            print(f"Создана услуга: id={new_id}, code={service_code}")
        else:
            print(f"Услуга {service_code} уже существует (id={new_id}), ничего не изменено.")

    elif cmd == "fio":
        if len(args) < 2:
            print("Использование: fio ФРАГМЕНТ_ИМЕНИ")
            print("Учитывает украинское и русское написание (Стріха / Стриха — одно и то же).")
            return
        fragment = " ".join(args[1:])
        rows = find_by_fio(fragment)
        if not rows:
            print("Ничего не найдено.")
            return
        for r in rows:
            print(f"{r['фио']} | кв.{r['квартира']}")
            if not r['авто']:
                print("    авто нет")
            for v in r['авто']:
                model = f" ({v['марка']})" if v['марка'] else ""
                mode = f", режим: {v['режим']}" if v['режим'] else ""
                print(f"    {v['номер']}{model}{mode}")

    else:
        print(f"Неизвестная команда: {cmd}")
        print(__doc__)


if __name__ == "__main__":
    main()