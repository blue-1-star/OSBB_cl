"""
Импорт фактических оплат с листа «Лист1» файла Охорона.xlsx
в отдельную Центральную кассу C.

Ключевое правило:
- Лист1 — самостоятельный источник прихода, не копия Sheet1.
- Платежи Sheet1 НЕ перемещаются и НЕ используются для дедупликации.
- Возможное совпадение квартиры/суммы лишь показывается предупреждением;
  оно не блокирует импорт, потому что источники считаются автономными.
- Повторный запуск безопасен: у каждой ячейки-источника свой source_ref.
- vehicle_id намеренно не заполняется: лист не содержит госномеров,
  а отдельная строка может объединять оплату за 2–3 автомобиля.
- Смешанные записи тарифа (например «1-д і сутки») не импортируются
  автоматически: они переходят в ручную проверку.
"""

from __future__ import annotations

from pathlib import Path
import argparse
import re
import sqlite3
import sys
import zipfile
import xml.etree.ElementTree as ET
from calendar import monthrange
from datetime import datetime
from decimal import Decimal, InvalidOperation

OSBB_ROOT = Path(__file__).resolve().parent
PY_ROOT = OSBB_ROOT.parent
if str(PY_ROOT) not in sys.path:
    sys.path.insert(0, str(PY_ROOT))

from config import paths, USE_TEST_DB
from audit_logger import audit_log
from cashier_journal import (
    now_db,
    norm_text,
    norm_apartment,
    table_exists,
    table_columns,
    insert_dynamic,
    update_dynamic,
    ensure_service_catalog,
    ensure_service_item,
    recalc_cashbox_balance,
    find_vehicle,
    create_cashbox_operation,
    duplicate_operation_exists,
    money,
)


DEFAULT_SOURCE_FILE = (
    getattr(paths, "OSBB_RAW_TYPED_DIR", paths.OSBB_RAW_DIR / "typed") / "Охорона.xlsx"
)

SHEET_NAME = "Лист1"
CENTRAL_CASHBOX = "C"
SOURCE_TYPE = "ohorona_list1_central"
SOURCE_NAME = "import_ohorona_list1_central"

MONTH_COLUMNS = {
    4: ("2026-05", "травень"),
    5: ("2026-06", "червень"),
}
NOTE_COLUMN = 6

NS_MAIN = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
NS_REL = {"r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}
NS_PKG_REL = {"r": "http://schemas.openxmlformats.org/package/2006/relationships"}


def get_db_file() -> Path:
    return paths.OSBB_TEST_DB_FILE if USE_TEST_DB else paths.OSBB_DB_FILE


def now_file() -> str:
    return datetime.now().strftime("%Y-%m-%d_%H-%M-%S")


def parse_amount(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)

    text = norm_text(value).replace(" ", "").replace(",", ".")
    if not text:
        return None

    # Чистое число в строке можно считать суммой.
    if not re.fullmatch(r"[-+]?\d+(?:\.\d+)?", text):
        return None

    try:
        return float(Decimal(text))
    except (InvalidOperation, ValueError):
        return None


def column_number(reference: str) -> int:
    letters = re.match(r"([A-Z]+)", reference or "")
    if not letters:
        return 0

    value = 0
    for char in letters.group(1):
        value = value * 26 + (ord(char) - ord("A") + 1)
    return value


def parse_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    path = "xl/sharedStrings.xml"
    if path not in archive.namelist():
        return []

    root = ET.fromstring(archive.read(path))
    result = []

    for item in root.findall("m:si", NS_MAIN):
        parts = [node.text or "" for node in item.findall(".//m:t", NS_MAIN)]
        result.append("".join(parts))

    return result


def sheet_xml_path(archive: zipfile.ZipFile, requested_name: str) -> str:
    workbook = ET.fromstring(archive.read("xl/workbook.xml"))
    relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))

    rel_targets = {
        item.attrib["Id"]: item.attrib["Target"]
        for item in relationships.findall("r:Relationship", NS_PKG_REL)
    }

    for sheet in workbook.findall("m:sheets/m:sheet", NS_MAIN):
        if sheet.attrib.get("name") != requested_name:
            continue

        rel_id = sheet.attrib.get(f"{{{NS_REL['r']}}}id")
        target = rel_targets.get(rel_id)
        if not target:
            break

        target = target.lstrip("/")
        if not target.startswith("xl/"):
            target = "xl/" + target
        return target

    raise RuntimeError(f"Лист {requested_name!r} не найден в файле Excel.")


def read_xlsx_rows(source_file: Path, sheet_name: str) -> list[dict[int, object]]:
    """
    Небольшой read-only XLSX reader на стандартной библиотеке.
    Нам нужны только значения A:F Лист1; формулы в этом листе не используются.
    """
    with zipfile.ZipFile(source_file) as archive:
        shared = parse_shared_strings(archive)
        xml_path = sheet_xml_path(archive, sheet_name)
        root = ET.fromstring(archive.read(xml_path))

    rows: list[dict[int, object]] = []

    for row_node in root.findall("m:sheetData/m:row", NS_MAIN):
        record: dict[int, object] = {
            "_row": int(row_node.attrib.get("r", 0)),
        }

        for cell in row_node.findall("m:c", NS_MAIN):
            ref = cell.attrib.get("r", "")
            col = column_number(ref)
            cell_type = cell.attrib.get("t", "")

            value_node = cell.find("m:v", NS_MAIN)
            inline_node = cell.find("m:is/m:t", NS_MAIN)

            if cell_type == "inlineStr":
                value: object = (inline_node.text if inline_node is not None else "") or ""
            elif value_node is None:
                value = ""
            else:
                raw = value_node.text or ""
                if cell_type == "s":
                    try:
                        value = shared[int(raw)]
                    except (ValueError, IndexError):
                        value = raw
                elif cell_type == "b":
                    value = raw == "1"
                elif cell_type == "str":
                    value = raw
                else:
                    try:
                        number = float(raw)
                        value = int(number) if number.is_integer() else number
                    except ValueError:
                        value = raw

            record[col] = value

        rows.append(record)

    return rows


def month_end(period_code: str) -> str:
    year, month = map(int, period_code.split("-"))
    return f"{year:04d}-{month:02d}-{monthrange(year, month)[1]:02d}"


def source_ref(row_no: int, period_code: str, source_part: str = "month") -> str:
    return f"ohorona_list1:row:{row_no}:period:{period_code}:part:{source_part}"


def operation_source_ref(row_no: int, period_code: str, source_part: str = "month") -> str:
    return f"cashier_backfill:{source_ref(row_no, period_code, source_part)}"


def normalize_tariff(raw_value) -> tuple[str, str]:
    """
    Возвращает (base_service_code, warning).

    Важно: аббревиатура «1-д» также означает Day.
    Поэтому запись вроде «1-д і сутки» — смешанный тариф, а не Night.
    Такие строки всегда отправляются на ручную проверку, чтобы не
    записать общую сумму квартиры в одну ошибочную статью.
    """
    raw = norm_text(raw_value).lower().replace("ё", "е")
    compact = raw.replace("і", "и").replace(" ", "")

    has_day_word = "день" in compact or "day" in compact
    has_day_abbrev = bool(
        re.search(r"\d+\s*-\s*д(?=\s|$|[іи,+;/])", raw)
    )
    has_day = has_day_word or has_day_abbrev

    has_night = "сутки" in compact or "night" in compact

    if has_day and has_night:
        return "", (
            f"Смешанный тариф {raw_value!r}: Day и Night в одной строке. "
            "Нельзя автоматически отнести общую оплату к одному виду парковки."
        )
    if has_day:
        return "PARKING_DAY", ""
    if has_night:
        return "PARKING_NIGHT", ""

    return "", f"Не распознан тариф {raw_value!r}"


def service_item_code(period_code: str, base_service_code: str) -> str:
    _, month = period_code.split("-")
    suffix = "Day" if base_service_code == "PARKING_DAY" else "Night"
    return f"{month}_26_{suffix}"


def ensure_monthly_item(cur: sqlite3.Cursor, period_code: str, base_service_code: str) -> str:
    code = service_item_code(period_code, base_service_code)
    label = "Day" if base_service_code == "PARKING_DAY" else "Night"
    name = f"Парковка {label} — {period_code}"

    ensure_service_catalog(cur, base_service_code)
    ensure_service_item(
        cur=cur,
        item_code=code,
        base_service_code=base_service_code,
        item_name=name,
        service_type="MONTHLY",
        period_code=period_code,
        amount_default=None,
        date_from=f"{period_code}-01",
        date_to=month_end(period_code),
        description="Историческая статья, созданная при импорте Лист1 Охорона.xlsx",
        comment="Дата платежа техническая: использован последний день календарного месяца.",
    )
    return code


def ensure_central_cashbox(cur: sqlite3.Cursor) -> tuple[bool, bool]:
    if not table_exists(cur, "cashboxes"):
        raise RuntimeError("Не найдена таблица cashboxes. Сначала выполните migrate_cashier_core.py.")

    cur.execute("SELECT id FROM cashboxes WHERE cashbox_code = ?", (CENTRAL_CASHBOX,))
    exists = cur.fetchone() is not None

    values = {
        "cashbox_code": CENTRAL_CASHBOX,
        "cashbox_name": "Центральная касса",
        "currency": "UAH",
        "initial_balance": 0,
        "current_balance": 0,
        "is_active": 1,
        "comment": (
            "Центральная касса ОСББ. "
            "Лист1 Охорона.xlsx — самостоятельный исторический источник поступлений."
        ),
        "created_at": now_db(),
        "updated_at": now_db(),
    }

    if exists:
        cols = table_columns(cur, "cashboxes")
        update_values = {
            key: value
            for key, value in values.items()
            if key in cols and key not in {"cashbox_code", "created_at"}
        }
        from cashier_journal import update_dynamic_by
        update_dynamic_by(cur, "cashboxes", "cashbox_code", CENTRAL_CASHBOX, update_values)
        return False, True

    insert_dynamic(cur, "cashboxes", values)
    return True, False


def payment_by_source_ref(cur: sqlite3.Cursor, ref: str) -> dict | None:
    if not table_exists(cur, "payments"):
        return None

    cols = table_columns(cur, "payments")
    if "source_ref" not in cols:
        return None

    cur.execute("SELECT * FROM payments WHERE source_ref = ?", (ref,))
    row = cur.fetchone()
    return dict(row) if row else None


def possible_prior_sheet1_overlap(
    cur: sqlite3.Cursor,
    apartment_number: str,
    amount: float,
) -> int:
    """
    Это только предупреждение. По решению пользователя Лист1 — автономный источник,
    поэтому совпадение суммы/квартиры НЕ считается дубликатом и НЕ блокирует импорт.
    """
    if not table_exists(cur, "payments"):
        return 0

    cols = table_columns(cur, "payments")
    if "apartment_number" not in cols or "amount" not in cols:
        return 0

    where = [
        "apartment_number = ?",
        "ABS(COALESCE(amount, 0) - ?) < 0.00001",
    ]
    params: list[object] = [apartment_number, float(amount)]

    if "source" in cols:
        where.append("COALESCE(source, '') <> ?")
        params.append(SOURCE_NAME)

    cur.execute(
        f"SELECT COUNT(*) FROM payments WHERE {' AND '.join(where)}",
        tuple(params),
    )
    return int(cur.fetchone()[0] or 0)


def parse_note_extra(raw_note, row_no: int, apartment: str, tariff: str) -> dict | None:
    """
    F может содержать дополнительную оплату в виде «450-квітень», «200-липень».
    Если период указан ясно — планируем отдельную операцию.
    Непонятные значения F выносятся в review.
    """
    text = norm_text(raw_note)
    if not text:
        return None

    numeric = parse_amount(raw_note)
    if numeric is not None:
        return {
            "kind": "review",
            "reason": (
                f"В колонке F указан отдельный числовой платёж {money(numeric)} "
                "без месяца. Нужна ручная дата/период."
            ),
        }

    match = re.search(
        r"(?P<amount>\d+(?:[.,]\d+)?)\s*-\s*"
        r"(?P<month>квітень|апрель|травень|май|червень|июнь|липень|июль)",
        text.lower(),
    )
    if not match:
        return {
            "kind": "review",
            "reason": f"Непонятное дополнительное примечание F: {text!r}",
        }

    amount = float(match.group("amount").replace(",", "."))
    month_word = match.group("month")

    month_map = {
        "квітень": "2026-04",
        "апрель": "2026-04",
        "травень": "2026-05",
        "май": "2026-05",
        "червень": "2026-06",
        "июнь": "2026-06",
        "липень": "2026-07",
        "июль": "2026-07",
    }

    return {
        "kind": "ready",
        "amount": amount,
        "period_code": month_map[month_word],
        "source_part": "note",
        "note": text,
    }


def plan_payment(
    cur: sqlite3.Cursor,
    *,
    row_no: int,
    apartment: str,
    tariff_raw,
    amount: float,
    period_code: str,
    source_part: str,
    source_note: str = "",
) -> dict:
    plan = {
        "row": row_no,
        "source_part": source_part,
        "status": "ready",
        "reason": "",
        "apartment_number": apartment,
        "tariff_raw": norm_text(tariff_raw),
        "period_code": period_code,
        "operation_date": month_end(period_code),
        "amount": float(amount),
        "base_service_code": "",
        "service_item_code": "",
        "vehicle_id": None,
        "payment_source_ref": source_ref(row_no, period_code, source_part),
        "operation_source_ref": operation_source_ref(row_no, period_code, source_part),
        "possible_sheet1_matches": 0,
        "comment": source_note,
    }

    if not apartment:
        plan["status"] = "review"
        plan["reason"] = "Не определена квартира: строка не может быть импортирована автоматически."
        return plan

    base_service_code, tariff_warning = normalize_tariff(tariff_raw)
    if not base_service_code:
        plan["status"] = "review"
        plan["reason"] = tariff_warning
        return plan

    if payment_by_source_ref(cur, plan["payment_source_ref"]) or duplicate_operation_exists(
        cur, plan["operation_source_ref"]
    ):
        plan["status"] = "duplicate"
        plan["reason"] = "Эта ячейка Лист1 уже была импортирована по source_ref."
        return plan

    # Лист1 содержит оплату по квартире и тарифной записи (например, 2-день),
    # но не содержит номера автомобиля. Даже при одной машине не угадываем vehicle_id:
    # это защитит от ошибочного назначения оплаты «не тому» авто.
    # Распределение на конкретные vehicle/charges будет отдельным этапом.
    plan["vehicle_id"] = None

    plan["base_service_code"] = base_service_code
    plan["service_item_code"] = service_item_code(period_code, base_service_code)
    plan["possible_sheet1_matches"] = possible_prior_sheet1_overlap(
        cur=cur,
        apartment_number=apartment,
        amount=amount,
    )

    if plan["possible_sheet1_matches"]:
        plan["reason"] = (
            "Есть платежи из других источников с такой же квартирой и суммой; "
            "по утверждённому правилу НЕ считаем это дубликатом и импортируем отдельно."
        )

    return plan


def build_plans(cur: sqlite3.Cursor, rows: list[dict[int, object]]) -> tuple[list[dict], list[dict]]:
    plans: list[dict] = []
    reviews: list[dict] = []

    current_apartment = ""
    for row in rows:
        row_no = int(row.get("_row", 0))

        # Первая строка — заголовок.
        if row_no <= 1:
            continue

        raw_apartment = row.get(2)
        apartment = norm_apartment(raw_apartment)
        if apartment:
            current_apartment = apartment
        else:
            apartment = current_apartment

        tariff_raw = row.get(3)
        may_value = row.get(4)
        june_value = row.get(5)
        note_value = row.get(NOTE_COLUMN)

        # Пустая строка/строка общего итога не является оплатой.
        if not any([
            norm_text(raw_apartment),
            norm_text(tariff_raw),
            norm_text(may_value),
            norm_text(june_value),
            norm_text(note_value),
        ]):
            continue

        for column, (period_code, month_name) in MONTH_COLUMNS.items():
            raw_amount = row.get(column)
            amount = parse_amount(raw_amount)

            if amount is None:
                if norm_text(raw_amount):
                    reviews.append({
                        "row": row_no,
                        "source_part": month_name,
                        "apartment_number": apartment,
                        "tariff_raw": norm_text(tariff_raw),
                        "amount": "",
                        "reason": (
                            f"В колонке «{month_name}» нечисловая отметка "
                            f"{norm_text(raw_amount)!r}; это не будет импортировано автоматически."
                        ),
                    })
                continue

            if amount <= 0:
                continue

            plan = plan_payment(
                cur=cur,
                row_no=row_no,
                apartment=apartment,
                tariff_raw=tariff_raw,
                amount=amount,
                period_code=period_code,
                source_part=f"col_{column}",
                source_note=f"Источник Лист1, колонка «{month_name}».",
            )
            plans.append(plan)

        extra = parse_note_extra(note_value, row_no, apartment, norm_text(tariff_raw))
        if extra:
            if extra["kind"] == "ready":
                plans.append(plan_payment(
                    cur=cur,
                    row_no=row_no,
                    apartment=apartment,
                    tariff_raw=tariff_raw,
                    amount=extra["amount"],
                    period_code=extra["period_code"],
                    source_part=extra["source_part"],
                    source_note=(
                        "Источник Лист1, дополнительная запись из колонки F: "
                        f"{extra['note']}"
                    ),
                ))
            else:
                reviews.append({
                    "row": row_no,
                    "source_part": "F",
                    "apartment_number": apartment,
                    "tariff_raw": norm_text(tariff_raw),
                    "amount": "",
                    "reason": extra["reason"],
                })

    return plans, reviews


def insert_payment(cur: sqlite3.Cursor, plan: dict, operation_id: int) -> int:
    values = {
        "payment_date": plan["operation_date"],
        "amount": plan["amount"],
        "apartment_number": plan["apartment_number"],
        "vehicle_id": plan["vehicle_id"],
        "service_code": plan["base_service_code"],
        "base_service_code": plan["base_service_code"],
        "service_item_code": plan["service_item_code"],
        "service_type": "MONTHLY",
        "period_code": plan["period_code"],
        "cashbox_code": CENTRAL_CASHBOX,
        "cashbox_operation_id": operation_id,
        "source": SOURCE_NAME,
        "source_ref": plan["payment_source_ref"],
        "operator_id": "ohorona_list1_backfill",
        "comment": (
            f"Историческая фактическая оплата. {plan['comment']} "
            f"Техническая дата платежа: {plan['operation_date']} "
            "(точная дата в Лист1 не указана)."
        ),
        "created_at": now_db(),
        "updated_at": now_db(),
    }
    return insert_dynamic(cur, "payments", values)


def apply_plans(conn: sqlite3.Connection, plans: list[dict], reviews: list[dict]) -> dict:
    cur = conn.cursor()
    result = {
        "cashbox_created": False,
        "payments_inserted": 0,
        "operations_inserted": 0,
        "duplicates": 0,
        "review": len(reviews),
        "possible_sheet1_matches": 0,
        "balance_C": 0,
    }

    created, _updated = ensure_central_cashbox(cur)
    result["cashbox_created"] = created

    for plan in plans:
        if plan["status"] == "duplicate":
            result["duplicates"] += 1
            continue
        if plan["status"] != "ready":
            result["review"] += 1
            continue

        ensure_service_catalog(cur, plan["base_service_code"])
        item_code = ensure_monthly_item(
            cur=cur,
            period_code=plan["period_code"],
            base_service_code=plan["base_service_code"],
        )
        plan["service_item_code"] = item_code

        # Сначала создаём платеж, затем кассовую операцию и связываем их.
        payment_id = insert_dynamic(cur, "payments", {
            "payment_date": plan["operation_date"],
            "amount": plan["amount"],
            "apartment_number": plan["apartment_number"],
            "vehicle_id": plan["vehicle_id"],
            "service_code": plan["base_service_code"],
            "base_service_code": plan["base_service_code"],
            "service_item_code": item_code,
            "service_type": "MONTHLY",
            "period_code": plan["period_code"],
            "cashbox_code": CENTRAL_CASHBOX,
            "source": SOURCE_NAME,
            "source_ref": plan["payment_source_ref"],
            "operator_id": "ohorona_list1_backfill",
            "comment": (
                f"Историческая фактическая оплата. {plan['comment']} "
                f"Тарифная запись: {plan['tariff_raw']}. "
                f"Техническая дата: {plan['operation_date']}; vehicle_id не назначен."
            ),
            "created_at": now_db(),
            "updated_at": now_db(),
        })

        operation_id = create_cashbox_operation(
            cur=cur,
            operation_date=plan["operation_date"],
            cashbox_code=CENTRAL_CASHBOX,
            operation_type="historical_income",
            direction="in",
            amount=plan["amount"],
            base_service_code=plan["base_service_code"],
            service_item_code=item_code,
            service_type="MONTHLY",
            period_code=plan["period_code"],
            apartment_number=plan["apartment_number"],
            vehicle_id=plan["vehicle_id"],
            payment_id=payment_id,
            source_type=SOURCE_TYPE,
            source_ref=plan["operation_source_ref"],
            operator_id="ohorona_list1_backfill",
            actor_type="system",
            comment=(
                f"Охорона.xlsx Лист1, строка {plan['row']}; {plan['comment']} "
                f"Дата техническая: {plan['operation_date']}."
            ),
        )

        update_dynamic(cur, "payments", payment_id, {
            "cashbox_operation_id": operation_id,
        })

        audit_log(
            conn=conn,
            actor_type="system",
            operator_id="ohorona_list1_backfill",
            user_id="ohorona_list1_backfill",
            action_type="central_cashbox_payment_backfilled",
            table_name="payments",
            row_id=payment_id,
            field_name="amount",
            old_value="",
            new_value=plan["amount"],
            source_context="import_ohorona_list1_to_central_cashbox.py",
            comment=(
                f"Лист1 строка {plan['row']}; кв. {plan['apartment_number']}; "
                f"{plan['period_code']}; {plan['base_service_code']}; "
                f"тарифная запись: {plan['tariff_raw']}; vehicle_id намеренно не задан"
            ),
            extra={
                "cashbox_code": CENTRAL_CASHBOX,
                "cashbox_operation_id": operation_id,
                "service_item_code": item_code,
                "source_ref": plan["payment_source_ref"],
                "possible_other_source_matches": plan["possible_sheet1_matches"],
            },
            commit=False,
        )

        result["payments_inserted"] += 1
        result["operations_inserted"] += 1
        if plan["possible_sheet1_matches"]:
            result["possible_sheet1_matches"] += 1

    result["balance_C"] = recalc_cashbox_balance(cur, CENTRAL_CASHBOX)
    return result


def make_report(
    report_file: Path,
    source_file: Path,
    plans: list[dict],
    reviews: list[dict],
    result: dict | None,
) -> None:
    lines = [
        "=" * 132,
        "ИМПОРТ ОХОРОНА.XLSX / ЛИСТ1 В ЦЕНТРАЛЬНУЮ КАССУ C",
        "=" * 132,
        f"Generated : {now_db()}",
        f"Source    : {source_file}",
        f"Sheet     : {SHEET_NAME}",
        "Rule      : Лист1 — самостоятельный источник фактически полученных оплат.",
        "            Совпадения с Sheet1 не считаются дубликатами автоматически.",
        "",
        "ПЛАН АВТОИМПОРТА:",
        (
            "row | part | apartment | tariff | period | technical date | amount | "
            "base service | item | possible Sheet1 matches | status | warning"
        ),
        "-" * 132,
    ]

    for plan in plans:
        lines.append(
            f"{plan['row']} | {plan['source_part']} | {plan['apartment_number'] or '-'} | "
            f"{plan['tariff_raw'] or '-'} | {plan['period_code']} | "
            f"{plan['operation_date']} | {money(plan['amount'])} | "
            f"{plan['base_service_code'] or '-'} | {plan['service_item_code'] or '-'} | "
            f"{plan['possible_sheet1_matches']} | {plan['status']} | "
            f"{plan['reason'] or '-'}"
        )

    review_plans = [p for p in plans if p["status"] == "review"]

    lines.extend([
        "",
        "СТРОКИ ДЛЯ РУЧНОЙ ПРОВЕРКИ:",
        "row | part | apartment | tariff | amount | reason",
        "-" * 132,
    ])

    if review_plans or reviews:
        for plan in review_plans:
            lines.append(
                f"{plan['row']} | {plan['source_part']} | {plan['apartment_number'] or '-'} | "
                f"{plan['tariff_raw'] or '-'} | {money(plan['amount'])} | {plan['reason']}"
            )

        for item in reviews:
            lines.append(
                f"{item['row']} | {item['source_part']} | {item['apartment_number'] or '-'} | "
                f"{item['tariff_raw'] or '-'} | {item['amount'] or '-'} | {item['reason']}"
            )
    else:
        lines.append("Нет.")

    ready = sum(1 for p in plans if p["status"] == "ready")
    duplicate = sum(1 for p in plans if p["status"] == "duplicate")
    total_ready = sum(p["amount"] for p in plans if p["status"] == "ready")
    overlap_warnings = sum(1 for p in plans if p["possible_sheet1_matches"])
    review_total = len(review_plans) + len(reviews)

    lines.extend([
        "",
        "СВОДКА:",
        f"ready: {ready}",
        f"duplicates: {duplicate}",
        f"review: {review_total}",
        f"Сумма ready: {money(total_ready)} UAH",
        f"Предупреждений о возможном совпадении с Sheet1: {overlap_warnings}",
    ])

    if result is None:
        lines.extend([
            "",
            "DRY RUN ONLY. База не изменялась.",
            "Для записи добавьте --apply.",
        ])
    else:
        lines.extend([
            "",
            "ПРИМЕНЕНИЕ:",
            f"Центральная касса C создана: {result['cashbox_created']}",
            f"Payments добавлено: {result['payments_inserted']}",
            f"Кассовых операций добавлено: {result['operations_inserted']}",
            f"Дубликатов source_ref: {result['duplicates']}",
            f"Остаток C: {money(result['balance_C'])} UAH",
        ])

    report_file.parent.mkdir(parents=True, exist_ok=True)
    report_file.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Импорт фактически полученных оплат Лист1 Охорона.xlsx "
            "в отдельную Центральную кассу C."
        )
    )
    parser.add_argument("--source-file", type=Path, default=DEFAULT_SOURCE_FILE)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    source_file = args.source_file.expanduser().resolve()
    if not source_file.exists():
        raise SystemExit(f"Не найден файл: {source_file}")

    conn = sqlite3.connect(get_db_file())
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    print("=" * 78)
    print("ОХОРОНА.XLSX / ЛИСТ1 → ЦЕНТРАЛЬНАЯ КАССА C")
    print("=" * 78)
    print("DB:", get_db_file())
    print("MODE:", "TEST/WORK" if USE_TEST_DB else "PROD")
    print("Apply:", args.apply)
    print("Источник:", source_file)
    print()
    print("Лист1 считается автономным источником поступлений.")
    print("Операции Sheet1 не перемещаются и не используются как дедупликация.")
    print("Смешанные Day/Night строки выводятся в ручную проверку и не импортируются.")
    print()

    rows = read_xlsx_rows(source_file, SHEET_NAME)
    plans, reviews = build_plans(cur, rows)

    report_dir = paths.OSBB_EXPORTS_DIR / "cashier"
    suffix = "apply" if args.apply else "dry_run"
    report_file = report_dir / f"ohorona_list1_central_{suffix}_{now_file()}.txt"

    if not args.apply:
        # Проверяем, что создание C и статей возможно, но ничего не сохраняем.
        ensure_central_cashbox(cur)
        for plan in plans:
            if plan["status"] == "ready":
                ensure_monthly_item(cur, plan["period_code"], plan["base_service_code"])
        conn.rollback()
        make_report(report_file, source_file, plans, reviews, None)
        conn.close()

        ready = sum(1 for p in plans if p["status"] == "ready")
        amount = sum(p["amount"] for p in plans if p["status"] == "ready")
        review_total = len(reviews) + sum(
            1 for p in plans if p["status"] == "review"
        )
        print("Готово к импорту:", ready, "оплат на", money(amount), "UAH")
        print("Ручная проверка :", review_total)
        print("Отчёт:", report_file)
        print()
        print("DRY RUN ONLY. База не изменялась.")
        print("Для записи добавьте --apply.")
        return

    result = apply_plans(conn, plans, reviews)
    conn.commit()
    make_report(report_file, source_file, plans, reviews, result)
    conn.close()

    print("Центральная касса C создана:", result["cashbox_created"])
    print("Payments добавлено:", result["payments_inserted"])
    print("Кассовых операций добавлено:", result["operations_inserted"])
    print("Остаток C:", money(result["balance_C"]), "UAH")
    print("Ручная проверка:", result["review"])
    print("Отчёт:", report_file)


if __name__ == "__main__":
    main()
