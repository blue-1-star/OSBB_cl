# OSBB — handoff to ChatGPT Business
**Checkpoint date:** 27 June 2026  
**Purpose:** resume the local OSBB project in a new ChatGPT Business workspace without relying on the old personal-chat memory.

## 1. Transfer rule: keep the personal workspace separate
The user deliberately keeps the Personal and Business workspaces separate. Do **not** merge Personal into Business. The Business workspace should start as a new project using these handoff files.

The local code and databases are the operational source of truth; ChatGPT Business receives only this project context, not a database copy.

## 2. Security and upload boundary
Do **not** upload any of the following to ChatGPT Business:
- `G:\Prog_secret\telegram_osbb.py`
- Telegram bot tokens, passwords, API keys, QR codes, cookies, `.env` files
- `osbb.db`, sandbox `.db` files, database backups, raw resident registries, payment exports, or full phone lists
- screenshots containing private resident data unless they are needed for a specific debugging question

It is safe to upload these handoff Markdown/text files because they contain no secret values.

## 3. Local environment
- **Project root:** `G:\Programming\Py\OSBB`
- **Python venv:** `G:\Programming\Py\venv\Scripts\python.exe`
- **Configuration file is one level above the project:** `G:\Programming\Py\config.py`
- **Production DB — never use for sandbox tests:**  
  `G:\Programming\Py\OSBB\Data\db\osbb.db`
- **Live-services sandbox DB — current testing database:**  
  `G:\Programming\Py\OSBB\Data\db\sandbox\osbb_test_live_services_2026-06-26_20-13-26.db`
- **Sandbox backups:**  
  `G:\Programming\Py\OSBB\Data\db\sandbox\backups`
- **Logs:**  
  `G:\Programming\Py\OSBB\Data\db\logs`
- **Normal sandbox bot launcher:**  
  `Start_OSBB_Live_Services_Sandbox_Bot_v1.bat`

## 4. Safety rules that govern all work
1. Sandbox first. Never change `osbb.db` while working on a new flow.
2. Always stop the sandbox bot with `Ctrl+C` before replacing source files or running a migration.
3. Every source installer must create timestamped backups under:
   `G:\Programming\Py\OSBB\Data\backups\source_code\`
4. Every sandbox migration must make a `.db` backup first.
5. Never create a resident-facing action by impersonating or switching into another resident's client account.
6. For another apartment, use a future read-only operator view or an isolated operator TEST session. A legitimate operator audit entry is acceptable; a resident-profile trace attributed to the wrong person is not.
7. No physical barrier commands are connected. Current “activation” is accounting/state only in the sandbox.
8. Service catalog entries with historical activity are retired/effective-dated, never hard-deleted.
9. A phone used to open a barrier is a separate private access credential. It may be a driver/relative/tenant number and does **not** have to match Telegram or contact-phone data.

## 5. Business rules already agreed
### Parking / vehicles
- Apartment identifiers are strings. A future alias resolver must support combined records such as `84_85` and aliases `84`, `85`, `84_85`.
- For every vehicle, `parking_time` is an explicit business state:
  - `Day`
  - `Night`
  - `Inactive` internally, shown to users as **“Не користується паркуванням”**
- This is distinct from **“Автомобіля немає”**.
- Empty `parking_time` is unresolved critical data; it is never “correct data”.

### Resident profile verification
Phone access may be requested only after:
- an apartment is linked;
- either vehicles are explicitly resolved, or “no vehicle” is explicitly confirmed;
- every existing vehicle has a plate and explicit `parking_time`;
- resident confirmation of completed mandatory data;
- no pending operator correction request.

Make/model and colour are shown for review but are advisory only under the current policy. They do not block phone access.

### Phone access to two barriers
- Access points:
  - `BARRIER_FAR_01` — Far barrier No. 1
  - `BARRIER_NEAR_02` — Near barrier No. 2
- Price per access point:
  - connection: 200 UAH
  - monthly fee: 100 UAH
- Therefore:
  - one barrier: 200 UAH connection, 100 UAH/month
  - both barriers: 400 UAH connection, 200 UAH/month
- First monthly period rule: up to and including the calendar-month midpoint = current month; later = next month.
- Debt warning grace period: 10 days, stored as policy data.
- “No parking obligation” / non-car users are an explicit exception. They must not be treated as parking debtors.
- After debt deactivation, re-access is a new subscription/registration process.
- Order cards should show each selected barrier separately, with its own point status.

### Cashboxes
- `K1`–`K6` are individual concierge collection points; `K` is aggregate reporting.
- `C` is central cashbox.
- `O` is guard-post cashbox.
- Digital channel is planned.
- Cash receipt/payment/order/asset movements remain separately auditable.

## 6. Confirmed sandbox milestones
### A. Own remote reprogramming — completed
- `SO-20260626-000001`
- apartment 174
- payment #93
- flow completed: resident remote accepted → payment confirmed → reprogrammed → returned
- final order status: completed.

### B. New remote paid preorder — completed
- Interest: `SI-20260627-000001`
- cash notification: `N-2026062716157-000002`
- receipt: `R2-20260627-000002`
- payment #94
- real order: `SO-20260627-000003`, quantity 2
- supplier batch: `RB-20260627-000001`
- received two actual remotes and issued both.
- final result: completed.

### C. Two-barrier phone access — completed
- Structured interest: `SI-20260627-000003`
- apartment 174
- test private access phone: `+380501234567`
- selected both barriers
- connection 400 UAH; monthly fee 200 UAH
- cash notification: `N-20260627195153-000004`
- receipt: `R2-20260627-000004`
- payment #96
- auto-created order: `SO-20260627-000005`
- first monthly period: 2026-07
- subscription activated; order completed.
- This is a sandbox/accounting activation only, not a real barrier-controller command.

### D. Resident profile verification — completed for apartment 174
- Apartment 174 card successfully displayed a vehicle with `parking_time = Day`.
- Required data became `READY`.
- Phone access shown as allowed.
- Missing colour is only advisory.
- The confirmation button was correctly hidden after `READY`.

## 7. Current source/migration state
The following work is known to have been installed/tested in the sandbox from screenshots and console outputs:
- two-barrier phone-access schema migration and operational migration;
- cashier routing repair after phone-access V2;
- `service_code` / `base_service_code` compatibility repair;
- resident profile-verification schema migration;
- profile terminology/readiness V2;
- early profile-button routing repair;
- `critical_codes` UI repair;
- hide profile-confirmation button after `READY`.

Do **not** reapply older interim patch archives blindly. Treat the current local source tree as the baseline. If the next task needs a change, inspect the live local files first and create one clean replacement/installer with backups.

## 8. Current stopping point — next task
A read-only preflight was completed for apartment 40:

```text
Apartment: 40
Vehicle id: 20
Plate: AA0667HB
Model: GRANDIS
Colour: empty
parking_time: EMPTY
Result: SUITABLE_MISSING_PARKING_TIME
```

The preflight created no resident profile, no welcome, no resident request, no order, no payment and no audit record.

The next prepared package is:

```text
OSBB_profile_parking_time_test_v1_sandbox.zip
```

It has been generated and functionally tested by the prior assistant, but **has not yet been installed or run by the user**.

### Purpose of that next package
Create a separate **admin-only, no-write TEST** session for apartment 40 to test:
1. Empty `parking_time` → simulated phone access blocked.
2. Operator TEST selection: `Day`, `Night`, or `Не користується паркуванням`.
3. TEST queue / operator decision.
4. Test approval without changing `vehicles.parking_time`.
5. Confirmation that no resident profile, welcome, resident request, phone access, order, payment or source vehicle row was changed.

The package writes only to dedicated TEST tables:
- `profile_parking_time_test_schema_migrations`
- `profile_parking_time_test_sessions`
- `profile_parking_time_test_events`

Expected installation sequence, only after the user explicitly decides to proceed:
1. Stop bot (`Ctrl+C`).
2. Unpack package into `G:\Programming\Py\OSBB\`.
3. Run `RUN_INSTALL_profile_parking_time_test_v1.bat`.
4. Run `RUN_MIGRATE_profile_parking_time_test_sandbox.bat`.
5. Run `RUN_CHECK_profile_parking_time_test_sandbox.bat`.
6. Run `Start_OSBB_Live_Services_Sandbox_Bot_v1.bat`.
7. Telegram: Admin mode → `🧪 Тест parking_time` → `🧪 Відкрити TEST кв. 40`.

Do not enter apartment 40 as a resident and do not use “Change apartment” / “Verify my data” for it.

## 9. High-priority roadmap after that test
1. Build a proper read-only operator “preview resident profile” card, with an honest operator-view audit but no changes to resident profile state.
2. Add immutable snapshots of what a resident confirmed, plus database triggers protecting audit logs from direct update/delete.
3. Implement real controlled operator workflow for applying approved `parking_time` changes—not through TEST.
4. Extend phone-access debt monitoring, warning issuance, 10-day grace policy and point-by-point deactivation commands once the real controller integration is specified.
5. Continue operator verification per apartment and payment statements.
6. Later: unit/alias migration for commercial/technical units and combined apartment aliases.

## 10. What the new Business chat should do first
- Read this file and the project instructions file.
- Restate the next checkpoint and safety boundaries in 5–8 lines.
- Do not claim the pending parking_time TEST package is installed.
- Ask the user whether they want to install that prepared TEST package now.
- If a code error occurs, request the exact traceback and inspect the exact current source before offering another patch.
- Never create a new payment/notice/interest to compensate for an already-created record.

## 11. Contact / language
User: San Tretiak  
Primary work language: Russian; bot labels may be Ukrainian/Russian/English.  
User preference: give complete ready-to-run files/installers and exact run order; do not ask the user to hand-edit source code unless unavoidable; be candid about uncertainty.
