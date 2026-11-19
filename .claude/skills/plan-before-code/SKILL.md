---
name: plan-before-code
description: Use before any change that touches more than ~3 files, adds a dependency, changes a schema or public API, or when the request is ambiguous. Routes small changes to a short inline plan and large or irreversible ones to the Elephant-Goldfish design flow, and always ends with testable acceptance criteria.
license: MIT
metadata:
  version: "3.0"
  sources: "Anthropic harness design (2026): plan what/why, not how; Google Elephant-Goldfish Model (2026)"
---
# Plan before code

## Pick the route
| Change | Route |
|---|---|
| ≤ ~3 files, reversible, clear | **Inline plan** (below) |
| New component, schema/API change, migration, cross-team, or unclear requirements | **Design flow**: EGM if installed, otherwise `grill-with-docs` + inline plan |

## Design flow (EGM installed: `egm-*` skills are present)
EGM skills are user-invoked. Tell the user which command to run next. Do not imitate them.
1. `/egm-brainstorm` → concepts brief. 2. `/egm-draft` → design doc in `docs/designs/`. 3. `/egm-review` → fresh-context Goldfish reviewers. 4. `/egm-resolve` → fixes and decisions. The user approves the doc.
Then continue at "Acceptance criteria" below, taking the criteria from the approved doc.

## Inline plan
1. Restate the goal in 1–2 sentences. Explore the relevant code first: answer what you can from the codebase.
2. Options: if more than one approach is reasonable, give 2–3 with the main trade-off of each. Recommend one.
3. Steps small enough to verify. Name the files you expect to touch. List only the real risks.
4. Open decisions: one question at a time, each with your recommended answer. Use `grill-me` for one hard decision.

## Acceptance criteria (always)
2–6 testable statements of *what* must be true, not *how* to build it. Store them with `memory.py feature add --criterion ...` when `.agent-memory/` exists. The `evaluator` grades exactly these.

Stop and wait if a decision is irreversible (data migration, deletion, public API break, new paid dependency).
