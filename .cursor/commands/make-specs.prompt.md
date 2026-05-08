---
mode: 'agent'
description: 'Write a concise, actionable software specification for this project.'
---

# Create Project Specification (PRD)

Goal: Write a concise, actionable software specification for this project.

Instructions:
1) Ask for a brief description of the project if not provided.
2) Ask any clarifying questions **before** generating if requirements are unclear.
3) Output a single Markdown file named `specification.md` in the project root.
4) Keep it brief and practical (sections and bullets over prose). No emojis.

Required sections (use short bullets):
- Overview: what we’re building and for whom.
- Scope & Out-of-scope.
- Key user scenarios / flows (2–5).
- API / modules (endpoints, I/O, contracts).
- Data sources & constraints.
- Testing strategy (unit, integration, E2E).
- Assumptions & open questions.

Template:
- Project: [name]
- Overview:
- Scope:
- Out-of-scope:
- User scenarios:
- API / Modules:
- Data / Config:
- Testing strategy:
- Risks / Constraints:
- Assumptions:
- Open questions:

Acceptance:
- File created at `./specification.md`
- Clear enough to implement minimal MVP + tests
- ≤ 200 lines
