---
description: "Development standards (TDD workflow)"
alwaysApply: true
---
## TDD Workflow (Red → Green → Refactor)

### Phase 1: Understand & Plan (before any code)
- Frame the smallest useful change before touching code.
- Confirm the task against `TODO.md` and revise the list if priorities shift.
- Call out scope creep or overengineering as soon as it appears and wait for approval before expanding the task.
- Review `docs/user_flows/index.md` for affected flows (integration / manual validation).

### Phase 2: RED - Write Failing Tests First
- **Before writing ANY implementation code**, write comprehensive tests that:
  - Cover the expected behavior described in the task
  - Include edge cases and error conditions
  - Test the public interface/contract, not internals
  - Use descriptive test names that document requirements (e.g., `test_step_resolver_returns_error_when_selector_missing`)
- Run tests and **confirm they FAIL** with meaningful error messages
- If tests pass without implementation → tests are wrong or feature already exists

### Phase 3: GREEN - Implement Minimum Code
- Write **only** the code necessary to make failing tests pass
- No extra features, optimizations, or "while I'm here" changes
- Run the specific `pytest` slice for the area you touched
- All tests must pass before proceeding

### Phase 4: REFACTOR - Clean Up
- Improve code structure without changing behavior
- Keep functions below the flake8 `max-complexity=10` threshold
- Run `flake8` for style, `mypy .` for types, `pytest -m lint` for shared gate
- Structure code following `docs/architecture/overview.md`
- For test upkeep rules, follow `.cursor/rules/testing/RULE.md` (Test Maintenance).

### Phase 5: Document & Commit
- Update or add UF files when scope changes
- Capture durable context in `memory.md` as you go
- When adding a flow, log the identifier in the index
- When the work is stable, execute `.github/prompts/close-phase.prompt.md`

### Phase 6: Verify Before Marking Complete
- **NEVER mark a TODO item complete until you verify the feature works as named**
- If the task says "LLM does X" → write a test that mocks the LLM and asserts `.invoke()` was called
- If the task says "create prompts" → verify prompt files exist in `src/agents/planner/prompts/`
- If the task says "validates" → test that invalid input produces validation errors
- **Read the task name literally** - "LLM as Judge" means the LLM must judge, not deterministic code
- Run the specific tests that prove the named functionality before updating TODO.md

### Anti-patterns to Avoid
- ❌ Writing implementation first, then "covering" it with tests
- ❌ Writing tests that describe implementation details instead of requirements
- ❌ Skipping the "confirm tests fail" step
- ❌ Adding functionality not validated by a failing test
- ❌ Marking TODO complete when feature name doesn't match implementation
- ❌ "LLM" features that don't actually call the LLM
- ❌ Skipping prompt creation for LLM-dependent features
