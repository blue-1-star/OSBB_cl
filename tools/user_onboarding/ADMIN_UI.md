# User Onboarding Admin UI v0.1

## Purpose

Adds the admin side of resident confirmation.

Menu target:

```text
⚙️ Настройки
  👥 Пользователи и роли
    🆕 Новые жители
```

## Actions

For each pending `resident_verification_requests` record:

- `✔ Подтвердить`
  - creates `resident_access_accounts` with role `resident`
  - marks request as `APPROVED`

- `✏ Требует уточнения`
  - creates `operator_task_queue` task `VERIFY_RESIDENT`
  - marks request as `NEEDS_CLARIFICATION`

- `❌ Отклонить`
  - marks request as `REJECTED`

## Files

- `admin_ui.py`
- `patch_parking_bot_admin_ui.py`

## Commands

Dry run:

```powershell
python .\OSBB\tools\user_onboarding\patch_parking_bot_admin_ui.py
```

Apply:

```powershell
python .\OSBB\tools\user_onboarding\patch_parking_bot_admin_ui.py --apply
```

The patcher creates a backup before modifying `Bots\parking_bot.py`.

## Note

This is stage 2 of onboarding.

Stage 3 will add resident-side invitation acceptance.
