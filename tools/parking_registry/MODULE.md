# Parking Registry Toolkit v0.1

Operational tools for OSBB parking data.

## Commands

```powershell
python .\OSBB\tools\parking_registry\registry_report.py --include-all --title-period 05-06.2026
python .\OSBB\tools\parking_registry\operator_task_candidates.py --xlsx
python .\OSBB\tools\parking_registry\operator_task_candidates.py --probe-tables
python .\OSBB\tools\parking_registry\debtors_report.py
python .\OSBB\tools\parking_registry\debtors_report.py --probe
```

## Manual tasks

See `manual_task_design.md`.

## Debtors report

Minimal public columns:

- кв
- ФИО
- авто
- сумма

Current implementation is best-effort until exact debt/balance source table is confirmed.
