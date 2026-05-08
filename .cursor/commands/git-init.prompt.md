---
mode: 'agent'
description: 'Initialize git for a new project derived from this scaffold.'
---

# Git Initialization for Derived Projects

## Step 1: Confirm Project Type
Ask the user: "Is this the original scaffold repository, or a new project derived from it (clone/fork)?"
- If **scaffold**: exit; no changes needed.
- If **derived project**: proceed to Step 2.

## Step 2: Gather Project Context
Ask the user:
- Project name
- One-sentence project goal or description
- Primary use case or target audience (optional but helpful for README)

## Step 3: Wipe Repository History
1. Confirm with the user that wiping history is acceptable.
2. Remove the existing `.git` directory: `rm -rf .git`
3. Reinitialize: `git init --initial-branch main`

## Step 4: Generate Fresh README
Create a new `README.md` with:
- Project name and goal from Step 2
- Quick Start pointing to `make-specs.prompt.md` and `make-todo.prompt.md`
- Brief scaffold overview (testing, linting, architecture docs)
- References section (instructions, prompts, user flows)

## Step 5: Initial Commit
1. Stage all files: `git add .`
2. Commit with message: `"Initial commit: [project-name] scaffold"`

Acceptance:
- Fresh git history with no upstream template references.
- README reflects the new project's identity.
- User is ready to run `make-specs.prompt.md` next.

Follow the steps EXACTLY, do not make the project at this stage. Do not create any new files besides the 'README.md' file.
