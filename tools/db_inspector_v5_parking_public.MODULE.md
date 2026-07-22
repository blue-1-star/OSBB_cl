# db_inspector.py

## Parking payment report v5

Public sheets now show compact columns only:

- apartment
- plate
- amount
- inferred_parking_time

Internal sheets keep diagnostic columns for operator/admin review.

`inferred_parking_time` means the parking mode inferred from payment text or tariff amount:

- night
- day
- undefined

Command:

```powershell
python .\OSBB\tools\db_inspector.py parking-payments-report --include-all
```

If known tariffs are available:

```powershell
python .\OSBB\tools\db_inspector.py parking-payments-report --night-amount 400 --day-amount 200 --include-all
```

The command reports which vehicle table was used for plate/parking_time lookup.

If all `missing_parking_time` values look wrong, inspect vehicle tables:

```powershell
python .\OSBB\tools\db_inspector.py vehicles
python .\OSBB\tools\db_inspector.py search parking
```
