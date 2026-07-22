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
python .\OSBB\tools\db_inspector.py cashier --raw --limit 1
python .\OSBB\tools\db_inspector.py payments --limit 3
python .\OSBB\tools\db_inspector.py payments --telegram --limit 5
python .\OSBB\tools\db_inspector.py service-orders
python .\OSBB\tools\db_inspector.py remotes
python .\OSBB\tools\db_inspector.py vehicles
python .\OSBB\tools\db_inspector.py search cash
```

## Cashier output policy

`cashier` prints a short diagnostic summary by default.

Detailed payment cards are available through:

```powershell
python .\OSBB\tools\db_inspector.py payments --limit 3
```

Raw dictionaries are available only by explicit request:

```powershell
python .\OSBB\tools\db_inspector.py cashier --raw --limit 1
```

Telegram-style compact cards can be previewed with:

```powershell
python .\OSBB\tools\db_inspector.py payments --telegram --limit 5
```

## Design note

This tool belongs to the project maintenance layer.

It should grow into a reusable inspection API before any web dashboard is built.
