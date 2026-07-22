# ACCESS_CONTROL_V2_INSTALL

## Install type

Full Access Control v2 bridge for the existing OSBB RBAC/ACL schema.

## Add / replace folder

Copy folder:

```text
access_control
```

to:

```text
OSBB\tools\access_control
```

This folder intentionally replaces old Access Control helper scripts.

## Replace file

Replace only:

```text
OSBB\tools\user_onboarding\admin_ui.py
```

with:

```text
user_onboarding\admin_ui.py
```

Do not delete:

```text
OSBB\tools\user_onboarding\create_onboarding_schema.py
```

## Seed reference

Dry run:

```powershell
python .\OSBB\tools\access_control\seed_access_permissions_reference.py
```

Apply:

```powershell
python .\OSBB\tools\access_control\seed_access_permissions_reference.py --apply
```

## Compile check

```powershell
python -m py_compile .\OSBB\tools\access_control\access_control.py
python -m py_compile .\OSBB\tools\access_control\seed_access_permissions_reference.py
python -m py_compile .\OSBB\tools\access_control\inspect_access_summary.py
python -m py_compile .\OSBB\tools\user_onboarding\admin_ui.py
python -m py_compile .\OSBB\Bots\parking_bot.py
```

## Runtime check

Restart bot and open:

```text
⚙️ Настройки
👥 Пользователи и роли
🔑 Права доступа
📖 Справочник возможностей
```

## Important

Do not run old schema rebuild commands.

The old `create_permissions_schema.py` is now a safe STOP script.
