from pathlib import Path
from datetime import datetime

root = Path.cwd()
stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

docs = {
    root / "KNOWLEDGE" / "Development_Conventions.md": """
# OSBB Development Conventions

## Working Directory

Unless explicitly required otherwise, all development work is performed from the project root:

`G:\\Programming\\Py\\OSBB`

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
""",

    root / "KNOWLEDGE" / "Project_Documentator.md": """
# OSBB Project Documentator

## Mission

The Project Documentator keeps OSBB from losing architectural decisions, workflow rules and recovery knowledge.

The role is not just to write nice text.

The role is to prevent undocumented work from becoming future archaeology.

## Responsibilities

The Documentator must record:

- new modules
- changed workflows
- role/access model changes
- database migration assumptions
- Runner-to-core migration steps
- test scenarios
- rollback points
- unfinished decisions

## Required Check

After every meaningful change, ask:

- Was a `MODULE.md` changed?
- Was a `KNOWLEDGE` document changed?
- Was a report written to `Recovered/`?
- Is the test scenario documented?
- Is rollback documented?

## Future Agent Role

This role may later be assigned to a Codex-style project agent.

The agent should not make business decisions.

It should detect undocumented changes and propose documentation patches for review.
""",
}

for path, block in docs.items():
    path.parent.mkdir(parents=True, exist_ok=True)
    old = path.read_text(encoding="utf-8") if path.exists() else ""
    title = block.strip().splitlines()[0]
    if title in old:
        print(f"SKIP: {path}")
        continue
    path.write_text((old.rstrip() + "\n\n" if old else "") + f"<!-- Added {stamp} -->\n\n" + block.strip() + "\n", encoding="utf-8")
    print(f"UPDATED: {path}")

claude = root / "CLAUDE.md"
text = claude.read_text(encoding="utf-8") if claude.exists() else "# CLAUDE.md\n"
marker = "## Project Documentator"
block = """
## Project Documentator

OSBB has a standing documentation role: Project Documentator.

No architectural change is complete until the related documentation is updated.

Default working directory for all tools and git commands:

`G:\\Programming\\Py\\OSBB`

Before leaving a topic, check whether these need updates:

- `MODULE.md`
- component `*.MODULE.md`
- `KNOWLEDGE/*.md`
- `Recovered/*.md`
"""
if marker not in text:
    claude.write_text(text.rstrip() + "\n\n" + block.strip() + "\n", encoding="utf-8")
    print(f"UPDATED: {claude}")
else:
    print(f"SKIP: {claude}")
