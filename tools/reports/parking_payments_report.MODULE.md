# Parking Payments Report

## Purpose

`parking_payments_report.py` creates an Excel report for parking-payment verification.

This report is intended for guards, operators and administrators.

It is not a Telegram interface.

It is the first operational reporting module of OSBB and may later become part of the web/admin dashboard.

## Typical command

From the usual developer working directory:

```powershell
python .\OSBB\tools\reports\parking_payments_report.py --include-all
```

With known tariff amounts:

```powershell
python .\OSBB\tools\reports\parking_payments_report.py --include-all --night-amount 400 --day-amount 200
```

## Output

The report is saved to:

```text
OSBB\Recovered\PARKING_PAYMENTS_REPORT_<timestamp>.xlsx
```

## Public sheets

Public sheets are intentionally compact:

- apartment
- plate
- amount
- inferred_parking_time

These sheets are suitable for quick viewing and sharing.

## Internal sheets

Internal sheets contain diagnostic fields:

- payment id
- payment date
- status
- source_ref
- inference reason
- existing parking_time
- missing parking_time
- conflict
- comment

Internal sheets are for administrator/operator review.

## inferred_parking_time

`inferred_parking_time` is not a database field.

It is a calculated category inferred from:

- payment text markers;
- tariff amount, if `--night-amount` / `--day-amount` are provided.

Possible values:

- night
- day
- undefined

## Safety

The report is read-only.

It does not update `parking_time`.

Any update based on payments must go through a separate reconciliation workflow.
