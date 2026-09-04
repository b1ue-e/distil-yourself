# Knowledge Distiller Adapter Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use executing-plans and test-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Establish a fail-closed canonical event-graph contract, synthetic conformance fixtures, and a dated compatibility gate for Lark, Codex, Claude Code, and Trae without reading any real source content.

**Architecture:** Keep native products outside the trust boundary. This milestone defines and validates one strict JSON-compatible event graph, exposes a local validation command, and records evidence-backed adapter readiness separately from implementation. No native parser ships until an explicitly listed product/schema version passes the same synthetic and authorized-fixture conformance suite.

**Tech Stack:** Python 3.9+ standard library (`dataclasses`, `enum`, `hashlib`, `json`, `pathlib`), `unittest`, Markdown

---

## Milestone boundary

This milestone implements the feasibility gate and canonical contract from design section 6.2. It may use official documentation, installed CLI help, and synthetic fixtures only. It must not enumerate or read real Lark documents, local agent transcripts, session indexes, credentials, or account metadata. It does not implement native adapters, grants, the request broker, parser isolation, redaction, ingestion persistence, or source mutation.

### Task 1: Record the feasibility and compatibility gate

**Files:**

- Create: `knowledge-distiller/references/adapter-compatibility.md`
- Create: `knowledge-distiller/references/adapter-contract.md`
- Modify: `tests/test_skill_contract.py`

- [x] **Step 1: Write failing reference-contract tests**

Require both reference files, all four adapter rows, the exact readiness vocabulary `blocked`, and explicit statements that no native version is supported yet and no real source was inspected. Require the public skill to route adapter work through these references.

- [x] **Step 2: Run the tests red**

Run: `python3 -m unittest tests.test_skill_contract -v`

Expected: FAIL because the adapter references do not exist and `SKILL.md` does not route to them.

- [x] **Step 3: Write the compatibility and contract references**

Record the retrieval date, official sources, locally observed CLI versions, discovery/read interfaces, principal binding, revision or append semantics, missing native-schema guarantees, fixture requirements, and failure behavior. Mark Lark, Codex, Claude Code, and Trae `blocked`; distinguish product capability evidence from a stable parse contract. Define the canonical graph fields and invariant vocabulary used by the validator.

- [x] **Step 4: Run the reference tests green**

Run: `python3 -m unittest tests.test_skill_contract -v`

Expected: PASS.

### Task 2: Validate the canonical event graph

**Files:**

- Create: `knowledge-distiller/scripts/knowledge_distiller/adapters.py`
- Create: `tests/test_adapters.py`
- Create: `tests/fixtures/adapters/minimal-valid.json`
- Create: `tests/fixtures/adapters/tool-flow-valid.json`
- Create: `tests/fixtures/adapters/nested-agent-valid.json`

- [x] **Step 1: Write the smallest valid-graph test**

Load `minimal-valid.json`, validate it, and assert a manifest containing schema version, adapter identity, snapshot ID, event count, edge count, and canonical SHA-256 digest.

- [x] **Step 2: Run the test red**

Run: `python3 -m unittest tests.test_adapters.CanonicalGraphTest.test_minimal_graph_has_stable_manifest -v`

Expected: FAIL because `knowledge_distiller.adapters` does not exist.

- [x] **Step 3: Implement strict parsing and manifest generation**

Accept only the documented top-level, event, actor, content-segment, edge, reference, and fidelity-loss fields. Reject unknown fields, invalid runtime scalar types, empty or oversized identifiers, unsupported canonical/native versions, duplicate event/native IDs, duplicate stream positions, events over 1 MiB, and mismatched snapshot IDs. Return only counts, identifiers, and a canonical digest; never echo source text in an error.

- [x] **Step 4: Run the smallest test green**

Run: `python3 -m unittest tests.test_adapters.CanonicalGraphTest.test_minimal_graph_has_stable_manifest -v`

Expected: PASS.

- [x] **Step 5: Add failing graph-invariant tests**

Cover missing/external reference markers, graph cycles, unreachable tool outputs, duplicate or missing terminal tool results, non-contiguous output chunks, compaction content, explicit edit/retry/fork/supersession edges, child spawn and completion/join ordering, ambiguous owner attribution, prohibited fidelity loss, and unknown edge/event types.

- [x] **Step 6: Run the invariant tests red**

Run: `python3 -m unittest tests.test_adapters -v`

Expected: FAIL on the first unimplemented graph invariant.

- [x] **Step 7: Implement graph invariants minimally**

Build reachability over event-to-event edges. Enforce acyclicity; tool call/chunk/result cardinality and reachability; explicit marker edges; compaction honesty; exactly one preceding spawn for child roots; child termination before parent observation; and owner-claim eligibility only for deterministically resolved owner events. Permit only the fidelity losses listed in the design.

- [x] **Step 8: Run all adapter tests green**

Run: `python3 -m unittest tests.test_adapters -v`

Expected: PASS.

### Task 3: Expose local conformance validation through the CLI

**Files:**

- Modify: `tests/test_cli.py`
- Modify: `knowledge-distiller/scripts/kd.py`

- [x] **Step 1: Write failing CLI tests**

Cover `validate-event-graph PATH --expected-owner-id ID --expected-source-snapshot-id DIGEST`, canonical JSON success output, missing trust anchors, missing/non-regular/oversized/invalid-UTF-8 input, graph rejection at exit code `3`, and diagnostics that expose an error code without source text or absolute paths.

- [x] **Step 2: Run the CLI tests red**

Run: `python3 -m unittest tests.test_cli -v`

Expected: FAIL because the command is absent.

- [x] **Step 3: Implement the thin local CLI boundary**

Open one explicit local regular file without following symlinks, enforce a 64 MiB byte ceiling before parsing, decode through the strict duplicate-key-safe JSON decoder, and call the canonical validator with the caller-supplied immutable owner/snapshot trust anchors. Map missing files, unsafe files, encoding/JSON syntax errors, and missing arguments to exit code `2`; map duplicate keys and canonical contract rejection to exit code `3`. Do not discover files, traverse directories, derive trust anchors from the graph, or invoke a native source tool.

- [x] **Step 4: Run CLI and adapter tests green**

Run: `python3 -m unittest tests.test_cli tests.test_adapters -v`

Expected: PASS.

### Task 4: Integrate the gate into the skill guidance

**Files:**

- Modify: `knowledge-distiller/SKILL.md`
- Modify: `README.md`
- Modify: `tests/test_skill_contract.py`

- [ ] **Step 1: Extend the failing skill contract**

Require `adapter-contract.md`, `adapter-compatibility.md`, and `validate-event-graph`; require explicit language that validation does not authorize a source read or make a native adapter supported.

- [ ] **Step 2: Run the contract test red**

Run: `python3 -m unittest tests.test_skill_contract -v`

Expected: FAIL until public guidance includes the new bounded capability.

- [ ] **Step 3: Update progressive-disclosure guidance**

Route compatibility questions and canonical normalization through the new references and validator. Replace the blanket “source adapters not implemented” statement with the precise boundary: the contract and synthetic conformance harness exist, while all four native adapters remain blocked and unimplemented.

- [ ] **Step 4: Run contract tests green**

Run: `python3 -m unittest tests.test_skill_contract -v`

Expected: PASS.

### Task 5: Verify and review the milestone

**Files:**

- Modify: `docs/plans/2026-09-04-knowledge-distiller-adapter-contract.md`

- [ ] **Step 1: Run the complete suite**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -W error::ResourceWarning -m unittest discover -s tests -v`

Expected: all tests PASS without network access or source reads.

- [ ] **Step 2: Run static and repository checks**

Run: `PYTHONPYCACHEPREFIX=/private/tmp/distil-yourself-pyc-adapters python3 -m compileall -q knowledge-distiller/scripts`

Run: `git diff --check`

Expected: both exit 0 and no repository `__pycache__` directories exist.

- [ ] **Step 3: Review fail-closed behavior**

Review schema strictness, causal invariants, error redaction, file-opening behavior, compatibility claims, and the absence of native reads. Fix every Critical or Important issue with a failing regression test first.

- [ ] **Step 4: Record completion and commit locally**

Mark completed plan items and commit on `feat/implement_knowledge_distiller`. Do not push, merge, install, export, publish, inspect real sources, or change the wider environment.
