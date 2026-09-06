# Knowledge Distiller Core Dual-Source Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use executing-plans, test-driven-development, and requesting-code-review to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver the first usable Knowledge Distiller loop: ingest one explicitly authorized Lark cloud document and one explicitly authorized local Codex CLI session, preserve source provenance through a capability model, ask only blocking questions, and compile one reviewable non-executable skill draft.

**Architecture:** Add a narrow, fail-closed source boundary in front of the existing durable state machine and artifact validator. A typed authorization layer binds an exact source selector and immutable revision/range to the verified user. Read-only brokers acquire bounded native bytes; version-pinned adapters normalize Lark documents and Codex sessions into separate canonical source forms; deterministic redaction emits provenance-addressable spans. The active agent proposes claims and a capability model through strict JSON contracts, and a deterministic compiler renders only confirmed claims into the already-supported safe draft format. Native payloads, grants, and evidence remain private task artifacts; only the compiled draft is eligible for later review. Discovery, sealed evaluation, export, installation, publication, Claude Code, and Trae remain outside this milestone.

**Tech Stack:** Python 3.9+ standard library (`argparse`, `dataclasses`, `hashlib`, `json`, `os`, `pathlib`, `re`, `subprocess`, `unittest`), installed `lark-cli`, local Codex session JSONL, Markdown

---

## Milestone boundary and sequencing

This is a thin vertical slice, not a broad adapter rollout. The acceptance path is:

```text
exact ContentGrant + AuthorityAttestation
  -> Lark document revision + exact Codex session file/range
  -> bounded read-only brokers
  -> canonical document / canonical event graph
  -> deterministic redacted evidence spans + provenance
  -> proposed and adjudicated claims
  -> one capability model
  -> one non-executable draft skill
```

The implementation must not search Lark, enumerate local session directories, follow document links/embeds/attachments, read sibling sessions, use an implicit principal, or fall back to a best-effort schema. Before Tasks 5 and 6 can turn either native adapter from `blocked` to `supported`, the user must provide an exact selector and approve a content read for a redacted compatibility fixture. A generic request to support a source type is not itself an executable ContentGrant for an unspecified resource. The deterministic redaction boundary in Task 4 must be implemented and reviewed before either real fixture is captured.

The first native session target is Codex because it exercises the local CLI-session path with the currently available executable. Claude Code and Trae remain required by the v1 design, but follow this milestone so their adapters can reuse the authorization, broker, redaction, provenance, and conformance code established here.

### Task 1: Define typed authorization and canonical source contracts

**Files:**

- Create: `knowledge-distiller/scripts/knowledge_distiller/authorization.py`
- Create: `knowledge-distiller/scripts/knowledge_distiller/sources.py`
- Create: `tests/test_authorization.py`
- Create: `tests/test_sources.py`
- Modify: `knowledge-distiller/references/authorization.md`

- [x] **Step 1: Write failing authorization-record tests**

  Cover strict closed schemas for `ContentGrant` and `AuthorityAttestation`: immutable record ID, task ID, issuer and active principal, tenant/account, exact selector, allowed operation, purpose, revision or session range, issue/expiry times, derived-processing bound, decision digest, and revocation state. Reject unknown fields, wildcard or empty selectors, `latest`, expired/revoked records, principal/tenant/purpose mismatch, a self-attestation for third-party content, and a grant that attempts discovery or mutation.

- [x] **Step 2: Run authorization tests red**

  Run: `python3 -m unittest tests.test_authorization -v`

  Expected: FAIL because the typed records and validators do not exist.

- [x] **Step 3: Implement the minimal authorization validators**

  Use frozen dataclasses and canonical SHA-256 digests. Return bounded error codes without selectors or source text. Keep grant validation pure; technical credential checks belong to the broker.

- [x] **Step 4: Write failing canonical-source tests**

  Define a shared `SourceSnapshotManifest`, plus two distinct payloads:

  - `CanonicalDocument` with exact revision, ordered blocks, parent/order relations, stable native block locators, content segments, author resolution, and claim eligibility;
  - the existing canonical event graph for sessions, referenced by its validated manifest rather than duplicated.

  Require immutable snapshot IDs, adapter/product/native-schema versions, owner binding, raw/canonical digests, source byte and item counts, and explicit fidelity losses. Reject unknown fields and any document claim eligibility that is not backed by deterministic owner resolution.

- [x] **Step 5: Implement the contracts and run tests green**

  Run: `python3 -m unittest tests.test_authorization tests.test_sources -v`

  Expected: PASS.

### Task 2: Persist private grants, snapshots, evidence, and provenance safely

**Files:**

- Create: `knowledge-distiller/scripts/knowledge_distiller/private_store.py`
- Create: `tests/test_private_store.py`
- Modify: `knowledge-distiller/scripts/knowledge_distiller/persistence.py`
- Modify: `tests/test_persistence.py`

- [x] **Step 1: Write failing private-store transaction tests**

  Cover owner-only directories/files, descriptor-relative access, no symlink/hardlink/special-file acceptance, canonical manifests, immutable generation files, atomic commit, digest verification, crash recovery, idempotent replay, and cleanup of uncommitted raw staging on success, failure, cancellation, and next-start recovery.

- [x] **Step 2: Run the tests red**

  Run: `python3 -m unittest tests.test_private_store tests.test_persistence -v`

  Expected: FAIL because private generation artifacts are not yet supported.

- [x] **Step 3: Add a narrow artifact transaction to the existing coordinator**

  Reuse the existing generation, journal, fencing, fsync, and atomic-pointer mechanisms. Add only declared private paths under the task generation (`grants/`, `sources/`, `evidence/`, `provenance/`, `model/`, `decisions/`, and `draft-skill/`). Keep source text, selectors, and excerpts out of transition facts, logs, diagnostics, and telemetry.

- [x] **Step 4: Run the persistence tests green**

  Run: `python3 -m unittest tests.test_private_store tests.test_persistence -v`

  Expected: PASS.

### Task 3: Extract one reusable bounded-input boundary and implement brokers

**Files:**

- Create: `knowledge-distiller/scripts/knowledge_distiller/source_io.py`
- Create: `knowledge-distiller/scripts/knowledge_distiller/brokers.py`
- Create: `tests/test_source_io.py`
- Create: `tests/test_brokers.py`
- Modify: `knowledge-distiller/scripts/kd.py`
- Modify: `tests/test_cli.py`

- [x] **Step 1: Write failing safe-input refactor tests**

  Move the already-tested regular-file guarantees behind a reusable API without weakening them: explicit path only, component-wise `O_NOFOLLOW`, link count one, sparse/special-file rejection, pre/post `fstat` identity checks, 64 MiB ceiling, moving-file rejection, and guaranteed descriptor cleanup. Keep all current event-graph boundary tests green.

- [x] **Step 2: Refactor the existing event-graph reader**

  Make `kd.py` delegate to `source_io.py`; do not duplicate path-walking and descriptor code for session ingestion.

- [x] **Step 3: Write failing broker-policy tests**

  Lark tests must require exactly one normalized document URL/token, pinned revision, `--as user`, JSON response, an allowlisted `docs +fetch` argv, no shell, no redirects/fallback principal, a cleared credential-leaking environment, timeout, and byte ceiling. Local-session tests must require one exact granted file and byte/range bounds; they must prove no directory enumeration or adjacent-file access occurs. Both brokers must validate the active grant/attestation before touching the source.

- [x] **Step 4: Implement broker command construction and bounded reads**

  Inject a subprocess runner and credential resolver so tests do not call Lark or read real sessions. The Lark broker alone receives the minimal required authentication environment. The parser receives bytes and broker-established trust anchors, never credentials or ambient environment state.

- [x] **Step 5: Run boundary and broker tests green**

  Run: `python3 -m unittest tests.test_source_io tests.test_brokers tests.test_cli -v`

  Expected: PASS without network access or real source reads.

### Task 4: Build the deterministic pre-ingestion redaction boundary

**Files:**

- Create: `knowledge-distiller/scripts/knowledge_distiller/redaction.py`
- Create: `tests/test_redaction.py`

- [x] **Step 1: Write failing deterministic-redaction tests**

  Cover private keys, bearer/session tokens, cookies, credential-shaped assignments, emails, phone numbers, participant names/IDs, overlapping detections, arbitrary chunk boundaries, Unicode, false-positive allowlisting, stable placeholder IDs, and a guarantee that removed values never appear in outputs, errors, logs, manifests, or provenance metadata. Require exact byte/item/depth ceilings before retaining output.

- [x] **Step 2: Run redaction tests red**

  Run: `python3 -m unittest tests.test_redaction -v`

  Expected: FAIL because the redaction module does not exist.

- [x] **Step 3: Implement streaming redaction and provenance spans**

  Emit minimally sufficient redacted spans with immutable IDs and typed derivation edges: redacted span -> native locator digest -> source snapshot -> ingestion run -> active grant/attestation digests. Preserve owner statements separately from non-owner context and mark non-owner content claim-ineligible by default. Never retain removed values in detector state after finalization, diagnostics, or provenance.

- [x] **Step 4: Run redaction and boundary tests green**

  Run: `python3 -m unittest tests.test_redaction tests.test_source_io tests.test_sources -v`

  Expected: PASS without source or network access.

### Task 5: Unblock and implement the version-pinned Lark document adapter

**Files:**

- Create: `knowledge-distiller/scripts/knowledge_distiller/native_adapters.py`
- Create: `tests/test_lark_adapter.py`
- Create after explicit content authorization: `tests/fixtures/adapters/lark/<product-version>/<schema-version>/redacted-*.json`
- Create: `tests/fixtures/adapters/lark/<product-version>/<schema-version>/expected-*.json`
- Modify: `knowledge-distiller/references/adapter-compatibility.md`

- [ ] **Step 1: Resolve the exact-current approval gate**

  Resolve only the user-provided exact Lark URL/token to its current immutable revision, verify the active user principal and content owner/authority, and bind the resulting `ContentGrant`/`AuthorityAttestation`. The current-resolution read is allowed only for that exact resource; the stored fixture must bind the returned numeric revision rather than `latest`. Do not inspect links, embeds, attachments, child documents, comments, or revision history unless separately selected.

- [ ] **Step 2: Capture one minimized redacted compatibility fixture**

  Invoke only the approved `lark-cli docs +fetch` read through the broker and Task 4 redaction boundary. Store no raw source in the repository. Produce a structurally complete fixture with content, personal identifiers, secrets, and tenant-specific locators replaced deterministically while preserving types, IDs/relations, optional-field presence, and schema shape. Record the observed CLI version, response-schema fingerprint, pinned revision, fixture digest, redaction transform version, and loss inventory.

- [ ] **Step 3: Write the parser tests before implementation**

  Cover supported envelopes and block ordering, stable IDs, owner/author resolution, pinned revision and pre/post revision consistency, embeds remaining inert, permission/auth failure, unknown or missing fields, malformed JSON, over-limit payloads, mid-read changes, schema drift, and diagnostic redaction. Expected canonical documents must enumerate every retained block and fidelity loss.

- [ ] **Step 4: Implement only the observed allowlisted tuple**

  Parse the pinned fixture family into `CanonicalDocument`; reject every other product/native-schema tuple. Do not infer authorship or claim eligibility from document availability alone.

- [ ] **Step 5: Run the Lark conformance suite and update readiness precisely**

  Run: `python3 -m unittest tests.test_lark_adapter tests.test_brokers tests.test_sources -v`

  Expected: 100% PASS. Change only the exact tested Lark tuple from `blocked` to `supported`; list all remaining unsupported cases.

### Task 6: Unblock and implement the version-pinned Codex local-session adapter

**Files:**

- Modify: `knowledge-distiller/scripts/knowledge_distiller/native_adapters.py`
- Create: `tests/test_codex_adapter.py`
- Create after explicit content authorization: `tests/fixtures/adapters/codex/<product-version>/<schema-version>/redacted-*.jsonl`
- Create: `tests/fixtures/adapters/codex/<product-version>/<schema-version>/expected-*.json`
- Modify: `knowledge-distiller/references/adapter-compatibility.md`

- [ ] **Step 1: Stop at the real-content approval gate**

  Ask for one exact Codex session file or supported export selector and an immutable prefix/range. Do not use `--last`, pickers, `--all`, session-directory enumeration, sibling sessions, resume, fork, or mutation as a read mechanism.

- [ ] **Step 2: Capture one minimized redacted native fixture**

  Read only the exact granted descriptor through the local broker and Task 4 redaction boundary. Pin product and native-schema fingerprints, byte prefix length, prefix digest, owner/principal binding evidence, and redaction transform. Exclude later appends from the snapshot; reject a changing or truncated prefix.

- [ ] **Step 3: Write complete causality conformance tests**

  Cover messages, tools/chunks/results, edits/retries/supersession, compaction, forks, nested agents, cross-agent delivery, owner attribution, partial final lines, duplicate or missing IDs, concurrent append/reorder/truncation, unsupported records, and unknown/mixed schema versions. Each accepted fixture must validate through the existing canonical event-graph validator.

- [ ] **Step 4: Implement only the observed allowlisted tuple**

  Normalize native JSONL to the existing event graph without fabricating identity, order, or causality. Quarantine the entire root case if a decision-relevant relation cannot be represented.

- [ ] **Step 5: Run conformance and update readiness precisely**

  Run: `python3 -m unittest tests.test_codex_adapter tests.test_adapters tests.test_brokers -v`

  Expected: 100% PASS. Change only the exact tested Codex tuple from `blocked` to `supported`.

### Task 7: Ingest both source kinds with end-to-end provenance

**Files:**

- Create: `knowledge-distiller/scripts/knowledge_distiller/ingestion.py`
- Create: `tests/test_ingestion.py`
- Modify: `knowledge-distiller/scripts/kd.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write failing `ingest-source` CLI tests**

  Add an explicit command that consumes a private source request file, validates authorization, invokes exactly one adapter, persists the normalized/redacted snapshot atomically, and emits only source type, counts, digests, status, and bounded error codes. Cover Lark and Codex success using injected fixtures, plus revoked/expired grants, principal mismatch, schema drift, source change, partial ingestion, and crash recovery.

- [ ] **Step 2: Implement the orchestration and run tests green**

  Run: `python3 -m unittest tests.test_redaction tests.test_ingestion tests.test_cli -v`

  Expected: PASS; no raw source appears in the task journal or CLI output.

### Task 8: Build the core evidence-to-skill loop

**Files:**

- Create: `knowledge-distiller/scripts/knowledge_distiller/knowledge.py`
- Create: `knowledge-distiller/scripts/knowledge_distiller/compiler.py`
- Create: `tests/test_knowledge.py`
- Create: `tests/test_compiler.py`
- Modify: `knowledge-distiller/scripts/kd.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write failing evidence and claim-contract tests**

  Define strict `EvidenceRecord`, `Claim`, `ClaimDecision`, `CapabilityCandidate`, and `CapabilityModel` records. Require every claim to link to one or more redacted spans, distinguish observation/owner-statement/inference, carry support and contradiction links, confidence/freshness/sensitivity, and remain proposed until explicitly adjudicated. A hard invariant may compile only from confirmed claims or an explicit current user decision.

- [ ] **Step 2: Implement deterministic validation and capability scoring**

  Score recurrence, decision impact, evidence coverage, and testability from 0-3, show component scores, and break ties by decision impact then testability. Keep free-form model generation outside the trusted validator: the active agent proposes a strict JSON packet; local code validates IDs, provenance, status, and bounds before persistence.

- [ ] **Step 3: Implement the critical-question queue**

  Emit at most one question when competing rules materially change behavior, a critical branch is blocked, a hard constraint lacks evidence, or new authority is required. Record lower-impact uncertainty without stopping. Require alternatives, evidence IDs, behavioral impact, confidence, and a recommendation only when evidence supports it.

- [ ] **Step 4: Write failing compiler tests**

  Compile one selected capability into `SKILL.md` plus allowlisted one-level references. Test triggers/non-triggers, goals/non-goals, inputs/outputs, invariants, cues, decision rules, adaptive workflow, exceptions, failures/recovery, examples, and dependencies. Prove private excerpts, selectors, participant identifiers, grants, provenance internals, scripts, package manifests, and executable instructions cannot enter the draft.

- [ ] **Step 5: Implement compiler and core CLI commands**

  Add `validate-knowledge-packet`, `next-critical-question`, and `compile-capability`. The compiler must call the existing artifact validator and persist the exact validated bytes and manifest; it must not install or export the draft.

- [ ] **Step 6: Run knowledge, compiler, artifact, and CLI tests green**

  Run: `python3 -m unittest tests.test_knowledge tests.test_compiler tests.test_artifacts tests.test_cli -v`

  Expected: PASS.

### Task 9: Integrate the real core path into the skill guidance

**Files:**

- Modify: `knowledge-distiller/SKILL.md`
- Modify: `knowledge-distiller/references/workflow.md`
- Modify: `knowledge-distiller/references/authorization.md`
- Modify: `README.md`
- Modify: `tests/test_skill_contract.py`

- [ ] **Step 1: Extend contract tests first**

  Require the exact dual-source commands and approval boundaries, one-question policy, provenance requirements, and explicit statements that discovery, Claude Code, Trae, sealed evaluation, approval signatures, export, installation, and publication remain unavailable.

- [ ] **Step 2: Update progressive-disclosure guidance**

  Route source authorization, ingestion, knowledge packets, question adjudication, and compilation to their narrow references/commands. Keep user-facing progress compact and never print redacted evidence unless the user explicitly asks to review a bounded span.

- [ ] **Step 3: Run skill-contract tests green**

  Run: `python3 -m unittest tests.test_skill_contract -v`

  Expected: PASS.

### Task 10: Verify, simplify, independently review, and record completion

**Files:**

- Modify: `docs/plans/2026-09-05-knowledge-distiller-core-dual-source-loop.md`
- Modify: `docs/status/2026-09-05-implementation-status.md`

- [ ] **Step 1: Run the complete suite**

  Run: `PYTHONDONTWRITEBYTECODE=1 python3 -W error::ResourceWarning -m unittest discover -s tests -v`

  Expected: all tests PASS without accessing ungranted sources.

- [ ] **Step 2: Run static and repository checks**

  Run: `PYTHONPYCACHEPREFIX=/private/tmp/distil-yourself-pyc-core-loop python3 -m compileall -q knowledge-distiller/scripts`

  Run: `git diff --check`

  Expected: both exit 0 and no repository `__pycache__` directories exist.

- [ ] **Step 3: Run security and privacy review**

  Review grant/attestation binding, broker argv and environment, exact-selector enforcement, principal/revision/range pinning, source-change handling, schema allowlists, raw-content lifetime, diagnostic/telemetry leakage, provenance completeness, and the absence of implicit traversal or mutation. Fix every Critical or Important issue with a failing regression test first.

- [ ] **Step 4: Run explicit simplification and redundancy review**

  Answer separately: `是否有可以精简的代码，是否存在冗余、重复或无用的代码/测试/文档？` Inspect especially duplicated safe-file logic, repeated schema validators, adapter-specific code that belongs in the broker, dead compatibility branches, redundant fixtures, and abstractions used by only one call site. Remove only demonstrably redundant code; preserve separate trust boundaries where merging would weaken reviewability or safety.

- [ ] **Step 5: Run independent specification and quality reviews**

  Require both reviews to verify the thin vertical acceptance path using redacted fixtures and to report Critical, Important, Minor, and simplification findings independently. Re-run the full suite after every accepted change.

- [ ] **Step 6: Update status and commit locally**

  Record supported exact tuples, fixture coverage, test counts, remaining blocked adapters, deferred milestones, and the simplification verdict. Commit locally on `feat/implement_knowledge_distiller`. Do not push, merge, install, export, publish, or make a wider environment change without separate user approval.

## Acceptance criteria

- One explicitly granted, revision-pinned Lark document can be read through `lark-cli`, normalized, redacted, and persisted without implicit traversal.
- One explicitly granted, immutable Codex session prefix/range can be read from an exact local descriptor, normalized to the existing canonical event graph, redacted, and persisted without directory enumeration.
- Every compiled behavioral rule has a complete derivation chain back to an active grant through a redacted native span and confirmed claim/current user decision.
- The system produces one capability model, asks only a critical question when needed, and compiles a draft that passes the existing closed artifact policy.
- Raw content and source selectors do not appear in transition facts, logs, diagnostics, telemetry, or the generated skill.
- Unsupported versions, ambiguous ownership/authority, moving sources, prohibited fidelity loss, or schema drift quarantine the whole source/root case and do not advance `INGEST`.
- Claude Code and Trae remain visibly blocked and scheduled next; export, installation, publication, sealed evaluation, and signed approval remain unimplemented.

## Deferred next milestones

1. Add Claude Code and Trae native adapters using the same authorized-fixture and conformance gate.
2. Add metadata-only discovery only where the provider can provably suppress snippets/previews.
3. Add isolated evaluation, sealed holdouts, signed `ApprovalSubject`, and `VersionApproval`.
4. Add separately consented export, purge, audit retention, and stale-export repair.
