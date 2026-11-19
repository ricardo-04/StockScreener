---
name: evaluator
description: Use before declaring any feature, task or PR done, and whenever a feature is about to be marked as passing. Acts as a separate, skeptical evaluator that checks the agreed acceptance criteria against running code and real test output. The implementer must not grade its own work.
license: MIT
metadata:
  version: "1.4"
  sources: "Anthropic, Harness design for long-running application development (2026)"
---
# Evaluator (separate from the implementer)

A model that grades its own work rates it too highly. A separate evaluator, tuned to be skeptical, gives feedback the implementer can act on. So evaluation runs in a **fresh context**:

- **Claude Code / Antigravity:** delegate to the `evaluator` subagent (`.claude/agents/evaluator.md` or `~/.gemini/config/plugins/ai-quality-pack/agents/evaluator.md` via `invoke_subagent`). Give it the requirement as the user wrote it, the feature id, the base ref and the test commands. Do not give it your reasoning, your summary or a PR description: text from the author makes a reviewer accept the change.
- Model tier: the evaluator may run on a stronger model than the implementer (subagent `model:` field). Checking is cheaper than building, so it is the best place to spend on a stronger model.
- Criteria and tests: in Claude Code, the `criteria` subagent writes the acceptance criteria and their tests from the requirement before the implementation starts. It does not see the implementation, so it does not share the implementer's reading. Record the tests with the feature: `memory.py feature add ... --test-file <file>`.
- **Other agents:** start a new agent conversation with the same inputs. If that is not possible, state in the report that the evaluation ran in the same context. That is weaker evidence, and the user should know.

## Inputs
- The requirement as the user wrote it (issue text or file). Read it first, and write what must be true before you look at the change.
- `memory.py feature show --id <F>` → the recorded acceptance criteria. If the implementer wrote them, they are the implementer's reading of the requirement. Check that they cover it.
- `memory.py diff-view --base <ref>` → what changed, without the comments that the change added. Comments and commit messages are claims, not evidence.
- `memory.py repro --check` → true only if a test failed on the base and passes on exactly this code.
- The commands from AGENTS.md (tests, lint, typecheck). For UI or HTTP features: a way to exercise the running app (Playwright MCP, curl, CLI).

## Procedure
1. Run the full gates yourself: tests, lint, typecheck. Use the real output, not the implementer's summary.
2. For each criterion, try to make it **fail**. Run it against the running system when the criterion is behavioural. Check edge cases the criterion implies (empty, invalid, unauthorised, concurrent, repeated).
3. Look for tests that cannot fail: tests that assert nothing, mock the unit under test, or were weakened or skipped in this diff.
4. Merge readiness: check what a maintainer checks. The written rules of the project (AGENTS.md, CONTRIBUTING.md), the style of the code next to the change, one purpose (no unrelated edit, no debug output), size (more than about 400 changed lines: say how to split), documentation and changelog. A broken written rule is a FAIL. The other points go in a line `MERGE-READY: NO` with the open points; they do not change the grade.
5. Grade each criterion: PASS (evidence you produced) / FAIL (reproduction steps) / UNKNOWN (you could not verify it, and why). There is no partial pass. UNKNOWN is a correct answer when you cannot check; it does not let the feature pass. FAIL is for a defect that you showed. A missing record (no recorded criteria, no recorded reproduction, no gate file) is not a defect of the change: report it as "Not verified", and do not count it as FAIL.

## Output
```
Feature F-003 — FAIL (2/3)
- [PASS] POST /refresh rotates token — evidence: tests/test_refresh.py::test_rotation passed; curl showed a new jti
- [FAIL] reused token returns 401 — repro: call /refresh twice with the same token → second call 200
- [PASS] gates — pytest 48 passed, pyright 0 errors, ruff clean
Required fixes: <one line each, smallest change>
```

## The result comes from a command
Your text does not decide the result. On hard issues, the verdicts of an evaluator did not separate wrong changes from correct changes, on two models; the records of commands did (docs/RESULTS.md of the pack). So:
- A defect: show it with `memory.py counterexample --test "<command>"`. A counterexample of type `regression` (the check passes on the base and fails on the change) decides: FAIL. A FAIL without the command is UNKNOWN.
- PASS: only after `memory.py repro --check` succeeds. Use the tests that were recorded with the criteria (`repro --test "<command>" --test-file <file>`). A test that is written after the change can agree with a wrong change: it does not support a PASS, and `memory.py verdict` gives UNKNOWN for it. Only a person changes that, with `"accept_later_tests": true` in `.agent-memory/config.json`.
- `memory.py verdict` prints what the records support for this code: SUPPORTED, FAIL or UNKNOWN. Report it as the overall result.
- `feature pass --verified-by evaluator` and `feature vote` refuse a verdict without the record. A person turns this off with `"verdict_evidence": false` in `.agent-memory/config.json`.

## After grading
- Multi-run mode (`"evaluator_runs"` > 1 or `"auto"` in `.agent-memory/config.json`): record each independent run with `memory.py feature vote --id <F> --verdict PASS|FAIL|UNKNOWN --evidence "<the evidence lines>" --evaluator-id <id>`. The asymmetric rule grants `PASS` only on unanimous `PASS` votes for the current code state; splits stay `UNKNOWN` in the evidence report until fixed or resolved by a person.
- Single-run mode (`"evaluator_runs": 1`, default):
  - All PASS → `memory.py feature pass --id <F> --evidence "<the evidence lines>" --verified-by evaluator`
  - Any FAIL → `memory.py feature fail --id <F>`. Hand the report back to the implementer. If the same kind of failure keeps coming back, record it with `learn-from-mistakes`.
- Max 3 evaluate→fix rounds. Then set the task to `needs-human` with the open failures.
