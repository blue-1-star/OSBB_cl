# OSBB Development Conventions

<!-- Слито из двух файлов: OSBB/Development_Conventions.md (2026-07-06)
     и OSBB/KNOWLEDGE/Development_Conventions.md (2026-07-04) —
     существовали параллельно под одним именем в разных папках. -->

## Working Directory

Unless explicitly required otherwise, all development work is performed from the project root:

`G:\Programming\Py\OSBB`

This includes:

- git commands
- Python tools
- maintenance scripts
- generators
- Runner utilities
- documentation writers

Tools should use project-relative paths whenever possible.

## Safe Change Process

Default sequence:

1. Inspect current state.
2. Run dry-run.
3. Create backup if data or source files will change.
4. Apply.
5. Run validation:
   - `python -m py_compile ...`
   - targeted smoke test
6. Commit meaningful stage to Git.
7. Update documentation.

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

## Project Documentator

The Project Documentator is a standing project role.

No architectural decision is considered complete until the relevant documentation is updated.

Documentation targets:

- `MODULE.md`
- component `*.MODULE.md`
- `README.md`
- `KNOWLEDGE/*.md`
- migration reports in `Recovered/`

The Documentator tracks:

- why a decision was made
- where the implementation lives
- how to test it
- how to roll it back
- what still remains planned