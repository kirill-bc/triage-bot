---
mode: 'agent'
description: 'Write a concise, actionable TODO from the specification for this project'
---

# Create Phase-Ordered TODO from Specification

Goal: Produce an implementation roadmap derived from `specification.md`.

Preconditions:
- `specification.md` exists at repo root.

Instructions:
1) Read `specification.md`.
2) Create `TODO.md` in repo root with **phase-ordered**, actionable tasks.
3) Keep tasks small, testable, and in imperative voice.
4) Include checkboxes `[ ]` and clear deliverables per phase.
5) End each phase with a short “Done when …” line.
6) Keep it lean; no fluff.

Suggested structure:
1. Setup / Environment
2. Core Backend / API
3. Frontend / UX (if applicable)
4. Integration Tests
5. E2E Tests (Playwright)
6. Non-functional (logging, config, error handling)
7. Polish & Docs
8. Deployment (if in scope)

Acceptance:
- File created at `./TODO.md`
- Each task is verifiable (preferably by tests)
- ≤ 200 lines
