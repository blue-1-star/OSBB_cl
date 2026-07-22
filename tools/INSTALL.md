# OSBB Access Roles v0.1

## Add

Copy:

```text
access_control
```

to:

```text
OSBB\tools\access_control
```

## Replace

Replace only:

```text
OSBB\tools\user_onboarding\admin_ui.py
```

with:

```text
user_onboarding\admin_ui.py
```

Do not delete `create_onboarding_schema.py`.

## Apply DB schema

```powershell
python .\OSBB\tools\access_control\create_permissions_schema.py
python .\OSBB\tools\access_control\create_permissions_schema.py --apply
```

## Check

```powershell
python -m py_compile .\OSBB\tools\access_control\create_permissions_schema.py
python -m py_compile .\OSBB\tools\user_onboarding\admin_ui.py
python -m py_compile .\OSBB\Bots\parking_bot.py
```
