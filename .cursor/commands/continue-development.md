description: 'Continue working on the project using TDD (Red → Green → Refactor)'

1. Read the next task from `TODO.md`; frame the smallest useful change and flag scope creep.
2. Plan the tests that prove the requirement; pick the right file in `tests/unit/` or `tests/integration/`.
3. RED: Write the tests first and run them to confirm they FAIL.
   - **For LLM features**: Include test that mocks LLM and asserts `.invoke()` is called
   - **For "create X" tasks**: Test that X actually exists and works
4. GREEN: Implement only the code needed to make those tests pass; rerun the targeted `pytest` slice.
   - **For LLM features**: Create prompts in `src/agents/planner/prompts/` BEFORE implementation
5. REFACTOR: Clean up, then run `flake8` (or `pytest -m lint`), `mypy .`, and the relevant `pytest` slice.
6. **VERIFY before marking complete**:
   - Re-read the task name literally - does your implementation match what the name says?
   - "LLM validates" → LLM must be called. "Create prompts" → prompts must exist.
   - Run the tests that prove the NAMED functionality works, not just that code runs.
   - **DO NOT update TODO.md until verification passes.**
7. Document: update UF/docs/memory as needed and run `.cursor/commands/close-phase.md` when stable.
