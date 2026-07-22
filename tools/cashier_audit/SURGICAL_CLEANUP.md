# Cashier Surgical Cleanup v0.2

This version expands cleanup planning beyond receipts/payments:

- cashier receipts
- payments
- payment allocations
- payment notices
- resident payment notices
- service orders
- service order charge links
- charges
- phone access subscription charges
- remote/phone request-like tables if linked by ids
- audit/log/journal/history tables by discovery

Default is dry-run.

Examples:

```powershell
python .\OSBB\tools\cashier_audit\cashier_surgical_cleanup.py --receipt-ids 10,11
python .\OSBB\tools\cashier_audit\cashier_surgical_cleanup.py --source-like TEST_CASHIER
python .\OSBB\tools\cashier_audit\cashier_surgical_cleanup.py --operator-id 123456789 --date-from 2026-07-09
```

Review report in:

```text
OSBB\Data\reports\cashier_cleanup\
```

Then apply only if the plan is correct:

```powershell
python .\OSBB\tools\cashier_audit\cashier_surgical_cleanup.py --receipt-ids 10,11 --apply
```
