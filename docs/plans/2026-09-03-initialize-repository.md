# Distil Yourself Repository Initialization Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Initialize the empty `b1ue-e/distil-yourself` repository with the approved Knowledge Distiller specification and a concise project entry point.

**Architecture:** Keep the initial repository documentation-only. `README.md` provides orientation and scope, while `docs/specs/knowledge-distiller-design.md` is the canonical approved design; no runtime, CI, package manifest, or generated skill implementation is introduced.

**Tech Stack:** Markdown, Git, GitHub CLI (`gh`)

---

### Task 1: Publish the approved documentation baseline

**Files:**

- Create: `README.md`
- Create: `docs/specs/knowledge-distiller-design.md`
- Create: `docs/plans/2026-09-03-initialize-repository.md`

- [x] **Step 1: Verify the remote repository is empty and the local clone is on unborn `main`**

Run: `git status --short --branch`

Expected: `No commits yet on main` with no tracked or untracked project files before scaffolding.

- [x] **Step 2: Add the repository README and exact approved design**

The README must identify the project as a private, resumable Skill Factory, link the canonical specification, state that implementation has not started, and preserve the v1 exclusions for installation, publication, and generated executable domain code.

- [x] **Step 3: Verify documentation integrity and scope**

Run: `git diff --check`

Expected: exit 0 with no whitespace errors.

Run: `cmp docs/specs/knowledge-distiller-design.md ../docs/superpowers/specs/2026-09-03-knowledge-distiller-design.md`

Expected: exit 0, proving that the remote-ready specification is byte-identical to the approved source.

- [x] **Step 4: Create the initial commit**

Run: `git add README.md docs/specs/knowledge-distiller-design.md docs/plans/2026-09-03-initialize-repository.md`

Run: `git commit -m "docs: initialize distil yourself design"`

Expected: one root commit containing only the three documentation files.

- [x] **Step 5: Publish and verify `main`**

Run: `git push -u origin main`

Expected: the new `main` branch is created on `b1ue-e/distil-yourself`.

Run: `gh repo view b1ue-e/distil-yourself --json defaultBranchRef,url`

Expected: `defaultBranchRef.name` is `main` and the URL is `https://github.com/b1ue-e/distil-yourself`.
