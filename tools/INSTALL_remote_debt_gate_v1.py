#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
INSTALL_remote_debt_gate_v1.py

Назначение:
  Добавляет защиту от заказа/выдачи пульта при задолженности.

Что меняет при --apply:
  1) Bots/handlers/client_portal.py
     - добавляет read-only debt gate на основе уже существующей _billing_data();
     - блокирует INSERT INTO remote_requests при долге за PARKING/BARRIER;
     - пытается обернуть вызов _create_remote_request в try/except,
       чтобы жилец получил понятное сообщение, а не traceback.

  2) Bots/handlers/guard_workspace.py
     - добавляет read-only debt gate по apartment_number;
     - скрывает кнопку "✅ Пульт выдан", если есть долг;
     - повторно блокирует физическую выдачу в _save_remote_issued перед записью.

Безопасность:
  - БД не меняет.
  - До --apply работает как dry-run.
  - Перед изменением исходников делает timestamped backup.
  - После патча проверяет синтаксис compile().
  - Если ожидаемые функции не найдены — останавливается.

Запуск PowerShell из G:\Programming\Py:
  python .\OSBB\tools\INSTALL_remote_debt_gate_v1.py
  python .\OSBB\tools\INSTALL_remote_debt_gate_v1.py --apply
"""

from __future__ import annotations

import argparse
import ast
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import warnings


MARK = "OSBB_REMOTE_DEBT_GATE_V1"


CLIENT_HELPER = """
# OSBB_REMOTE_DEBT_GATE_V1_CLIENT_HELPERS
def _remote_gate_service_is_blocking(service_code: object) -> bool:
    service = text(service_code).upper()
    if not service:
        return False
    return (
        service.startswith("PARKING")
        or service.startswith("BARRIER")
        or "PARK" in service
        or "ШЛАГ" in service
        or "SHLAG" in service
    )


def _remote_gate_block_message(apartment_number: object, amount: float, reason: str = "") -> str:
    apt = text(apartment_number) or "-"
    if reason:
        return (
            f"⚠️ За квартирой {apt} невозможно автоматически проверить задолженность.\\n\\n"
            "Заказ нового пульта через бот временно недоступен.\\n"
            "Пожалуйста, обратитесь к оператору ОСББ для сверки."
        )
    return (
        f"⚠️ За квартирой {apt} числится задолженность за парковку / доступ к шлагбауму: "
        f"{amount:.2f} грн.\\n\\n"
        "Заказ нового пульта через бот временно недоступен.\\n"
        "Пожалуйста, погасите задолженность у кассира/охраны или обратитесь к оператору ОСББ для сверки."
    )


def _remote_debt_gate(unit: dict) -> dict:
    \"\"\"
    Read-only gate for resident remote requests.

    Uses _billing_data(), so it follows the current charges/payment_allocations
    compatibility logic and does not create any DB rows.
    \"\"\"
    billing = _billing_data(unit)
    apt = text((unit or {}).get("apartment_number"))

    if billing.get("error"):
        return {
            "allowed": False,
            "outstanding_total": 0.0,
            "message": _remote_gate_block_message(apt, 0.0, str(billing.get("error"))),
        }

    total = 0.0
    rows = []
    for item in billing.get("charges") or []:
        service = item.get("service_code")
        if not _remote_gate_service_is_blocking(service):
            continue
        rest = float(item.get("outstanding_amount") or 0)
        if rest > 0.01:
            total += rest
            rows.append(item)

    if total > 0.01:
        return {
            "allowed": False,
            "outstanding_total": round(total, 2),
            "rows": rows,
            "message": _remote_gate_block_message(apt, total),
        }

    return {
        "allowed": True,
        "outstanding_total": 0.0,
        "rows": [],
        "message": "",
    }

"""


CLIENT_CREATE_GATE = """    # OSBB_REMOTE_DEBT_GATE_V1_CREATE_CHECK
    gate = _remote_debt_gate(unit)
    if not gate.get("allowed"):
        raise ValueError(gate.get("message") or "Заказ пульта временно недоступен из-за задолженности.")

"""


GUARD_HELPER = """
# OSBB_REMOTE_DEBT_GATE_V1_GUARD_HELPERS
def _remote_gate_allocation_amount_column(columns) -> str | None:
    if "amount" in columns:
        return "amount"
    if "allocated_amount" in columns:
        return "allocated_amount"
    return None


def _remote_gate_service_is_blocking(service_code: object) -> bool:
    service = text(service_code).upper()
    if not service:
        return False
    return (
        service.startswith("PARKING")
        or service.startswith("BARRIER")
        or "PARK" in service
        or "ШЛАГ" in service
        or "SHLAG" in service
    )


def _remote_gate_block_message(apartment_number: object, amount: float, reason: str = "") -> str:
    apt = text(apartment_number) or "-"
    if reason:
        return (
            f"⚠️ По кв.{apt} невозможно автоматически проверить задолженность. "
            "Выдача пульта через пост O временно недоступна. "
            "Направьте жильца к оператору ОСББ для сверки."
        )
    return (
        f"⚠️ По кв.{apt} есть задолженность за парковку / доступ к шлагбауму: "
        f"{amount:.2f} грн. "
        "Пульт нельзя выдавать до оплаты или сверки с оператором."
    )


def _remote_gate_debt_for_apartment(cur, apartment_number: object) -> dict:
    \"\"\"
    Read-only debt gate for physical remote issue.

    Checks charges minus payment_allocations for PARKING/BARRIER services.
    Does not write anything to DB.
    \"\"\"
    apt = text(apartment_number)
    if not apt:
        return {
            "allowed": False,
            "outstanding_total": 0.0,
            "message": _remote_gate_block_message("-", 0.0, "missing apartment_number"),
        }

    if not table_exists(cur, "charges"):
        return {
            "allowed": False,
            "outstanding_total": 0.0,
            "message": _remote_gate_block_message(apt, 0.0, "charges table missing"),
        }

    charge_cols = table_columns(cur, "charges")
    if "apartment_number" not in charge_cols or "amount" not in charge_cols:
        return {
            "allowed": False,
            "outstanding_total": 0.0,
            "message": _remote_gate_block_message(apt, 0.0, "charge link columns missing"),
        }

    service_expr = (
        "COALESCE(c.base_service_code, c.service_code, '')"
        if "base_service_code" in charge_cols and "service_code" in charge_cols
        else ("COALESCE(c.service_code, '')" if "service_code" in charge_cols else "''")
    )
    period_expr = "COALESCE(c.period_code, '')" if "period_code" in charge_cols else "''"
    status_filter = (
        "AND COALESCE(c.charge_status, '') <> 'cancelled'"
        if "charge_status" in charge_cols
        else (
            "AND COALESCE(c.status, '') <> 'cancelled'"
            if "status" in charge_cols else ""
        )
    )

    allocation_join = ""
    allocation_select = "0 AS allocated_amount"
    if table_exists(cur, "payment_allocations"):
        alloc_cols = table_columns(cur, "payment_allocations")
        amount_col = _remote_gate_allocation_amount_column(alloc_cols)
        if amount_col and "charge_id" in alloc_cols:
            allocation_join = (
                f'LEFT JOIN payment_allocations pa ON pa.charge_id = c.id'
            )
            allocation_select = (
                f'COALESCE(SUM(pa."{amount_col}"), 0) AS allocated_amount'
            )

    cur.execute(f\"\"\"
        SELECT
            c.id AS charge_id,
            {service_expr} AS service_code,
            {period_expr} AS period_code,
            c.amount AS amount,
            {allocation_select}
        FROM charges c
        {allocation_join}
        WHERE c.apartment_number = ?
        {status_filter}
        GROUP BY c.id
        ORDER BY {period_expr}, c.id
    \"\"\", (apt,))

    total = 0.0
    rows = []
    for row in cur.fetchall():
        item = dict(row)
        if not _remote_gate_service_is_blocking(item.get("service_code")):
            continue
        amount = float(item.get("amount") or 0)
        allocated = float(item.get("allocated_amount") or 0)
        rest = max(0.0, amount - allocated)
        if rest > 0.01:
            total += rest
            item["outstanding_amount"] = rest
            rows.append(item)

    if total > 0.01:
        return {
            "allowed": False,
            "outstanding_total": round(total, 2),
            "rows": rows,
            "message": _remote_gate_block_message(apt, total),
        }

    return {
        "allowed": True,
        "outstanding_total": 0.0,
        "rows": [],
        "message": "",
    }

"""


GUARD_SHOW_CARD = """async def _show_remote_issue_card(update: Update, state: dict, request_id: int) -> None:
    row = _remote_request(request_id)
    if not row or row.get("status") not in {"NEW", "IN_REVIEW"}:
        await update.message.reply_text("Заявка не найдена или уже закрыта.")
        return

    conn = get_conn()
    try:
        gate = _remote_gate_debt_for_apartment(conn.cursor(), row.get("apartment_number"))
    finally:
        conn.close()

    state["mode"] = "remote_issue_card"
    state["remote_request_id"] = int(request_id)

    lines = [
        f"🔑 Заявка #{row['id']}",
        "",
        f"Квартира: {row.get('apartment_number') or '-'}",
        f"Вид: {row.get('request_kind') or '-'}",
        f"Количество: {row.get('quantity') or 1}",
        f"Комментарий жителя: {row.get('resident_comment') or '—'}",
        f"Статус: {row.get('status')}",
        "",
    ]

    if not gate.get("allowed"):
        lines.extend([
            gate.get("message") or "⚠️ Выдача пульта временно недоступна.",
            "",
            "Действие доступно только после оплаты или ручной сверки оператором.",
        ])
        buttons = [["⬅️ К заявкам", HOME]]
    else:
        lines.append("Подтверждайте только после фактической выдачи пульта.")
        buttons = [
            ["✅ Пульт выдан"],
            ["⬅️ К заявкам", HOME],
        ]

    await update.message.reply_text(
        "\\n".join(lines),
        reply_markup=kb(buttons),
    )
"""


GUARD_SAVE_ISSUED = """async def _save_remote_issued(update: Update, state: dict, user_id: int, note: str) -> None:
    if not (
        _is_allowed(
            user_id, "remote_requests", "ISSUE",
            scope_type="POST", scope_value=POST_CODE,
        )
        and _is_allowed(
            user_id, "remote_handover_events", "CREATE",
            scope_type="POST", scope_value=POST_CODE,
        )
    ):
        await _denied(update)
        return

    request_id = int(state["remote_request_id"])
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM remote_requests WHERE id = ?",
            (request_id,),
        )
        row = cur.fetchone()
        if not row:
            raise ValueError("Заявка не найдена.")
        request = dict(row)
        if request.get("status") not in {"NEW", "IN_REVIEW"}:
            raise ValueError("Заявка уже обработана другим сотрудником.")

        gate = _remote_gate_debt_for_apartment(cur, request.get("apartment_number"))
        if not gate.get("allowed"):
            raise ValueError(gate.get("message") or "Выдача пульта временно недоступна из-за задолженности.")

        event_id = _insert_remote_event(
            conn,
            event_kind="ISSUED_FROM_POST",
            operator_id=user_id,
            remote_request=request,
            quantity=int(request.get("quantity") or 1),
            note=note,
        )

        rcols = table_columns(cur, "remote_requests")
        updates = {
            "status": "ISSUED",
            "operator_id": str(user_id),
            "operator_note": note or None,
            "updated_at": now_db(),
            "issued_at": now_db(),
            "closed_at": now_db(),
        }
        actual = {key: value for key, value in updates.items() if key in rcols}
        assignments = ", ".join(f"{key} = ?" for key in actual)
        cur.execute(
            f"UPDATE remote_requests SET {assignments} WHERE id = ?",
            tuple(actual.values()) + (request_id,),
        )

        write_business_access_audit(
            conn,
            actor_user_id=user_id,
            action_type="guard_o_remote_issued",
            resource="remote_requests",
            action="ISSUE",
            scope_type="POST",
            scope_value=POST_CODE,
            target_table="remote_requests",
            target_id=request_id,
            details=(
                f"Пульт выдан с поста O. Event={event_id}; "
                f"кв.{request.get('apartment_number')}; количество={request.get('quantity') or 1}."
            ),
        )
        conn.commit()
    except Exception as exc:
        conn.rollback()
        await update.message.reply_text(f"Не удалось подтвердить выдачу: {exc}")
        return
    finally:
        conn.close()

    await update.message.reply_text(
        f"✅ Пульт выдан.\\nЗаявка #{request_id} закрыта как ISSUED.\\n"
        f"Событие выдачи: #{event_id}."
    )
    await _show_remote_issue_list(update, state, user_id)
"""


@dataclass
class Change:
    file: Path
    description: str
    changed: bool


def project_root() -> Path:
    here = Path(__file__).resolve()
    if here.parent.name.lower() == "tools":
        return here.parent.parent
    cwd = Path.cwd().resolve()
    if (cwd / "OSBB").exists():
        return cwd / "OSBB"
    return cwd


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def parse_tree(source: str, filename: str) -> ast.AST:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SyntaxWarning)
        return ast.parse(source, filename=filename)


def function_range(source: str, func_name: str, filename: str) -> tuple[int, int, ast.AST]:
    tree = parse_tree(source, filename)
    matches = [
        node for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == func_name
    ]
    if not matches:
        raise RuntimeError(f"Function not found: {func_name}")
    node = matches[0]
    return int(node.lineno), int(getattr(node, "end_lineno", node.lineno)), node


def insert_after_function(source: str, func_name: str, block: str, marker: str, filename: str) -> tuple[str, bool]:
    if marker in source:
        return source, False
    start, end, _node = function_range(source, func_name, filename)
    lines = source.splitlines(keepends=True)
    lines.insert(end, block if block.endswith("\n") else block + "\n")
    return "".join(lines), True


def insert_before_function(source: str, func_name: str, block: str, marker: str, filename: str) -> tuple[str, bool]:
    if marker in source:
        return source, False
    start, _end, _node = function_range(source, func_name, filename)
    lines = source.splitlines(keepends=True)
    lines.insert(start - 1, block if block.endswith("\n") else block + "\n")
    return "".join(lines), True


def replace_function(source: str, func_name: str, new_func: str, filename: str) -> tuple[str, bool]:
    start, end, _node = function_range(source, func_name, filename)
    lines = source.splitlines(keepends=True)
    old = "".join(lines[start - 1:end])
    if old.strip() == new_func.strip():
        return source, False
    replacement = new_func if new_func.endswith("\n") else new_func + "\n"
    lines[start - 1:end] = [replacement]
    return "".join(lines), True


def insert_client_create_gate(source: str, filename: str) -> tuple[str, bool]:
    if "OSBB_REMOTE_DEBT_GATE_V1_CREATE_CHECK" in source:
        return source, False
    start, end, _node = function_range(source, "_create_remote_request", filename)
    lines = source.splitlines(keepends=True)
    for idx in range(start - 1, end):
        if lines[idx].strip() == "conn = get_conn()":
            lines.insert(idx, CLIENT_CREATE_GATE)
            return "".join(lines), True
    raise RuntimeError("Could not find 'conn = get_conn()' inside _create_remote_request")


def call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Call):
        f = node.func
        if isinstance(f, ast.Name):
            return f.id
        if isinstance(f, ast.Attribute):
            return f.attr
    return ""


def find_parent_async_function(tree: ast.AST, line: int) -> ast.AsyncFunctionDef | None:
    candidates = []
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef):
            start = int(node.lineno)
            end = int(getattr(node, "end_lineno", start))
            if start <= line <= end:
                candidates.append(node)
    if not candidates:
        return None
    candidates.sort(key=lambda n: int(getattr(n, "end_lineno", n.lineno)) - int(n.lineno))
    return candidates[0]


def wrap_create_remote_request_calls(source: str, filename: str) -> tuple[str, int]:
    if "OSBB_REMOTE_DEBT_GATE_V1_CALL_WRAP" in source:
        return source, 0

    tree = parse_tree(source, filename)
    lines = source.splitlines(keepends=True)
    targets: list[tuple[int, int]] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and call_name(node.value) == "_create_remote_request":
            parent = find_parent_async_function(tree, int(node.lineno))
            if parent is None:
                continue
            parent_text = "".join(lines[int(parent.lineno)-1:int(getattr(parent, "end_lineno", parent.lineno))])
            if not all(name in parent_text for name in ("update", "lang", "state")):
                continue
            targets.append((int(node.lineno), int(getattr(node, "end_lineno", node.lineno))))

    if not targets:
        return source, 0

    for start, end in sorted(targets, reverse=True):
        block_lines = lines[start - 1:end]
        first = block_lines[0]
        indent = first[:len(first) - len(first.lstrip(" "))]
        stripped = []
        for line in block_lines:
            if line.startswith(indent):
                stripped.append(line[len(indent):])
            else:
                stripped.append(line)

        wrapped = []
        wrapped.append(f"{indent}# OSBB_REMOTE_DEBT_GATE_V1_CALL_WRAP\n")
        wrapped.append(f"{indent}try:\n")
        for line in stripped:
            wrapped.append(f"{indent}    {line}")
        wrapped.append(f"{indent}except ValueError as exc:\n")
        wrapped.append(f'{indent}    await update.message.reply_text(f"⚠️ {{exc}}", reply_markup=kb(remotes_menu_keyboard(lang)))\n')
        wrapped.append(f'{indent}    state["mode"] = "client_remotes"\n')
        wrapped.append(f"{indent}    return True\n")
        lines[start - 1:end] = wrapped

    return "".join(lines), len(targets)


def patch_client(path: Path) -> tuple[str, list[Change], list[str]]:
    source = read(path)
    changes: list[Change] = []
    warnings_out: list[str] = []

    new, changed = insert_after_function(
        source,
        "_billing_data",
        CLIENT_HELPER,
        "OSBB_REMOTE_DEBT_GATE_V1_CLIENT_HELPERS",
        str(path),
    )
    changes.append(Change(path, "client helper _remote_debt_gate", changed))
    source = new

    new, changed = insert_client_create_gate(source, str(path))
    changes.append(Change(path, "gate before INSERT INTO remote_requests", changed))
    source = new

    new, wrapped = wrap_create_remote_request_calls(source, str(path))
    changes.append(Change(path, f"wrap _create_remote_request call sites ({wrapped})", wrapped > 0))
    if wrapped == 0:
        warnings_out.append("No _create_remote_request call site was wrapped. If the caller does not catch ValueError, user may see no friendly message.")
    source = new

    return source, changes, warnings_out


def patch_guard(path: Path) -> tuple[str, list[Change], list[str]]:
    source = read(path)
    changes: list[Change] = []
    warnings_out: list[str] = []

    new, changed = insert_before_function(
        source,
        "_remote_rows_for_issue",
        GUARD_HELPER,
        "OSBB_REMOTE_DEBT_GATE_V1_GUARD_HELPERS",
        str(path),
    )
    changes.append(Change(path, "guard helper _remote_gate_debt_for_apartment", changed))
    source = new

    new, changed = replace_function(source, "_show_remote_issue_card", GUARD_SHOW_CARD, str(path))
    changes.append(Change(path, "replace _show_remote_issue_card with debt-aware card", changed))
    source = new

    new, changed = replace_function(source, "_save_remote_issued", GUARD_SAVE_ISSUED, str(path))
    changes.append(Change(path, "replace _save_remote_issued with final debt check", changed))
    source = new

    return source, changes, warnings_out


def syntax_check(path: Path, source: str) -> None:
    compile(source, str(path), "exec")


def backup_file(path: Path, backup_root: Path, project_root: Path) -> Path:
    rel_path = path.relative_to(project_root)
    dst = backup_root / rel_path
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, dst)
    return dst


def main() -> int:
    ap = argparse.ArgumentParser(description="Install remote/pult debt gate v1.")
    ap.add_argument("--apply", action="store_true", help="Actually replace source files after backup.")
    ap.add_argument("--include-safe-linking", action="store_true", help="Also patch client_portal_safe_linking.py if present.")
    args = ap.parse_args()

    root = project_root()
    client = root / "Bots" / "handlers" / "client_portal.py"
    guard = root / "Bots" / "handlers" / "guard_workspace.py"

    targets: list[tuple[Path, str]] = [
        (client, "client"),
        (guard, "guard"),
    ]
    if args.include_safe_linking:
        safe = root / "Bots" / "handlers" / "client_portal_safe_linking.py"
        if safe.exists():
            targets.insert(1, (safe, "client"))

    print("OSBB remote/pult debt gate installer v1")
    print("Mode:", "APPLY" if args.apply else "DRY RUN / CHECK ONLY")
    print("Root:", root)
    print("")

    all_changes: list[Change] = []
    all_warnings: list[str] = []
    patched_sources: dict[Path, str] = {}

    for path, kind in targets:
        if not path.exists():
            raise SystemExit(f"Missing target: {path}")

        print("Checking:", path)
        if kind == "client":
            patched, changes, warns = patch_client(path)
        elif kind == "guard":
            patched, changes, warns = patch_guard(path)
        else:
            raise RuntimeError(kind)

        syntax_check(path, patched)
        patched_sources[path] = patched
        all_changes.extend(changes)
        all_warnings.extend([f"{path.name}: {w}" for w in warns])

    print("")
    print("Planned changes:")
    any_changed = False
    for ch in all_changes:
        status = "CHANGE" if ch.changed else "already installed / no change"
        print(f" - {ch.file.relative_to(root)}: {status}: {ch.description}")
        any_changed = any_changed or ch.changed

    if all_warnings:
        print("")
        print("Warnings:")
        for w in all_warnings:
            print(" -", w)

    if not any_changed:
        print("")
        print("Result: already installed. No source changes needed.")
        return 0

    if not args.apply:
        print("")
        print("DRY RUN COMPLETED. Re-run with --apply to install.")
        return 0

    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    backup_root = root / "Data" / "backups" / "source_patches" / f"remote_debt_gate_v1_{stamp}"
    backup_root.mkdir(parents=True, exist_ok=True)

    print("")
    print("Backups:")
    for path in patched_sources:
        dst = backup_file(path, backup_root, root)
        print(" -", dst)

    for path, patched in patched_sources.items():
        write(path, patched)

    print("")
    print("Installed.")
    print("Backup root:", backup_root)
    print("Next: restart the bot and test pult request/issue on a debtor apartment.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
