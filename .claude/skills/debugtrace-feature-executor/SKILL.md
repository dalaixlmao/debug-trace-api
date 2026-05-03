---
name: debugtrace-feature-executor
description: Triggers when the user requests implementation, modification, testing, or debugging of any story or feature from the DebugTrace API project's SUPER_PRD.md. Drives the end-to-end story execution workflow: comprehend → plan → implement → verify → conclude. ALWAYS load this skill before working on the codebase.
trigger: Use when the user asks to implement, extend, modify, debug, or test any story or feature from the DebugTrace API project's SUPER_PRD.md. This is the entry point for all feature work on this codebase.
---

## Section 1: Mission

This skill drives every story in SUPER_PRD.md from a fresh start to a green-test, committed change. It encodes the correct execution loop — comprehend → plan → implement → verify → conclude — so that subprocess leaks, OCP violations, and untested acceptance criteria are caught before commit, not after. It does NOT contain language-specific or pattern-specific knowledge; it loads other skills when those are needed.

---

## Section 2: The Five Phases

### Phase 1 — Comprehend

1. Open `SUPER_PRD.md`. Locate the story being worked on (#1 through #8).
2. Read the entire mini-PRD section: Scope, Acceptance Criteria, Technical Design, Known Pitfalls, Test Plan.
3. Identify dependencies on other stories (e.g. Story 3 depends on Stories 1, 7, 8 being in place).
4. List the acceptance criteria as a numbered checklist — this becomes the verification gate in Phase 4.
5. Identify which other skills will be needed:

| Situation | Skill to load |
|---|---|
| Always | `design-principles` |
| Story 1 (endpoint) | `fastapi-pydantic-patterns` |
| Stories 2–6 (adapters) | `debug-adapter-implementation` |
| Stories 3–6 (subprocess-using adapters) | `subprocess-lifecycle-management` |
| Writing tests (any story) | `adapter-testing-strategy` |
| Touching code that imports an adapter | `design-patterns` (Strategy + Factory) |
| Refactoring or self-reviewing | `design-principles`, `python-idiomatic-design` |

---

### Phase 2 — Plan

6. Write a SHORT plan (5–15 lines) covering:
   - Files to create or modify (full paths)
   - Classes/functions to add (name + one-line responsibility)
   - Test cases that map 1:1 to acceptance criteria
   - Which patterns/principles apply (cite the relevant skill)

7. Ask clarifying questions **only** if a blocking ambiguity exists that the PRD does not resolve. State assumptions inline:
   > "Assuming X (per PRD §Y); proceeding."

8. **Do not ask** any of the following — the PRD already answers them:
   - "What test framework?" → pytest
   - "Pydantic v1 or v2?" → v2
   - "Where do files go?" → PRD §6 file structure
   - "What Python version?" → 3.11+

---

### Phase 3 — Implement

9. Build in this order — **not all at once**:

   a. **Interfaces / abstract classes** (e.g. `DebugAdapter` ABC)
   b. **Models / data shapes** (Pydantic models, enums, exceptions)
   c. **Concrete implementation**
   d. **Wire-up / registration** (factory entry, DI in `main.py`)
   e. **Tests**, in the same file order as the implementation

10. After each unit, self-check against design principles:
    - **KISS**: Is this the simplest thing that works?
    - **YAGNI**: Am I building anything not in the AC?
    - **DRY**: Is anything duplicated that should be extracted?
    - **SRP**: Does this class have one reason to change?
    - **OCP**: Did I have to modify existing code to add this?

11. If a self-check fails, **refactor before moving on** — not after.

---

### Phase 4 — Verify

12. Run the story's test file:
    ```bash
    pytest tests/test_<story>.py -q
    ```
    Iterate on failures. Do not proceed until green.

13. Run the smoke `curl` from the story's Test Plan section.

14. Run the OCP boundary check:
    ```bash
    grep -rE "from \.adapters\.\w+_adapter import" debug_service/ | grep -v factory.py
    ```
    Must return **empty**. Any output is a violation.

15. Run subprocess cleanup verification after the request completes:
    ```bash
    pgrep -f "dlv|node --inspect|java DebugClient|lldb"
    ```
    Must return **empty**. Any surviving process is a leak.

16. Walk through the AC checklist from Phase 1 step-by-step. Every item must map to either a passing test or a verified curl/pgrep output. **Any unchecked item blocks completion.**

---

### Phase 5 — Conclude

17. Generate a commit message following Conventional Commits:

    ```
    <type>(<scope>): <subject>

    <body: explain the WHY, not the WHAT>

    Closes Story #<N>
    ```

    - **Types**: `feat`, `fix`, `refactor`, `test`, `docs`, `chore`
    - **Scope examples**: `adapter-python`, `adapter-go`, `factory`, `session`, `endpoint`, `observers`, `decorators`
    - Subject: under 72 chars, imperative mood (`add`, not `added`)

18. Write a session history entry under `.history/` before the final user summary:

    ```bash
    .history/story-<N>-<unix_timestamp>.md
    ```

    The history file must include:
    - The user's story request.
    - Skills and project context loaded.
    - Files created or modified.
    - Implementation decisions and notable fixes.
    - Verification commands and their results.
    - Known blockers, environment notes, or skipped checks.

    Keep this record factual and session-scoped. Do not include unrelated work from other stories.

19. Update `SUPER_PRD.md` §10 Done Definition checklist if applicable.

20. Summarize for the user:
    - What was done
    - Which ACs are now green
    - What remains (blocked items, follow-up stories)

---

## Section 3: Decision Tree for Skill Loading

```
Is the story about the HTTP endpoint (Story 1)?
  └─ YES → load fastapi-pydantic-patterns

Is the story about an adapter (Stories 2–6)?
  └─ YES → load debug-adapter-implementation
       └─ Is it Story 3, 4, 5, or 6 (uses subprocess)?
            └─ YES → ALSO load subprocess-lifecycle-management

Does any file being touched import a concrete adapter?
  └─ YES → load design-patterns (Strategy + Factory)

Are you writing or modifying tests?
  └─ YES → load adapter-testing-strategy

Are you refactoring, reviewing, or shaping class structure?
  └─ YES → load design-principles
       └─ Is this Python class shape or API design?
            └─ YES → ALSO load python-idiomatic-design

Default (any story): load design-principles
```

---

## Section 4: Anti-patterns This Skill Prevents

- **Jumping into code** without reading the story's Pitfalls section first.
- **Asking "what test framework?"** — it's pytest, stated in the PRD and CLAUDE.md.
- **Skipping the OCP boundary grep** — violations are silent and compound across stories.
- **Marking a story done** without all ACs verified against actual test output.
- **Forcing a pattern** that wasn't in the design (e.g. wrapping a Pydantic model in a hand-rolled Builder just because it seemed architectural).
- **Mixing stories in one session** — modify only the story being worked on; cross-story contamination invalidates the OCP check.
- **Leaving subprocess cleanup to "later"** — implement `try/finally` at the point of subprocess creation, not as a follow-up.

---

## Section 5: Concrete Example — Story 3 (Go Adapter)

### Phase 1

Story 3 ACs (abbreviated checklist):
- [ ] AC1: `GoAdapter` implements `DebugAdapter` ABC
- [ ] AC2: Compiles Go source with `go build`
- [ ] AC3: Launches `dlv` in DAP/JSON-RPC mode
- [ ] AC4: Sets breakpoints via Delve protocol
- [ ] AC5: Retrieves local variables at breakpoint
- [ ] AC6: Returns `DebugResult` with `variables`, `stdout`, `stderr`
- [ ] AC7: Cleans up `dlv` process in `try/finally`
- [ ] AC8: Raises `AdapterError` on compile failure
- [ ] AC9: OCP: only `factory.py` imports `GoAdapter`

Dependencies: Stories 1 (endpoint), 7 (OCP factory), 8 (session model) must exist.

Skills to load: `debug-adapter-implementation`, `subprocess-lifecycle-management`, `adapter-testing-strategy`, `design-principles`.

### Phase 2

Plan:
```
Create:  debug_service/adapters/go_adapter.py  — GoAdapter class
Modify:  debug_service/factory.py              — add 'go': GoAdapter to _REGISTRY
Create:  tests/test_go_adapter.py              — 9 test functions (AC1–AC9)
```

- `GoAdapter._compile(src_path)` → runs `go build`, raises `AdapterError` on non-zero exit
- `GoAdapter._trace(binary, breakpoints)` → launches `dlv`, drives JSON-RPC, collects vars
- `GoAdapter.debug(request)` → orchestrates compile + trace, `try/finally` kills `dlv`

Patterns: Strategy (GoAdapter as a strategy), Factory (registry dispatch). Per design-patterns skill.

### Phase 3

Build order:
1. Confirm `DebugAdapter` ABC exists (Story 1/7 dependency)
2. Write `GoAdapter` class skeleton with `debug()` signature
3. Implement `_compile` with subprocess + error handling
4. Implement `_trace` with Delve JSON-RPC and variable extraction
5. Add `try/finally` subprocess cleanup
6. Register in `factory.py`
7. Write `tests/test_go_adapter.py` — one test per AC

### Phase 4

```bash
pytest tests/test_go_adapter.py -q
# → 9 passed

curl -s -X POST http://localhost:8000/debug \
  -H 'Content-Type: application/json' \
  -d '{"language":"go","source":"package main\nfunc main(){x:=1;_ =x}","breakpoints":[3]}'
# → {"variables": {...}, "stdout": "", "stderr": ""}

grep -rE "from \.adapters\.go_adapter import" debug_service/ | grep -v factory.py
# → (empty — OCP holds)

pgrep -f "dlv"
# → (empty — no leaked processes)
```

All 9 ACs checked. Story unblocked for commit.

### Phase 5

```
feat(adapter-go): add GoAdapter via Delve JSON-RPC

Implements the Go debug adapter using dlv in headless DAP mode.
Subprocess cleanup is guaranteed via try/finally even on DAP
protocol errors. OCP preserved: only factory.py references GoAdapter.

Closes Story #3
```
