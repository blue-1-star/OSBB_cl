<!-- Added 2026-07-04 20:57:54 -->

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
