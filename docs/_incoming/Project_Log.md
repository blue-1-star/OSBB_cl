## 2026-08-04 — Перенос Streamlit-инструментов из OSBB_util в OSBB_cl

### Решение
Streamlit-дерево (`streamlit_apps/`) переносится из `OSBB_util` в
`OSBB_cl/admin_console/` — копированием через `migrate_streamlit_to_osbb_cl.py`
(по аналогии с `migrate_to_osbb_cl.py`), верхняя папка переименована,
внутренняя структура (`pages/`, `utils/`, `home.py`) не менялась.

### Причина
Пока инструмент был read-only (`03_payments_viewer.py`) — раздельное
расположение было оправдано. С появлением редактора, пишущего в
payments/cashbox_operations/cashier_receipts и пересчитывающего
cashboxes.current_balance (`04_cashbox_editor.py`), граница
"проект / утилита" обязана совпасть с границей "чтение / запись" —
иначе бизнес-правила (формула баланса, структура audit_log) дублируются
в двух кодовых базах и расходятся, тем же классом бага, что уже был
пойман в `apply_cleanup_batch()` (см. Surgical_Delete.md, "Пересчёт
кассы" в разделе "Выявленные ограничения").

### Следующий шаг
`04_cashbox_editor.py` переписать так, чтобы формула баланса и запись
в audit_log вызывались напрямую из `handlers/cashier_operator.py` /
`cashier_v2_core.py`, а не дублировались собственной реализацией —
текущая версия страницы это нарушает и подлежит правке сразу после
переноса.