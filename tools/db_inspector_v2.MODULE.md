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
python .\OSBB\tools\db_inspector.py tables
python .\OSBB\tools\db_inspector.py schema payments
python .\OSBB\tools\db_inspector.py admins
python .\OSBB\tools\db_inspector.py roles
python .\OSBB\tools\db_inspector.py cashier
python .\OSBB\tools\db_inspector.py cashier --raw
python .\OSBB\tools\db_inspector.py cashier --telegram
python .\OSBB\tools\db_inspector.py payments --limit 20
python .\OSBB\tools\db_inspector.py service-orders
python .\OSBB\tools\db_inspector.py remotes
python .\OSBB\tools\db_inspector.py vehicles
python .\OSBB\tools\db_inspector.py search cash
```

## Payment formatter

By default, cashier/payment output is human-readable.

Use `--raw` only for emergency debugging.

Use `--telegram` to preview compact cards that may later be reused in Telegram UI.
