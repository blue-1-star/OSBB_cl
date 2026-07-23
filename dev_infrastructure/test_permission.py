#!/usr/bin/env python3
"""
test_permission.py
=====================

Вызывает has_permission()/has_guard_workspace_access() из access_control.py
НАПРЯМУЮ, против явно указанного файла БД — в обход config.py/USE_TEST_DB
и в обход всего Telegram-бота целиком. Нужен, чтобы убрать все посредники
и увидеть: сама функция даёт разный ответ на разных файлах БД, или нет.

Ничего не пишет в БД — все вызовы read-only по своей природе (has_permission
сам только читает).

Как запускать:

    python test_permission.py --access-control-dir "G:\\Programming\\OSBB_cl" --db "G:\\Programming\\Py\\OSBB\\Data\\db\\osbb_test.db" --user 210312208
"""

import argparse
import sys
from pathlib import Path


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--access-control-dir", required=True, help=r'Папка, где лежит access_control.py, например "G:\Programming\OSBB_cl"')
    ap.add_argument("--db", required=True, help="Явный путь к файлу БД для проверки")
    ap.add_argument("--user", required=True, help="telegram_user_id для проверки")
    ap.add_argument("--cashbox", default="O", help="Код кассы для проверки охраны (по умолчанию O)")
    args = ap.parse_args()

    ac_dir = Path(args.access_control_dir).resolve()
    sys.path.insert(0, str(ac_dir))

    import access_control as ac

    db_path = Path(args.db).resolve()
    if not db_path.exists():
        print(f"Файл БД не найден: {db_path}", file=sys.stderr)
        sys.exit(1)

    # Подмена на явный путь — в обход config.py/USE_TEST_DB полностью,
    # чтобы протестировать именно эту функцию против именно этого файла.
    ac.get_db_file = lambda: db_path

    print(f"access_control.py взят из: {ac_dir}")
    print(f"БД для проверки: {db_path}")
    print(f"user_id: {args.user}")
    print()

    ready, msg = ac.schema_ready()
    print(f"schema_ready(): {ready}  {msg}")
    print()

    guard = ac.has_guard_workspace_access(args.user, cashbox_code=args.cashbox)
    print(f"has_guard_workspace_access(cashbox='{args.cashbox}'): {guard}")

    remote = ac.has_permission(args.user, "service_orders", "VIEW", scope_type="SERVICE_CATEGORY", scope_value="REMOTE")
    access = ac.has_permission(args.user, "service_orders", "VIEW", scope_type="SERVICE_CATEGORY", scope_value="ACCESS")
    print(f"has_permission(service_orders/VIEW/REMOTE): {remote}")
    print(f"has_permission(service_orders/VIEW/ACCESS): {access}")
    print(f"-> has_service_workspace_access эквивалент: {remote or access}")

    print()
    print("Итог: если этот вывод отличается между двумя файлами БД для одного")
    print("и того же user_id — значит, дело реально в данных этих таблиц")
    print("(access_user_roles/access_role_permissions/access_user_permissions),")
    print("и compare_db.py что-то не уловил. Если ответы ОДИНАКОВЫ на обоих")
    print("файлах — значит, разница в поведении бота была вызвана чем-то")
    print("ДРУГИМ, не содержимым БД (например, тем, какой именно файл бот")
    print("реально открывал в момент теста).")


if __name__ == "__main__":
    main()