# Cashier v0.4.1 hotfix

Replace the whole folder:

```text
OSBB\tools\cashier_v2_telegram\
```

No changes to `parking_bot.py` are required if v0.4 is already connected.

Compile:

```powershell
python -m py_compile .\OSBB\tools\cashier_v2_telegram\cashier_search.py
python -m py_compile .\OSBB\tools\cashier_v2_telegram\cashier_card.py
python -m py_compile .\OSBB\tools\cashier_v2_telegram\cashier_v2_ui.py
python -m py_compile .\OSBB\Bots\parking_bot.py
```

Expected flow:

```text
Касса
-> enter apartment or plate digits
-> payer card
-> system infers Night/Day, period and tariff
-> accept, or edit only the differing field
```

A zero or unknown amount cannot be saved.
