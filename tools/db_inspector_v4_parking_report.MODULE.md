# db_inspector.py

## Purpose

`db_inspector.py` is the OSBB SQLite diagnostic tool.

It is not a Telegram bot interface. It is a developer/operator command-line tool and a future foundation for a web/admin dashboard.

## Typical developer working directory

```text
G:\Programming\Py
```

## Default database

```text
OSBB\Data\db\osbb_test.db
```

## Commands

```powershell
python .\OSBB\tools\db_inspector.py summary
python .\OSBB\tools\db_inspector.py cashier
python .\OSBB\tools\db_inspector.py payments --limit 3
python .\OSBB\tools\db_inspector.py parking-payments-report
python .\OSBB\tools\db_inspector.py parking-payments-report --include-all
python .\OSBB\tools\db_inspector.py parking-payments-report --night-amount 400 --day-amount 200
```

## Parking payments report

The `parking-payments-report` command creates an Excel workbook in `OSBB\Recovered`.

Sheets:

- Summary
- Night
- Day
- Undefined
- Review_Needed

Purpose:

- find paid parking cases;
- split night/day/undefined payments;
- find payments that can suggest missing `parking_time`;
- prepare a report for guards/operators/admins.

The command is read-only. It does not update the database.

Use `--include-all` when parking payments are not clearly marked by text tokens.

Use `--night-amount` and `--day-amount` when tariffs are known by amount.

## Cashier output policy

`cashier` prints a short diagnostic summary by default.

Detailed payment cards:

```powershell
python .\OSBB\tools\db_inspector.py payments --limit 3
```

Raw dictionaries:

```powershell
python .\OSBB\tools\db_inspector.py cashier --raw --limit 1
```
