# Jira-triage

**Jira-triage** is a QA-focused AI assistant for triaging Jira work: it helps separate true **bugs** from **stories**, flags accidental misclassification (especially when support files a story as a bug), and suggests **more realistic priorities** so fewer items land as P0 or P1 by default. Behaviour is **advisory** for now and can expand as you harden workflows and integrations.

**Audience:** QA engineers and support agents who touch Jira classification and severity.

## Quick start

1. Open [`.cursor/commands/make-specs.prompt.md`](.cursor/commands/make-specs.prompt.md) and run it to shape requirements and specs for this repo.
2. Open [`.cursor/commands/make-todo.prompt.md`](.cursor/commands/make-todo.prompt.md) and run it to turn specs into an actionable backlog.

## Scaffold overview

This tree is a **Cursor-first** scaffold: project conventions live under `.cursor/` (commands, rules, and optional skills). As you add application code, align with the rules in `.cursor/rules/` for development workflow, **TDD**, and **testing** expectations. When you introduce a real codebase, add the usual **linting** (for example `flake8` / `ruff`), **type checking** (`mypy`), and **`pytest`** markers your team uses, plus **`docs/architecture/`** and **`docs/user_flows/`** so agents and humans share the same map.

## References

| What | Where |
|------|--------|
| Specs workflow | [`.cursor/commands/make-specs.prompt.md`](.cursor/commands/make-specs.prompt.md) |
| TODO / backlog workflow | [`.cursor/commands/make-todo.prompt.md`](.cursor/commands/make-todo.prompt.md) |
| Other Cursor commands | [`.cursor/commands/`](.cursor/commands/) |
| Standing rules (dev, testing, TDD) | [`.cursor/rules/`](.cursor/rules/) |

When you add user journeys, index them in `docs/user_flows/index.md` and keep that path in sync with your test markers.
