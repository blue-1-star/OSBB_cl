# Parking Registry Tools

## Purpose

This subsystem is the operational face of OSBB parking data.

It is not a Telegram handler. It is the foundation for future web/admin dashboards and guard-facing reports.

## Main principle

`vehicles` is the main source of truth for the parking registry.

Payments are evidence. Payments confirm or challenge the registry, but should not silently overwrite it.

## Tariff period

For historical parking payments, `payment_date` is not the main business date.

The main business date is the tariff period.

Current historical title period:

```text
05-06.2026
```

Later this period should be split into atomic periods:

```text
2026-05
2026-06
```

## Commands

```powershell
python .\OSBB\tools\parking_registry\registry_report.py --include-all --title-period 05-06.2026
python .\OSBB\tools\parking_registry\registry_report.py --include-all --title-period 05-06.2026 --night-amount 400 --day-amount 200
python .\OSBB\tools\parking_registry\registry_audit.py
```

## Outputs

```text
OSBB\Recovered\PARKING_REGISTRY_REPORT_<timestamp>.xlsx
OSBB\Recovered\PARKING_REGISTRY_AUDIT_<timestamp>.xlsx
```

## Output philosophy

Public sheets should be short and readable.

Internal sheets may contain diagnostic details.

The system must show errors instead of hiding them.


## v2 output cleanup

Registry report readability changes:

- rows are sorted by apartment as numbers: 1, 2, 10, 11, ...
- missing payment status is shortened to `NO`
- long title period values are compacted:
  - `2026-05_2026-06` -> `05-06_26`
  - `05-06.2026` -> `05-06_26`
