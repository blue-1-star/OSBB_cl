# OSBB Runner

Runner executes complete OSBB business workflows.

## Golden rule

Markdown first. YAML second. Python last.

## Workflow

```text
Business decision
  ↓
Scenario passport: Runner/SCENARIOS/*.md
  ↓
Executable scenario: Runner/SCENARIOS_YAML/*.yaml
  ↓
Runner execution
  ↓
DB before/after snapshots
  ↓
DB diff
  ↓
Report
```

## First scenario

```text
Runner/SCENARIOS/TEST_REMOTE_NEW_FROM_STOCK.md
Runner/SCENARIOS_YAML/TEST_REMOTE_NEW_FROM_STOCK.yaml
```

Runner v1 should prove one scenario well instead of pretending to cover everything.

Created: 2026-07-03
