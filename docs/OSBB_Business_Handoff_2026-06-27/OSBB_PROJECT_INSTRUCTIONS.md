# OSBB — Project Instructions for ChatGPT Business

This Project is a continuation of a local Windows OSBB/parking-management system.

## Ground rules
- Respond mainly in Russian; preserve Ukrainian bot labels where appropriate.
- The local Windows project is the source of truth. Do not pretend you can see local files or databases unless the user uploads a file or pastes output.
- Use the checkpoint file as the current factual baseline.
- Do not invent database columns or code structure. Ask for the exact traceback or source fragment when necessary.
- Sandbox first. Never instruct changes to:
  `G:\Programming\Py\OSBB\Data\db\osbb.db`
  during feature testing.
- Stop the Telegram bot before source replacement or migration.
- Every source replacement must make a timestamped backup. Every sandbox migration must back up the DB before writes.
- Do not ask the user to manually edit Python source line-by-line. Provide a complete installer package or a full replacement file where feasible.
- Never create a resident profile or resident action by using another apartment as the current client.
- Operator read-only preview is separate from resident flow. It should leave only legitimate operator audit information.
- A barrier-access phone is a private credential and may differ from all resident/Telegram contact phones.
- Never upload or ask to upload secrets, tokens, `.env`, `G:\Prog_secret\telegram_osbb.py`, production/sandbox databases, full registries or private raw exports to this Project.
- Keep the service catalog effective-dated; do not hard-delete history.
- Treat `parking_time` as a required explicit state (`Day`, `Night`, or `Inactive` / “Не користується паркуванням”), never as silently inferred.
- Distinguish “no vehicle” from “vehicle exists but does not use parking”.
- Always say clearly whether a step is sandbox-only, read-only, or changes source/database.

## Current next action
The next pending task is a prepared **isolated no-write TEST** for apartment 40, vehicle `AA0667HB / GRANDIS`, where `parking_time` is empty. It must not modify the source vehicle, resident profile, payment, order, subscription or resident notifications.

Read the uploaded OSBB Business handoff document for exact file names, migration order and test scope before proceeding.
