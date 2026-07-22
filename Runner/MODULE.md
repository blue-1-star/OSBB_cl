# MODULE: Runner

Status: draft  
Created: 2026-07-03  

## Purpose

Runner is the OSBB business scenario execution subsystem.

It is not a unit-test framework. Runner describes, executes, journals, and verifies complete business workflows.

## Core rule

No scenario starts from Python.

Order:

1. Markdown scenario passport.
2. YAML executable scenario.
3. Runner execution.
4. DB snapshot.
5. DB diff.
6. Markdown/JSON report.

## Responsibilities

- Create disposable Working DB from a known source DB.
- Execute approved business scenario.
- Record each step.
- Verify expected database changes.
- Store useful evidence, not unnecessary full DB copies.
- Keep failed Working DB copies only when debugging requires them.

## Scenario states

Draft → Approved → Executable → Verified → Regression → Archived

## Directory map

```text
Runner/
  MODULE.md
  README.md
  KNOWLEDGE/
  SCENARIOS/
  SCENARIOS_YAML/
  REPORTS/
  DB_SNAPSHOTS/
  DB_DIFFS/
  LOGS/
```

## Notes

<!-- MODULE_NOTES:BEGIN -->
- 2026-07-03 18:48:09 - Runner scaffold generated.
<!-- MODULE_NOTES:END -->

## Change log

<!-- MODULE_CHANGELOG:BEGIN -->
- 2026-07-03 18:48:09 - Runner module created by runner_generator.py.
<!-- MODULE_CHANGELOG:END -->
