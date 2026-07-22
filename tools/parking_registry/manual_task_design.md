# Manual Operator Task Button

Admins need a high-priority manual task button from vehicle/apartment cards.

Button:

```text
📝 Поставить задачу оператору
```

Manual tasks must be shown before automatic audit tasks.

Suggested fields:

- priority: MANUAL_HIGH
- task_type: MANUAL_VERIFY
- apartment
- plate
- vehicle_id
- created_by_admin_telegram_id
- comment
- status: PENDING
- origin: MANUAL_ADMIN
