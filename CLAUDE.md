# DebugTrace API

Python FastAPI service wrapping five language debuggers (Python, Go,
C++, Java, JS) behind POST /debug. See SUPER_PRD.md for the full spec.

## How to work on this project

1. **For any feature work**: load `.claude/skills/debugtrace-feature-executor/SKILL.md`
   first. It defines the end-to-end workflow.
2. **For principles questions**: `.claude/skills/design-principles/SKILL.md` and
   `.claude/skills/design-patterns/SKILL.md`.
3. **For adding/modifying adapters**: `.claude/skills/debug-adapter-implementation/SKILL.md`
   plus `.claude/skills/subprocess-lifecycle-management/SKILL.md`.

## Conventions

- Python 3.11+, FastAPI, Pydantic v2, pytest.
- No `print` debugging in committed code — use `logging`.
- Every adapter must have a `try/finally` that cleans up subprocesses.
- Every PR adds tests. Coverage check runs in CI.
- Commit messages: Conventional Commits format (`feat:`, `fix:`, `test:`,
  `refactor:`, `chore:`).

## Non-negotiables (from SUPER_PRD §10 Done Definition)

- `pytest tests/ -q` green on a fresh checkout.
- No subprocess (`dlv`, `node`, `java`, lldb) survives 5s after request finish.
- OCP boundary holds: only `factory.py` imports concrete adapters.

## Tool permissions

Default `Bash`, `Read`, `Write`, `Edit`, `Glob`, `Grep` are fine. The
`subprocess-lifecycle-management` skill may invoke `pgrep` and `kill` —
allow these in the bash tool's allowlist.