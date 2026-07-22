# User Onboarding v0.1

A Telegram user is not automatically a resident.

Lifecycle:

```text
UNKNOWN -> INVITED -> REGISTERED -> PENDING_ADMIN_CONFIRMATION -> ACTIVE resident
```

## Tables

- `resident_invitations`
- `resident_verification_requests`
- `resident_access_accounts`
- `operator_task_queue`

`operator_task_queue` is shared: it can later receive manual admin tasks and parking verification tasks.

## Commands

Dry run:

```powershell
python .\OSBB\tools\user_onboarding\create_onboarding_schema.py
```

Apply:

```powershell
python .\OSBB\tools\user_onboarding\create_onboarding_schema.py --apply
```

Apply mode creates a DB backup first.

## Next

1. Admin queue: pending resident confirmations.
2. Confirm resident button.
3. Correction path: create operator task if resident says data is wrong.
4. Role/access screens under Settings.

---

<!-- Added by OSBB Documentator: 2026-07-06 12:28:36 -->

## Python internal import order

Date: 2026-07-06

For OSBB Python entrypoints, internal project imports must be placed only after
the project root has been added to `sys.path`.

Correct order:

1. Python standard library imports.
2. External library imports.
3. Determine `BOT_DIR`, `OSBB_ROOT`, `PY_ROOT` or equivalent project roots.
4. Insert project roots into `sys.path`.
5. Import internal OSBB modules, for example `config`, `handlers`, `tools`.

Reason: some entrypoints are launched from subdirectories, for example `OSBB\Bots`.
In that case `tools`, `config`, and other OSBB-level packages are not importable
until `OSBB_ROOT` is added to `sys.path`.

Patchers must preserve this rule and must not insert `tools.*` imports at the
top of `parking_bot.py` before the `sys.path` setup block.
