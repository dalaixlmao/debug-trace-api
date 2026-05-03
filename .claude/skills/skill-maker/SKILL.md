---
name: skill-maker
description: Create a new Claude Code skill (SKILL.md). Use when asked to create a skill, custom command, or reusable workflow for Claude Code.
argument-hint: "<skill-name>"
disable-model-invocation: false
---

Create a new Claude Code skill for $ARGUMENTS.

## Steps

1. Determine the skill location based on scope:
   - Personal (all projects): `~/.claude/skills/<skill-name>/SKILL.md`
   - Project-specific: `.claude/skills/<skill-name>/SKILL.md`
   - Default to project-level unless the user says otherwise

2. Create the skill directory and write `SKILL.md` with:

### Required structure

```markdown
---
name: <kebab-case-name>            # becomes the /slash-command; omit to use directory name
description: <what it does and WHEN to use it — front-load the key trigger phrase>
# Optional fields below:
when_to_use: <additional trigger phrases or example requests>
argument-hint: "[arg1] [arg2]"     # shown in autocomplete
arguments: arg1 arg2               # names positional $ARGUMENTS[0], $ARGUMENTS[1] as $arg1, $arg2
disable-model-invocation: true     # set for side-effect workflows (deploy, commit, send) you trigger manually
user-invocable: false              # set for background knowledge Claude loads silently
allowed-tools: Bash(git *) Read    # tools pre-approved when skill is active
context: fork                      # run in isolated subagent (use only for explicit task skills, not reference content)
agent: Explore                     # subagent type when context: fork (Explore, Plan, general-purpose, or custom)
model: sonnet                      # model override for this skill's turn
effort: high                       # low | medium | high | xhigh | max
paths: src/**/*.ts, tests/**       # activate only for matching files
---

Skill body here...
```

### Frontmatter decision guide

| Situation | Frontmatter to add |
|---|---|
| Workflow with side effects (deploy, commit, send) | `disable-model-invocation: true` |
| Background knowledge, not a user action | `user-invocable: false` |
| Needs git/bash without per-use prompts | `allowed-tools: Bash(git *) ...` |
| Heavy research that should not pollute main context | `context: fork` + `agent: Explore` |
| Should only activate for specific file types | `paths: src/**/*.ts` |
| Takes a single argument like an issue number | `argument-hint: [issue-number]` |
| Takes named args | `arguments: file format` (then use `$file`, `$format`) |

### Body guidelines

- **Reference skills** (conventions, patterns, style): write standing instructions Claude applies throughout a task.
- **Task skills** (deploy, fix-issue, commit): write numbered steps; add `disable-model-invocation: true`.
- Use `$ARGUMENTS` for the full argument string; `$ARGUMENTS[0]` / `$0` for positional access.
- Use `` !`command` `` to inject live shell output before Claude sees the prompt (e.g., `` !`gh pr diff` ``).
- Use `${CLAUDE_SKILL_DIR}` to reference scripts bundled with the skill directory.
- Keep `SKILL.md` under 500 lines. Move large reference docs to sibling files and link them.
- Add `ultrathink` anywhere in the body to enable extended thinking.

### Supporting files (optional)

```
my-skill/
├── SKILL.md          # required — overview + navigation
├── reference.md      # detailed docs, loaded only when needed
├── examples.md       # sample outputs
└── scripts/
    └── helper.py     # executable scripts
```

Reference them in `SKILL.md`:
```markdown
- Full API details: [reference.md](reference.md)
- Examples: [examples.md](examples.md)
```

## Example: reference skill

```markdown
---
name: api-conventions
description: REST API design conventions for this codebase. Use when writing or reviewing API endpoints.
---

When writing API endpoints:
- Use kebab-case for URL paths
- Use camelCase for JSON properties
- Always include pagination for list endpoints (`limit`, `offset`, `total`)
- Version APIs in the URL path (/v1/, /v2/)
- Return `{ error: string, code: string }` for all error responses
```

## Example: task/workflow skill

```markdown
---
name: fix-issue
description: Fix a GitHub issue end-to-end
argument-hint: "[issue-number]"
disable-model-invocation: true
allowed-tools: Bash(gh *) Bash(git *)
---

Fix GitHub issue $ARGUMENTS following our coding standards.

1. `gh issue view $ARGUMENTS` — read the issue
2. Search the codebase for relevant files
3. Implement the fix
4. Write and run tests
5. Ensure lint and type checks pass
6. Commit with a descriptive message
7. Push and open a PR referencing the issue
```

## What NOT to put in a skill

- Things Claude can infer from reading code (use CLAUDE.md only for what Claude can't guess)
- Information that changes frequently (use live `!` injection instead)
- Duplicate of existing CLAUDE.md content
- Anything better suited to a hook (deterministic, must-run behavior → use hooks)
