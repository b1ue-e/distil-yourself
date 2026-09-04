# Knowledge Distiller Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first runnable Knowledge Distiller milestone: a standards-compatible meta-skill, a deterministic guarded workflow model, a closed-policy domain-draft validator, and a small CLI with automated tests.

**Architecture:** Use Python 3.9 standard-library modules so the repository has no install step or third-party runtime dependency. Keep workflow transitions and artifact validation as pure library functions under the meta-skill, with a thin CLI wrapper; keep behavioral eval prompts beside the skill while unit tests live outside the installable skill directory.

**Tech Stack:** Python 3.9+, `unittest`, JSON, Markdown, Agent Skills

---

## Milestone boundary

This plan implements the local deterministic foundation only. Network source adapters, trusted request broker, parser containers, evaluator vault, signed ApprovalSubject, write-ahead persistence, export, and purge brokers remain separate milestones because each needs its own threat-model tests and deployment boundary.

### Task 1: Establish red tests and the skill contract

**Files:**

- Create: `tests/test_skill_contract.py`
- Create: `knowledge-distiller/evals/evals.json`
- Create: `knowledge-distiller/SKILL.md`
- Create: `knowledge-distiller/references/workflow.md`
- Create: `knowledge-distiller/references/authorization.md`
- Create: `knowledge-distiller/references/artifact-policy.md`

- [x] **Step 1: Add a contract test before creating the skill**

The test loads `knowledge-distiller/SKILL.md`, verifies YAML delimiters, exact `name`, a third-person `Use when...` description, and references to the three progressive-disclosure documents. It also checks that the body stays below 500 lines and contains no automatic install or source-expansion instruction.

- [x] **Step 2: Run the contract test and observe the missing-file failure**

Run: `python3 -m unittest tests.test_skill_contract -v`

Expected: FAIL because `knowledge-distiller/SKILL.md` does not exist.

- [x] **Step 3: Add the minimal meta-skill and references**

`SKILL.md` must route discover, distill, update, and resume requests; require exact grants before content reads; ask only blocking questions; invoke the deterministic CLI for state and artifact checks; and stop before adapters, evaluation, or export that the current milestone does not implement.

- [x] **Step 4: Add three realistic eval prompts**

The eval set covers capability discovery from a Lark seed, resuming a paused session-backed task, and a near-miss generic summarization request that must not trigger personal-knowledge distillation.

- [x] **Step 5: Run the contract test green**

Run: `python3 -m unittest tests.test_skill_contract -v`

Expected: PASS.

### Task 2: Implement the guarded workflow model with TDD

**Files:**

- Create: `tests/test_state.py`
- Create: `knowledge-distiller/scripts/knowledge_distiller/__init__.py`
- Create: `knowledge-distiller/scripts/knowledge_distiller/state.py`

- [x] **Step 1: Write failing transition tests**

Tests cover discover/distill/update/repair entry guards, discovery grants, exact content authority, all-source rejection, claim-review routing, evaluation revision routing, pause/resume, authorization repair, budget exhaustion/extension, malformed imported topology, terminal-state rejection, and epoch increments on revision or repair.

- [x] **Step 2: Run the tests red**

Run: `python3 -m unittest tests.test_state -v`

Expected: FAIL because `knowledge_distiller.state` is absent.

- [x] **Step 3: Implement immutable workflow types and transition guards**

Expose `Mode`, `Phase`, `Event`, `TaskState`, `TransitionFacts`, `InvalidTransition`, and `transition`. Facts are explicit frozen booleans rather than arbitrary dictionaries. The transition function returns a new state and never mutates its input.

- [x] **Step 4: Run the state tests green**

Run: `python3 -m unittest tests.test_state -v`

Expected: PASS.

### Task 3: Implement the closed artifact policy with TDD

**Files:**

- Create: `tests/test_artifacts.py`
- Create: `knowledge-distiller/scripts/knowledge_distiller/artifacts.py`

- [x] **Step 1: Write failing validator tests**

Tests require `SKILL.md`, accept one-level UTF-8 references and compiler-allowlisted raster assets, and reject nested references, unknown files, executable bits, symlinks, hardlinks, invalid UTF-8, malformed structured references, mismatched image magic, case-fold collisions, package manifests, script directories, executable instructions, substitution races, sparse files, and resource-limit violations.

- [x] **Step 2: Run the tests red**

Run: `python3 -m unittest tests.test_artifacts -v`

Expected: FAIL because `knowledge_distiller.artifacts` is absent.

- [x] **Step 3: Implement deterministic validation and manifest generation**

Expose `ArtifactRecord`, `DraftValidationError`, and `validate_draft(Path, allowed_asset_digests)`. Successful validation returns records sorted by normalized relative path with SHA-256, byte size, and media type. Validation pins filesystem objects with descriptors, rechecks exact bytes and directory snapshots, and enforces entry and byte ceilings. Assets are rejected by default unless their digest is supplied by the compiler's reviewed template allowlist.

- [x] **Step 4: Run the artifact tests green**

Run: `python3 -m unittest tests.test_artifacts -v`

Expected: PASS.

### Task 4: Add the CLI with TDD

**Files:**

- Create: `tests/test_cli.py`
- Create: `knowledge-distiller/scripts/kd.py`

- [x] **Step 1: Write failing CLI tests**

Tests invoke subprocesses for `validate-draft` and `transition`, require structured JSON on stdout, require stable nonzero exit codes on policy/transition failure, and ensure error output contains codes without source contents.

- [x] **Step 2: Run the tests red**

Run: `python3 -m unittest tests.test_cli -v`

Expected: FAIL because `knowledge-distiller/scripts/kd.py` is absent.

- [x] **Step 3: Implement the thin CLI**

`validate-draft PATH` emits the sorted manifest and does not accept caller-supplied asset trust. `transition --state JSON --event NAME --facts JSON` emits the new immutable state. Exit code `2` represents invalid input and exit code `3` represents a rejected policy or transition.

- [x] **Step 4: Run the CLI tests green**

Run: `python3 -m unittest tests.test_cli -v`

Expected: PASS.

### Task 5: Verify the complete foundation

**Files:**

- Modify: `README.md`

- [x] **Step 1: Document runnable commands and milestone limits**

Add commands for unit tests, draft validation, and transition simulation. State explicitly that the four source adapters, sandbox, sealed evaluator, persistence, signing, export, and purge are not implemented in this milestone.

- [x] **Step 2: Run the complete test suite**

Run: `python3 -m unittest discover -s tests -v`

Expected: all tests PASS with no warnings or network access.

- [x] **Step 3: Run repository checks**

Run: `git diff --check`

Expected: exit 0.

Run: `PYTHONPYCACHEPREFIX=/private/tmp/distil-yourself-pyc-verify python3 -m compileall -q knowledge-distiller/scripts`

Expected: exit 0.

- [x] **Step 4: Commit the milestone**

Run: `git add README.md docs/plans/2026-09-03-knowledge-distiller-foundation.md knowledge-distiller tests`

Run: `git commit -m "feat: add knowledge distiller foundation"`

Expected: one feature commit on `feat/implement_knowledge_distiller` with tests and no generated cache files.
