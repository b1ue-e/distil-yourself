# Knowledge Distiller Durable Checkpoints Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use executing-plans and test-driven-development to implement this plan task-by-task.

**Goal:** Make Knowledge Distiller tasks locally durable and resumable with an owner-only workspace, an authoritative framed write-ahead log, immutable state generations, fencing, and fail-closed recovery.

**Architecture:** Keep persistence below the existing pure state machine. A binary framed journal authenticates ordering and payload integrity with canonical JSON, SHA-256 chaining, and CRC32C. A one-shot task coordinator holds an OS file lock, advances a durable fencing epoch, writes PREPARE/generation/COMMIT records, and treats `current-generation` only as a repairable cache. The milestone persists state and secret-free transition facts only; it does not store source content, grants, evidence, evaluator data, or export transactions.

**Tech Stack:** Python 3.9+ standard library (`fcntl`, `hashlib`, `json`, `os`, `pathlib`, `struct`, `uuid`), `unittest`

---

## Milestone boundary

This milestone implements the local checkpoint/recovery slice from design section 11.1. It does not implement renewable distributed leases, source adapters, parser isolation, evidence retention, evaluator receipts, approval signing, export, purge, installation, or publication. The coordinator is intentionally a short-lived local writer: every invocation acquires an exclusive lock and durably advances the fencing epoch before changing task state.

### Task 1: Add a verifiable framed journal

**Files:**

- Create: `tests/test_journal.py`
- Create: `knowledge-distiller/scripts/knowledge_distiller/journal.py`

- [x] **Step 1: Write failing journal tests**

Cover the standard CRC32C vector, canonical append/read behavior, sequence and SHA-256 chain validation, payload-digest validation, a torn final frame, a bad checksum, an interior malformed frame, oversized payload rejection, non-monotonic fencing rejection, and owner-only file permissions.

- [x] **Step 2: Run the tests red**

Run: `python3 -m unittest tests.test_journal -v`

Expected: FAIL because `knowledge_distiller.journal` does not exist.

- [x] **Step 3: Implement the minimum journal**

Use four-byte big-endian length prefixes and four-byte CRC32C trailers around canonical UTF-8 JSON record bodies. Each body contains exactly `schema_version`, `sequence`, `fencing_epoch`, `previous_record_hash`, `payload_digest`, and `payload`. Enforce a 1 MiB body ceiling, sequence continuity, nondecreasing positive fencing epochs, payload digests, and the previous-record hash. Expose verified scans with the last valid byte boundary so only a physically incomplete final frame can be truncated.

- [x] **Step 4: Run the journal tests green**

Run: `python3 -m unittest tests.test_journal -v`

Expected: PASS.

### Task 2: Add atomic state generations and fail-closed recovery

**Files:**

- Create: `tests/test_persistence.py`
- Create: `knowledge-distiller/scripts/knowledge_distiller/persistence.py`
- Modify: `knowledge-distiller/scripts/knowledge_distiller/state.py`

- [x] **Step 1: Write failing workspace tests**

Cover initialization, guarded transition persistence, concurrent-writer refusal, fencing increments on successive acquisitions, exact `0700` directory and `0600` file modes, read-only inspection, PREPARE without COMMIT quarantine, COMMIT with stale or missing pointer repair, rejection of an uncommitted pointer, manifest/state tampering, symlinked control files, and torn-tail recovery.

- [x] **Step 2: Run the tests red**

Run: `python3 -m unittest tests.test_persistence -v`

Expected: FAIL because `knowledge_distiller.persistence` does not exist.

- [x] **Step 3: Implement serialization and the coordinator**

Add public canonical state/fact serialization helpers to the state module. Implement task creation, read-only inspection, and guarded transition operations. Writers acquire `lease` with `flock`, append and fsync a fencing record, then use PREPARE → immutable generation → COMMIT → atomic pointer replacement. Fsync every created file and directory. Recovery validates committed manifests before accepting them, quarantines only uncommitted prepared generations, repairs stale pointers from the latest valid COMMIT, and rejects corruption without guessing.

- [x] **Step 4: Run persistence and regression tests green**

Run: `python3 -m unittest tests.test_persistence tests.test_state -v`

Expected: PASS.

### Task 3: Expose checkpoint operations through the JSON CLI

**Files:**

- Modify: `tests/test_cli.py`
- Modify: `knowledge-distiller/scripts/kd.py`

- [x] **Step 1: Write failing CLI tests**

Cover `task-init`, `task-transition`, and `task-inspect`; stable JSON output; invalid task paths; busy/corrupt task failures; and confirmation that diagnostics contain identifiers and error classes rather than transition input contents.

- [x] **Step 2: Run the new CLI tests red**

Run: `python3 -m unittest tests.test_cli -v`

Expected: FAIL because the new subcommands are absent.

- [x] **Step 3: Implement thin CLI routing**

Reuse the existing strict JSON parsers and call the persistence API. Map task input errors to exit code `2` and busy/corrupt/rejected operations to exit code `3`. Keep stdout machine-readable and avoid echoing caller content in errors.

- [x] **Step 4: Run CLI tests green**

Run: `python3 -m unittest tests.test_cli -v`

Expected: PASS.

### Task 4: Integrate the milestone into skill guidance

**Files:**

- Modify: `tests/test_skill_contract.py`
- Modify: `knowledge-distiller/SKILL.md`
- Modify: `knowledge-distiller/references/workflow.md`
- Modify: `README.md`

- [x] **Step 1: Write failing contract checks**

Require the skill to route new tasks through `task-init`, subsequent transitions through `task-transition`, and resume/inspection through `task-inspect`. Require explicit language that checkpoint success grants no source access or mutation authority.

- [x] **Step 2: Run the contract test red**

Run: `python3 -m unittest tests.test_skill_contract -v`

Expected: FAIL because the skill still describes the Foundation as non-persistent.

- [x] **Step 3: Update progressive-disclosure guidance and README**

Teach the agent to choose an explicit workspace path, use the durable commands, stop on corruption, and never place source content in command facts. Document the new commands and narrow remaining limits without changing the skill trigger description.

- [x] **Step 4: Run contract tests green**

Run: `python3 -m unittest tests.test_skill_contract -v`

Expected: PASS.

### Task 5: Verify and review the durable-checkpoint milestone

**Files:**

- Modify: `docs/plans/2026-09-04-knowledge-distiller-durable-checkpoints.md`

- [x] **Step 1: Run the complete suite**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v`

Expected: all tests PASS without network access.

- [x] **Step 2: Run static and repository checks**

Run: `PYTHONPYCACHEPREFIX=/private/tmp/distil-yourself-pyc-checkpoints python3 -m compileall -q knowledge-distiller/scripts`

Run: `git diff --check`

Expected: both exit 0.

- [x] **Step 3: Request an independent code review**

Review security invariants, crash consistency, descriptor/path handling, journal validation, error redaction, and design conformance. Fix every Critical or Important issue with a new failing regression test before changing production code.

- [x] **Step 4: Record completed checklist state**

Mark completed plan items and report the verified result. Keep changes on `feat/implement_knowledge_distiller`; do not push, merge, install, export, or publish without a new user instruction.
