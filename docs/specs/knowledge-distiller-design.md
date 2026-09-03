# Knowledge Distiller Design

Status: approved for implementation planning
Date: 2026-09-03
Working name: `knowledge-distiller`

## 1. Purpose

`knowledge-distiller` is a private, resumable Skill Factory that turns one person's documents, agent sessions, and explicit judgments into small domain skills. Each generated domain skill represents one repeatable, behaviorally testable capability rather than a broad subject, project, or source collection.

In this specification, `private` means that workspaces and generated artifacts are access-controlled for the owner and are not automatically shared or published. It does not mean local-only inference or zero provider retention; those boundaries must be disclosed for the active runtime before content ingestion.

The primary success criterion is behavioral fidelity: an agent using a generated skill should make decisions in a way the user recognizes as their own, including the user's priorities, hard constraints, diagnostic cues, exceptions, and recovery strategies.

## 2. Non-goals

The first version will not:

- build a general personal knowledge management system;
- create a knowledge graph, vector database, or continuous background indexer;
- silently scan every accessible document or session;
- automatically install, overwrite, publish, or share generated skills;
- contain an installer or mutate an agent's configured skill directories;
- generate or execute scripts inside a domain skill draft;
- support arbitrary agent session formats beyond its named built-in adapters;
- place raw source archives inside generated domain skills;
- turn low-confidence inferences into mandatory rules.

## 3. Research basis

The design combines four established ideas:

- Tacit knowledge must be externalized and refined iteratively rather than captured in a one-shot summary. See Nonaka, *A Dynamic Theory of Organizational Knowledge Creation*: https://doi.org/10.1287/orsc.5.1.14.
- Applied Cognitive Task Analysis uses a task map, knowledge audit, and scenario simulation to expose difficult cognitive work. See Militello and Hutton, *Applied Cognitive Task Analysis*: https://web.mit.edu/16.459/www/Militello98.pdf.
- The Critical Decision Method reconstructs real incidents to uncover cues, goals, alternatives, trade-offs, and counterfactuals. See Klein, Calderwood, and MacGregor: https://doi.org/10.1109/21.31053.
- Agent Skills should use concise metadata, a focused `SKILL.md`, and progressively loaded references or scripts. See the Agent Skills specification: https://agentskills.io/specification.

These imply that the product should be an evidence-grounded, iterative compiler of behavior rather than a document summarizer.

## 4. Confirmed product decisions

- The output is a collection of operational domain skills, not primarily a searchable knowledge base.
- Skills are split by repeatable capability, such as reviewing a backend design or diagnosing an incident.
- Knowledge acquisition combines source analysis with focused elicitation.
- The user supplies a theme or seed material. The system proposes related sources and reads them only under an active ContentGrant.
- Generated behavior uses layered freedom: invariants and critical checks are strict; implementation steps may adapt to context.
- Conflicting evidence is surfaced for user adjudication. Confirmed conclusions become current rules while superseded conclusions remain in history.
- The first version is private and optimized for the owner's use.
- The system first builds a candidate capability map and then distills one selected capability at a time.
- Validation combines historical replay, novel boundary cases, and user review of both conclusions and reasoning.
- The first version includes native session adapters for Codex, Claude Code, and Trae.
- The workflow is asynchronous and checkpointed. It asks only critical questions.
- Generated domain skills and private evidence packs are stored separately.
- Existing skills evolve through proposed, reviewed, regression-tested incremental updates.
- V1 domain drafts contain instructions, references, and static assets only; generated executable code is prohibited.

## 5. System shape

The system exposes one public meta-skill, `knowledge-distiller`. Its internal source adapters are bundled implementation components rather than separate user-visible skills. This avoids trigger collisions while keeping adapters independently testable.

The meta-skill orchestrates four bounded loops.

### 5.1 Capability discovery loop

1. Accept a theme, document link, session identifier, or other seed material.
2. Search for candidate sources under a discovery grant using metadata-only interfaces.
3. Present the proposed content-ingestion grant and explain why each candidate may matter.
4. Read only exact resources covered by an active content-ingestion grant.
5. Produce a deduplicated map of repeatable, testable capabilities.
6. Ask the user to choose one capability.

### 5.2 Knowledge distillation loop

1. Extract evidence related to the selected capability.
2. Model triggers, goals, cues, rules, priorities, procedures, exceptions, and failure modes.
3. Resolve questions from already authorized evidence where possible.
4. Queue only behavior-changing conflicts or blocking gaps for the user.
5. Ask one critical question at a time, provide an evidence-based recommendation when justified, and persist each decision.
6. Repeat until no unresolved issue blocks compilation.

### 5.3 Validation and improvement loop

1. Compile a domain skill draft and evaluation set.
2. Replay historical cases without exposing their original conclusions to the test run.
3. Run novel boundary, counterfactual, and failure cases.
4. Compare against a no-skill baseline.
5. Diagnose behavioral differences and propose a revision.
6. Repeat within a configurable iteration budget.
7. Stop for an explicit VersionApproval; never self-promote a version.

### 5.4 Incremental lifecycle loop

1. Accept newly authorized source material for an existing capability.
2. Identify new, supporting, conflicting, or superseding evidence.
3. Produce a source-linked behavioral diff.
4. Request adjudication only for high-impact changes.
5. Recompile and run the full regression set.
6. Offer the new version for an explicit VersionApproval.

All loops are resumable. Their legal transitions, budgets, and terminal states are defined in section 11. Generic `approved` is not a workflow state: source grants, claim decisions, version approvals, and mutation consent are distinct records.

## 6. Components

### 6.1 Source Scout

The Source Scout accepts seed material and produces a candidate manifest. Before content-ingestion approval it may retain only identifiers, titles, timestamps, project or space names, and source types returned by metadata-only interfaces. Search snippets are content and may not be inspected or persisted under a metadata-only grant. If a source service cannot suppress snippets or body previews, that service cannot be used for pre-approval discovery.

Each candidate includes a relevance explanation derived from authorized metadata and seed context. Source expansion is an approval boundary, even if the underlying account already has technical access. Links, embeds, attachments, child documents, referenced artifacts, directory descendants, and resources created after approval are never traversed implicitly.

### 6.2 Source Adapters

The first version contains four read-only adapters:

- Lark cloud documents through `lark-cli`, using an explicitly verified user identity with no bot or default-principal fallback;
- Codex sessions;
- Claude Code sessions;
- Trae sessions.

Each session adapter converts its native format into a versioned canonical event graph. A session event envelope records:

- schema version, stable event ID, native event ID, native offset, and source snapshot ID;
- root session, session, thread, branch, parent-event, and span identifiers when available;
- event type, actor kind, actor identity, and project or workspace identity;
- native timestamp and a monotonic sequence within each native stream;
- partial-order edges across streams rather than a fabricated global ordering;
- tool-call and tool-result correlation identifiers;
- edit, retry, supersession, fork, sub-agent, and compaction markers;
- typed content segments and privacy-safe call/result summaries;
- referenced artifact locators and observable outcomes;
- an explicit fidelity-loss list for native facts that cannot be represented.

The minimum canonical invariants are normative:

- every event has a stable ID, source snapshot ID, event type, resolved actor kind, root stream ID, native offset, and monotonic position within its native stream;
- the event graph is acyclic, and every reference either resolves inside the snapshot or carries a typed `external` or `missing` marker;
- each tool-output chunk and terminal result correlates to exactly one tool call that is a reachable predecessor in the partial-order graph; chunks have a contiguous order and each call has exactly one terminal result;
- edits, retries, forks, and supersession are explicit edges; a compacted summary represents only the visible summary and never reconstructs hidden events;
- every child-agent root has exactly one `spawned-by` edge from a preceding parent event and a resolved child actor; cross-agent communication is represented as ordered `sent`, `delivered`, and, when observable, `consumed` events rather than collapsed text;
- every child completion, cancellation, or failure precedes the matching parent `joined`, `returned`, or cancellation-observed event; orphaned children and native sources that omit a decision-relevant relation are quarantined;
- a personal-knowledge claim may be attributed to the owner only when the adapter deterministically resolves that event to the verified owner identity.

“Earlier” means graph reachability, never timestamp comparison across streams. Permitted fidelity loss is limited to non-semantic formatting, unavailable token counts, unavailable wall-clock timestamps when monotonic ordering remains intact, and hidden model reasoning that the native source never exposed. Prohibited loss includes ambiguous ownership on a claim-bearing event, omitted user content, missing decision-relevant spawn/message/join/branch/supersession edges, unmatched or multiply terminated decision-relevant tool calls, ID collisions, and graph cycles. A prohibited loss quarantines the entire incident or root case. An adapter version ships only when all required invariants pass on 100% of fixtures whose expected graphs enumerate these relations.

Adapters must detect unsupported versions and fail closed. They must never infer actor identity, causality, or ordering when the native source does not establish it. They must never modify original documents, session files, indexes, or cloud resources.

#### Adapter feasibility and compatibility gate

Before implementation planning assigns estimates to adapter work, a read-only feasibility spike must produce a compatibility matrix for Lark, Codex, Claude Code, and Trae. The spike may inspect only public documentation, synthetic fixtures, or explicitly content-authorized redacted samples.

For each adapter, the matrix records:

- supported product and native schema versions;
- discovery and read interface, including required binaries or APIs;
- principal, account, and tenant binding;
- native snapshot, revision, watermark, and append semantics;
- representation of branches, nested agents, compaction, retries, edits, and tool causality;
- fixture inventory and expected canonical output;
- unsupported or lossy cases;
- authentication, permission, format-change, and source-mutation failures.

An adapter ships only for explicitly listed versions with conformance fixtures. There is no fallback principal or generic “best effort” parser. If any of the four required adapters proves inaccessible or cannot preserve the minimum event semantics, the v1 scope must return to the user for an explicit decision rather than silently dropping the adapter.

### 6.3 Capability Mapper

The Capability Mapper identifies candidate capabilities and merges synonymous candidates. A candidate must have a recognizable trigger, intended outcome, and plausible evaluation scenario. It assigns transparent 0-3 scores for recurrence, decision impact, evidence coverage, and testability. Scores are equally weighted; ties are resolved by decision impact and then testability. The component always shows the component scores, evidence, and tie-break rationale rather than only a total.

Broad domains remain organizational labels. Projects and source collections remain provenance, not skill boundaries.

### 6.4 Knowledge Extractor

The Knowledge Extractor creates evidence records and a capability model. It must distinguish observed behavior, explicit user statements, and model inference. Inference is allowed as a proposal but cannot become a confirmed hard constraint without support or adjudication.

### 6.5 Critical Elicitor

The Critical Elicitor asks a question only when at least one condition holds:

- competing rules would cause materially different behavior;
- a missing decision blocks a critical branch;
- a hard constraint cannot be determined from content-authorized evidence;
- more source access, authentication, or scope is required;
- an export or another separately invoked external mutation requires approval.

Low-impact uncertainty is recorded in a review queue without interrupting progress. Questions are asked one at a time. Each question states alternatives, supporting evidence, behavioral impact, and confidence. It includes a recommended answer only when evidence supports one; value-sensitive or evenly supported choices explicitly state that no evidence-based recommendation is available.

### 6.6 Skill Compiler

The Skill Compiler generates a standards-compatible skill directory from a closed artifact policy. `SKILL.md` must be one UTF-8 regular Markdown file; `references/` may contain only UTF-8 regular `.md`, `.txt`, `.json`, `.yaml`, or `.yml` files at one level; `assets/` may contain only regular PNG, JPEG, or WebP images selected by digest from the reviewed template allowlist shipped with the compiler. Source binaries are never copied into a domain draft. Every path must be relative, normalized, and unique under case folding.

V1 rejects executable bits, scripts, macros, package manifests, archives, HTML, SVG, PDF, Office formats, symlinks, hardlinks, devices, FIFOs, sockets, unexpected extended attributes, extension/MIME mismatches, and instructions that ask the agent to synthesize and run code. Validation opens files without following links, requires link count one, records content digests from the immutable generation, and exports those exact bytes; post-validation substitution fails digest verification.

The compiler outputs a non-executable draft directory and an export manifest only. It does not install, overwrite, package, publish, distribute, or write into any configured agent skill directory. Installation and publication are out of scope for v1 and must be performed by a separately invoked tool after independent consent.

### 6.7 Evaluator and Updater

The Evaluator executes behavioral tests, grades objective assertions, prepares outputs for qualitative user review, and compares the generated skill against a no-skill baseline. The Updater performs evidence-aware diffs and reruns the complete regression set for every proposed version.

## 7. Canonical knowledge model

Each capability model contains:

- **identity**: capability name and concise purpose;
- **triggers**: requests and contexts that should activate it;
- **non-triggers**: adjacent tasks that should not activate it;
- **goals and non-goals**: intended result and explicit boundary;
- **inputs and outputs**: required context and deliverables;
- **invariants**: rules that must always hold;
- **cues**: signals the user notices before making a decision;
- **decision rules**: conditions, choices, priorities, and trade-offs;
- **adaptive workflow**: steps that may change with context;
- **exceptions and counterfactuals**: when normal rules cease to apply;
- **failure modes**: common mistakes, warning signs, and recovery actions;
- **examples**: successful, failed, and boundary cases;
- **dependencies**: tools, permissions, or other skills required.

Each evidence record contains:

- stable evidence identifier;
- source type, native identifier, and locator;
- source timestamp, version, and content digest;
- minimally sufficient original excerpt;
- extracted claim or observation;
- applicability conditions;
- links to supporting and contradicting evidence;
- confidence and freshness assessments with reasons;
- sensitivity classification;
- status: proposed, confirmed, rejected, or superseded;
- adjudication record when applicable.

### 7.1 Provenance and derivation graph

The system assigns immutable IDs to every grant, source snapshot, ingestion run, native span, redaction transform, evidence record, claim, rule, evaluation case, evaluation run, compiler version, generated section, and exported draft. It records typed derivation edges between them.

At minimum, every generated behavioral rule must resolve backward to:

```text
generated section
→ compiled rule and compiler version
→ confirmed claim or explicit user decision
→ redacted evidence span
→ ingestion run and source snapshot
→ active ContentGrant
```

Every evaluation result resolves to the exact draft digest, model identifier, model configuration, system and skill prompt digests, tool versions, dependency lock, test-case version, and run seed where supported. Redaction records name the transform and detector version without retaining removed secret values.

Idempotency keys are hashes of canonical typed inputs rather than mutable paths. A behavioral diff that cannot produce a complete derivation chain is invalid and cannot be offered for version approval.

## 8. Artifact separation and workspace

Each distillation task uses an isolated workspace separate from the generated skill. A conceptual layout is:

```text
knowledge-distiller-workspace/<task-id>/
├── event-log.jsonl
├── current-generation
├── lease
└── generations/<generation-id>/
    ├── manifest.json
    ├── state.yaml
    ├── grants/grants.jsonl
    ├── sources/
    │   ├── content-manifest.yaml
    │   └── snapshots.jsonl
    ├── evidence/evidence.jsonl
    ├── provenance/derivations.jsonl
    ├── model/capability.yaml
    ├── decisions/adjudications.jsonl
    ├── draft-skill/
    │   ├── SKILL.md
    │   ├── references/
    │   └── assets/
    ├── evaluation-manifest.json
    └── reports/validation.md
```

Sealed evaluation material lives under a different root and security principal:

```text
knowledge-distiller-evaluator/<task-id>/<run-epoch>/
├── event-log.jsonl
├── current-generation
└── generations/<generation-id>/
    ├── manifest.json
    ├── partitions/
    │   ├── extraction-manifest.json
    │   ├── validation/
    │   └── sealed-holdout/
    ├── gold/
    └── runs/
```

The exact workspace root is selected at task start and recorded in state. It is not implicitly global.

The evaluator root is accessible only to an isolated evaluator identity and execution context; it is never mounted into the Capability Mapper, Knowledge Extractor, or Skill Compiler. The task workspace stores only evaluation digests, aggregate reports, and opaque case IDs. If the runtime cannot enforce this separation, sealed evaluation is unsupported and version promotion is blocked. Any unauthorized access or cross-partition exposure invalidates both the run and the sealed set, which must be replaced.

The evaluator boundary uses this fixed capability matrix:

| Principal | Extraction data | Validation/holdout/gold | Domain draft | Evaluator result |
|---|---:|---:|---:|---:|
| task mapper/extractor/compiler | read/write | none | write | aggregate envelope only |
| isolation broker | one-pass route | write-only route | none | collision status only |
| evaluator | none | read | read-only by digest | write and authenticate |
| revision agent | read current model | none | read/write proposed revision | declassified aggregate envelope only |
| exporter | none | none | allowlisted read by digest | none |

Validation and sealed-holdout channels are distinct. Validation envelopes may contain declassified aggregate category scores and non-case-specific failure codes for revision. A sealed-holdout run is a one-shot final gate for one frozen draft: its task-visible signed receipt contains only the draft/case/rubric/scorer digests and overall pass/fail predicates required by VersionApproval, never category scores or failure codes. Disclosure of even that pass/fail result retires the sealed set; it can approve the unchanged draft, but any revision or later candidate requires a newly partitioned sealed set.

With fewer than ten independent cases in a reported group, neither channel releases case-level metrics, prompts, gold labels, excerpts, or outputs to an agent. A separate owner-only review UI may reveal sealed cases after final scoring; using any revealed detail to revise the skill retires the set, as does any other change to the draft. The revision agent never receives that UI content.

The private evidence pack is never copied into the generated skill bundle. `SkillExport` uses an exact allowlist of `SKILL.md`, `references/**`, and `assets/**`; it excludes evidence, grants, provenance internals, evaluation cases, gold labels, and reports by construction. The domain skill includes only confirmed guidance and carefully selected examples. It may hold opaque evidence identifiers for local traceability, but it must remain usable without loading the private evidence pack.

## 9. Interaction contract

The skill triggers when the user wants to:

- distill personal experience, documents, or sessions into reusable skills;
- discover repeatable capabilities in their prior work;
- reproduce their decision style in an agent;
- update or validate a previously distilled personal skill;
- resume a paused distillation task.

It should not trigger for ordinary summarization, generic skill creation, session search, or knowledge questions without a personal-knowledge-distillation intent.

The natural-language entry modes are:

- **discover**: create a candidate capability map from a theme or seed;
- **distill**: develop one selected capability;
- **update**: propose an evidence-grounded change to an existing skill;
- **resume**: continue from a saved checkpoint.

User-facing progress remains compact:

```text
Current stage
Completed work
The single required approval or decision, if any
Recommended answer and rationale
Next action
```

When no critical question exists, the system proceeds to the next approval boundary without conversational filler.

## 10. Authorization and privacy

### 10.1 Typed grants and decisions

Technical account access never implies consent to ingest every accessible source. The system uses distinct, auditable record types:

- **DiscoveryGrant**: permits searching a named account, tenant, project, directory, or time range through metadata-only interfaces.
- **MetadataGrant**: permits retaining identifiers, titles, timestamps, source types, and container names for exact selectors.
- **ContentGrant**: permits reading and processing exact resource selectors and pinned revisions or session ranges.
- **AuthorityAttestation**: a verified, issuer-authenticated record that permits processing exact third-party content for this purpose.
- **ClaimDecision**: confirms, rejects, or supersedes a proposed knowledge claim. It is not a source-access grant.
- **BudgetDecision**: extends named stage limits for one suspended epoch; it cannot alter evaluation thresholds or source scope.
- **VersionApproval**: approves a compiled draft as a version eligible for export. It is not mutation consent.
- **MutationConsent**: authorizes one external mutation by a separately invoked tool. It is never inferred from another record.

A MutationConsent is single-use, binds the exact operation, destination, `ApprovalSubject` digest, export manifest, and collision policy, and permits an initial claim for one hour. A trusted user-scoped consent registry atomically changes `unused` to `claimed(transaction-id)` exactly once; only that transaction may recover after claim expiry, and successful verification changes it to `consumed(result-digest)`. No other transaction can reuse or reclaim it. `ApprovalSubject` uses the canonical and authenticated construction in section 13. A VersionApproval binds that exact digest; changing any constituent invalidates approval.

Every grant records its ID, issuing user, active principal, tenant or account, resource selectors, allowed operations, purpose, version or time-range bounds, issue time, expiry, task scope, and revocation state. ContentGrant also records a separate `derived_processing_until` bound. Grants expire at task completion or after 24 hours, whichever occurs first, unless the user explicitly chooses a shorter duration. A persisted task may resume an unexpired grant only after verifying the same principal and tenant. An expired grant must be renewed explicitly for new reads. Revocation immediately stops new processing and triggers the source-removal procedure in section 13.

Seed semantics are explicit:

- Text or files pasted or attached for analysis are content-authorized for the current task.
- A locator alone authorizes metadata resolution only.
- A request such as “distill this document/session” is a ContentGrant for that exact resource and its pinned revision or range.
- No grant includes linked pages, embeds, attachments, child documents, referenced artifacts, sibling sessions, recursive directory contents, or resources created after the grant unless named by its selectors.

Historical approvals found inside a session are evidence about past decisions. They never authorize current source access, version promotion, or external mutation.

### 10.2 Threat model and trust boundaries

Threats include malicious or accidental prompt injection in documents and sessions, secrets in tool output, collaborator data, parser and archive bombs, compromised dependencies, cross-task data mixing, and executable content disguised as a document or static asset.

All source content is inert data. Instructions, commands, links, tool calls, or approval-like language found inside a source are quoted evidence and never control the distillation agent. Adapters must frame source payloads separately from control instructions. The system never follows a source link, executes a source command, or invokes a tool requested by source content.

The active agent model may process redacted, content-authorized material under the data policy of the current product; the skill does not claim local-only inference. Before model exposure, deterministic local preprocessing removes recognized credentials, cookies, tokens, private keys, and unnecessary personal identifiers where technically possible. Raw fetch output must not be printed into the conversation or telemetry before this preprocessing.

The only permitted network egress is the explicitly approved source API and the active model runtime. Private source content must not be sent to web search, unrelated MCP servers, analytics, crash reporting, or other external services.

The normative data flow and trust boundaries are:

```text
[Lark API / local native sessions: untrusted, content-granted]
  → [local read-only adapter: untrusted parser boundary]
  → [local deterministic redaction and schema validation]
  → [isolation broker: root-case assignment]
      ↘ [extraction partition: private task workspace]
          → [distillation model runtime: declared provider boundary]
          → [claims, evidence links, capability model, and compiler]
          → [non-executable domain draft]
          → [closed-policy static checks]
      ↘ [validation/holdout/gold: evaluator-only vault]
          + [read-only domain draft by digest]
          → [isolated evaluator model runtime: declared provider boundary]
          → [authenticated aggregate envelope]
          → [task validation report]
          → [user VersionApproval]
          → [user MutationConsent for exact export destination]
```

No edge may be added implicitly. In particular, source payloads have no direct path to tools, the network, generated-script execution, or exported artifacts without the intervening controls.

Remote fetching occurs in a trusted request broker, never in the untrusted parser. The broker alone holds credentials and enforces an exact read-only method, normalized selector, tenant, pinned revision/range, endpoint, response type, and raw-byte ceiling from the active ContentGrant. It rejects redirects and any response whose resolved identity or revision differs. The parser receives a bounded byte stream over a one-way pipe with no credential or network capability. For local sessions, the trusted broker opens only the exact granted path without following links and passes one read-only descriptor.

Each parser runs in a mandatory least-privilege container with a read-only root, a syscall allowlist that forbids process creation, no inherited environment credentials, no network namespace route, no host or Unix-socket mounts, and no filesystem access except the broker-provided stream or descriptor plus a quota-limited write-only staging volume. Parser dependencies are locked by digest, recorded in an SBOM, and verified before launch.

The request broker pins the approved hostname and TLS identity, revalidates resolved addresses on every connection, and rejects any address class not declared by that adapter's endpoint policy. Local archive handling rejects absolute paths, `..` traversal, links, devices, duplicate normalized paths, and sparse-file expansion. Parsing begins only after MIME, magic-byte, and schema allowlist validation; executables, active documents, macros, polyglots, and unsupported archives are quarantined.

Hard system ceilings are 64 MiB raw bytes per source, 256 MiB raw bytes per task, a 10:1 decompression ratio, archive nesting depth of two, 10,000 archive entries, 1 MiB per canonical event, 10,000 staged files, 512 MiB staging storage, 60 seconds wall time, 60 seconds CPU time, 512 MiB memory, and 64 MiB parser output per source. Task configuration may lower but never raise these ceilings. Parsing and deterministic redaction stream together so raw output is neither logged nor copied to the model boundary. Any quota, time, memory, nesting, schema, network-policy, or expansion violation terminates the container and quarantines that source without partial ingestion or state advancement.

Evaluation of generated skills runs in a restricted environment with:

- no inherited credentials;
- no network unless a test explicitly grants an allowlisted endpoint;
- a temporary, capability-scoped filesystem;
- tool and command allowlists;
- pinned dependencies;
- bounded CPU time, wall time, memory, file size, and output size;
- a read-only copy of the non-executable domain draft plus only the evaluator-assigned cases for that run.

The compiler rejects executable files, scripts, macros, package manifests, and instructions to synthesize or run code. No generated domain code is reviewed or executed in v1 because none is permitted to exist. Prompt-injection and malicious-artifact cases are mandatory negative tests.

### 10.3 Storage, retention, and third-party data

Task and evaluator directories use owner-only permissions (`0700`). Every contained regular file, including grants, snapshots, evidence, models, provenance, decisions, drafts, reports, gold data, and audit records, uses `0600`. The first version does not invent a separate encryption/key-management system; it relies on encryption provided by the selected local storage platform and warns before persisting evidence where that guarantee is unavailable.

Raw source copies are not retained. A `source snapshot` is an immutable metadata record containing the native locator, revision or watermark, digest, byte/event counts, and ingestion time; it is not a full source copy. At task creation the user selects an evidence retention period; the safe default is 30 days after last task activity, with a hard maximum of 90 days unless the user issues a new retention decision. Only an explicit user command or a successfully committed phase transition counts as activity; polling, retries, background checks, and reads do not extend retention. A ContentGrant separately records the latest permitted derived-processing time, which may be shorter. Source-grant expiry stops new reads; already-redacted evidence may be reused only until both retention and derived-processing limits permit it. Expiry of either limit invokes the preauthorized application-owned cleanup policy and emits a deletion report. `SkillExport` never extends retention and never deletes or exports task evidence; private task-archive export is out of scope for v1.

Temporary unredacted files are created only when an adapter cannot stream through the redactor, use owner-only permissions, and are deleted on success, failure, cancellation, and next-start recovery. Logs, checkpoints, diagnostics, and telemetry contain IDs, counts, hashes, and error classes rather than raw content.

Compilation performs a second sensitivity and declassification pass over rules, examples, generated references, and reports. Evidence separation alone is not considered sufficient protection.

The user must have both technical access and organizational authority to process a source for this purpose. AuthorityAttestation is issued only by an allowlisted verifier and records verifier identity and key ID, issuing principal, exact selectors, participant scope, purpose, classification, expiry, named authority basis, underlying immutable decision/evidence digest, and verification time. Accepted verifiers are: source ownership metadata bound to the active identity for user-owned private content; an organization-configured policy/classification resolver for policy authority; or authenticated consent from each affected participant identity. The user cannot self-attest authority over another participant's data.

The verifier checks signature or authenticated-channel provenance, selector and purpose scope, issuer trust, classification, current policy version, expiry, supersession, and revocation before ingestion, resume, evaluation, and export review. Attestations are re-resolved at least every 24 hours and immediately upon a resolver revocation event. If no configured verifier can establish authority, the source is excluded. A policy prohibition always wins; a user affirmation or named policy reference without verifier evidence is insufficient.

Shared documents and sessions may contain third-party personal or confidential data. By default, only the owner's statements, corrections, decisions, and actions are candidates for personal-knowledge claims; other participants' content is minimized and used only as situational context. If participant identity or processing authority is ambiguous, that participant's content cannot support a personal-knowledge claim and the affected source is excluded unless authority is established. Sources carrying a policy or classification that forbids this processing are always excluded.

The system maintains a secret-free audit log of grants, reads, redactions, transformations, decisions, skill exports, and deletions in an owner-only audit root separate from task workspaces. Audit records contain no source excerpts, are retained for 90 days, and include deletion reports. Cleanup removes all application-owned task paths and test caches, then verifies absence from those paths. It cannot guarantee erasure from operating-system backups, filesystem snapshots, model-provider retention, or user-created skill exports; the deletion report names these boundaries and links to the governing platform policies. Installation, overwrite, publication, sharing, and private task-archive export remain outside v1.

## 11. Reliability and recovery

### 11.1 Formal task state machine

A task has a durable `mode` (`discover`, `distill`, or `update`), a monotonically increasing run epoch, a phase, and one active writer. `resume` is an operation on a suspended epoch rather than a fourth mode. An update epoch names one parent approved version. Legal transitions are guard/action records in an append-only event log:

| From | Event and guard | To / action |
|---|---|---|
| `INIT` | `StartDiscover`; task seed exists | `SCOUT` |
| `INIT` | `StartDistill`; selected capability is named | `SOURCE_REVIEW`, or `CAPABILITY_REVIEW` when sufficient authorized snapshots already exist |
| `INIT` | `StartUpdate`; the exact parent was historically approved, its subject and signature validate, its current state is resolved, and any `stale` or `revoked` repair intent is explicit | `SOURCE_REVIEW`, carrying the parent version and lineage head/branch choice |
| `INIT` | `StartRepair`; a verified revocation event names an approved/exported parent and source digest | `AUTH_STALE` in update mode with `repair=true` and the dependency closure |
| `SCOUT` | valid `DiscoveryGrant` and `MetadataGrant`; bounded search completes | `SOURCE_REVIEW` with candidate manifest |
| `SOURCE_REVIEW` | valid `AuthorityAttestation` where required and user issues exact `ContentGrant` | `INGEST` |
| `SOURCE_REVIEW` | user rejects all candidates | `DONE_PARTIAL` with metadata-only report |
| `INGEST` | every selected source is snapshotted or fail-closed excluded | `MAP` for discover mode; otherwise `CAPABILITY_REVIEW` |
| `MAP` | deterministic capability map completes | `CAPABILITY_REVIEW` |
| `CAPABILITY_REVIEW` | user selects or confirms one capability | `EXTRACT` |
| `EXTRACT` | evidence predicate passes with no high-impact conflict | `COMPILE` |
| `EXTRACT` | at least one high-impact conflict exists | `CLAIM_REVIEW` |
| `CLAIM_REVIEW` | all blocking claims receive `ClaimDecision` | `COMPILE` |
| `COMPILE` | compiler predicate passes | `EVALUATE` with frozen draft digest |
| `EVALUATE` | thresholds pass | `VERSION_REVIEW` |
| `EVALUATE` | thresholds fail and revision budget remains | `REVISION_REVIEW` |
| `EVALUATE` | thresholds fail and revision budget is exhausted | `SUSPENDED_EXHAUSTED`, recording the failed report |
| `REVISION_REVIEW` | user accepts a proposed revision | `EXTRACT`, `CLAIM_REVIEW`, `COMPILE`, or `EVALUATE` according to the invalidated artifact set, in a new run epoch |
| `REVISION_REVIEW` | user rejects revision | `DONE_PARTIAL` |
| `VERSION_REVIEW` | valid `VersionApproval` binds the exact `ApprovalSubject` | `EXPORT_REVIEW` |
| `VERSION_REVIEW` | user rejects the version | `REVISION_REVIEW` or `CANCELLED`, as explicitly selected |
| `EXPORT_REVIEW` | single-use `MutationConsent` binds destination and export manifest | `DONE` after verified `SkillExport` |
| `EXPORT_REVIEW` | user defers or denies export | `DONE_APPROVED`; the approved version remains unexported and later export is a separately invoked mutation operation, not a task mode |
| any active phase | user pauses | `SUSPENDED`, recording the prior phase and releasing the lease |
| `SUSPENDED` | user resumes; manifests and digests validate, the same principal/tenant resolves, required next-operation grants and AuthorityAttestations are current, derived-processing and retention bounds permit work, implementation versions are compatible, and the lease is reacquired | recorded prior phase |
| any active phase | stage budget is exhausted | `SUSPENDED_EXHAUSTED`, recording the prior phase and partial coverage |
| `SUSPENDED_EXHAUSTED` | typed `BudgetDecision` extends exact limits and all resume guards pass | recorded prior phase |
| any source-dependent phase | authority required by the next operation is invalid, derived-processing authority expires, or a grant is revoked | `AUTH_STALE`; stop affected work and apply the invalidation rules below |
| `AUTH_STALE` | replacement grants and authority records validate; affected sources are resnapshotted or excluded; any required purge completes | earliest invalidated phase in a new run epoch |
| any nonterminal phase | user cancels | `CANCELLED` |

`DONE`, `DONE_APPROVED`, `DONE_PARTIAL`, `CANCELLED`, and `FAILED_PERMANENT` are terminal. `SUSPENDED`, `SUSPENDED_EXHAUSTED`, and `AUTH_STALE` are resumable and preserve their prior phase. A transient failure stays in its phase with a recorded retry; an unsupported schema, corrupt state log, or nonrecoverable invariant violation enters `FAILED_PERMANENT`. No transition may skip a typed grant, decision, approval, or consent guard.

Expiry of source-read permission stops new reads and resnapshotting but does not enter `AUTH_STALE` when the next operation needs only an already verified redacted snapshot whose derived-processing permission and retention period remain active. Expiry of derived-processing authority or explicit revocation invalidates dependent snapshots, evidence, claims, drafts, evaluation runs, and approval subjects. Revocation after export creates a new repair-update task rather than reopening the terminal task, and marks the exported version `stale`; it becomes `revoked` when the removed source supported a high-impact rule or when remaining evidence no longer satisfies promotion predicates.

Revision routing is computed from the derivation graph: invalidated evidence or model inputs return to `EXTRACT`; claim status changes return to `CLAIM_REVIEW`; draft/compiler/policy changes return to `COMPILE`; evaluation-only invalidation returns to `EVALUATE`. The router records the invalidated node set and chosen earliest phase, and a transition is rejected if recomputing descendants produces a different phase.

The append-only write-ahead event log is authoritative and writable only through a task state coordinator. Every lease acquisition or takeover durably increments a monotonically increasing fencing epoch before work starts. Each worker request, PREPARE/COMMIT record, evaluator receipt, export transaction, and purge transaction binds that epoch; the state coordinator, evaluator, request broker, consent registry, and export/purge broker reject an epoch lower than the current authority epoch. A stale worker therefore cannot commit or perform an external effect even if it resumes with open file descriptors.

Log records are length-prefixed frames containing schema version, sequence number, fencing epoch, previous-record hash, payload digest, and CRC32C. Recovery scans from the start, truncates only a torn final frame to the last verified boundary, and enters `FAILED_PERMANENT` for an interior checksum, sequence, or hash-chain failure. Each transition then uses this protocol:

1. allocate a transaction and generation ID under the current fencing epoch; append and `fsync` a `PREPARE` record containing prior state, input hashes, and expected outputs, including the log's parent directory when the log is first created;
2. write outputs and a content-addressed manifest into an immutable same-filesystem generation directory, validate them, then `fsync` every file and directory including the manifest and generation parent;
3. for evaluator work, require the evaluator to validate the current fencing and authority epochs, durably commit first, and return an authenticated receipt binding both epochs; the task generation stores only that receipt and allowed envelope;
4. append and `fsync` `COMMIT`, binding the generation manifest and any evaluator receipt;
5. atomically replace the `current-generation` pointer as a derived cache and `fsync` its parent directory.

Recovery discards or quarantines a prepared generation with no commit; accepts a committed generation even if the pointer is stale and repairs the pointer from the log; and rejects a pointer with no matching commit. Evaluator receipts are verified before recovery accepts the commit. Orphan evaluator runs are inaccessible and garbage-collected under their original retention policy.

Export uses a separate idempotent protocol because it crosses the task boundary. The export broker atomically claims the MutationConsent for its transaction ID, verifies the current fencing and authority epochs, appends and `fsync`s `EXPORT_INTENT`, stages exact digest-verified bytes beside the destination, rechecks both epochs, atomically renames on a filesystem that supports it, `fsync`s the destination directory, verifies the final manifest, appends `EXPORT_RESULT`, and marks the claim consumed. On recovery, only the same claimed transaction may proceed after consent expiry: a matching destination completes the result record; an absent destination may retry; a mismatched destination stops for new consent. Destinations without these atomic and verification guarantees are unsupported in v1.

A task uses an owner-only lock file with a renewable lease, process identity, and coordinator-issued fencing epoch. A second writer refuses to proceed while the lease is valid. An expired lease may be recovered only after artifact validation and durable epoch increment. Read-only inspection is allowed concurrently.

Stage completion uses recorded predicates rather than model judgment:

| State reached | Required completion predicate |
|---|---|
| `SOURCE_REVIEW` | In discover mode, search and retention are covered by DiscoveryGrant and MetadataGrant, every retained candidate is metadata-only, and every cursor is exhausted or has a recorded budget stop. Direct distill and update instead require exact user-named selectors or existing valid snapshots. |
| `INGEST` | Every selector has a valid `ContentGrant`, required `AuthorityAttestation`, verified principal/tenant, and pinned revision/range. |
| `MAP` / `CAPABILITY_REVIEW` | Every source has a valid snapshot or fail-closed exclusion; every mapped candidate has a trigger, outcome, evaluation scenario, scorecard, and evidence links. |
| `EXTRACT` | Every capability-model field is populated or marked unsupported; every claim has a provisional derivation chain. |
| `COMPILE` | No unresolved high-impact claim exists; lower-impact uncertainty cannot compile into an invariant. The draft passes schema, provenance, sensitivity, and executable-content rejection checks. |
| `VERSION_REVIEW` | Required isolated partitions and all three repetitions completed without contamination and meet section 12 thresholds. |
| `EXPORT_REVIEW` | A VersionApproval references the exact `ApprovalSubject` digest. |
| `DONE` | MutationConsent references the exact destination, allowlisted export manifest, collision policy, and `ApprovalSubject`; post-write digest verification passes. |

A predicate failure either keeps the current phase, enters the applicable resumable or terminal phase, or requests the single typed decision required for progress. It never infers completion from elapsed time or conversational agreement.

### 11.2 Default task budgets

Budgets are visible and configurable when the task starts. Conservative defaults are:

- five metadata search pages and no recursive source traversal;
- 100 candidate metadata records;
- 30 content-authorized resources;
- 50 MiB of redacted source text and 100,000 canonical session events;
- three extraction passes;
- 12 user questions;
- three retries per transient operation with bounded backoff;
- three validation/revision iterations;
- 90 minutes of cumulative active execution time.

These are upper bounds, not work quotas: the system stops earlier as soon as the stage's deterministic exit conditions are met. Reaching a budget produces `SUSPENDED_EXHAUSTED`, saves valid partial outputs, reports coverage and unresolved risks, and offers a typed `BudgetDecision` for exact limits. It never silently truncates a source, lowers a validation threshold, or starts a new loop.

### 11.3 Snapshot and mutation consistency

Source identifiers, grant IDs, versions, and content digests form an idempotency key for ingestion. Lark documents use a pinned revision or ETag when available and verify it before and after reading. A mid-read change discards the staged snapshot and retries within budget or quarantines the source.

Append-only sessions record a native watermark, prefix length, and prefix digest. Events appended after the watermark are excluded until a later incremental run. Reordered, edited, or deleted prefixes invalidate the snapshot and require re-ingestion under the same still-valid ContentGrant.

Unavailable sources are recorded with expected relevance and coverage loss. Unsupported native versions, principal mismatches, ambiguous identity, and missing causality are quarantined with diagnostics rather than guessed. Completed atomic steps are not repeated unless their declared inputs or implementation versions changed.

Unresolved high-impact conflicts block compilation or version approval. Low-confidence knowledge remains advisory and cannot become an invariant.

## 12. Evaluation

Each generated domain skill receives four classes of evaluation.

### 12.1 Trigger evaluation

Use realistic positive and hard-negative prompts to check both activation and non-activation, including near-miss tasks that share vocabulary but require another skill.

### 12.2 Historical replay

Select content-authorized historical holdout cases, hide their original conclusions from the run, and compare the new result with the user's recorded decisions. Grade key conclusions, hard constraints, required checks, and prohibited actions.

### 12.3 Novel boundary evaluation

Create new cases by changing important cues, constraints, trade-offs, or failure conditions. These tests distinguish transferable judgment from memorized examples.

### 12.4 Safety and workflow evaluation

Verify that the skill does not expose secrets, exceed active grants, skip typed decision boundaries, elevate weak evidence into hard rules, or install, overwrite, and publish without consent.

Every evaluation set includes objective assertions where possible and qualitative user review of conclusions and reasoning. Runs using the generated skill are compared with no-skill baselines to show whether the skill adds meaningful behavior.

### 12.5 Contamination control

Content-authorized cases are assigned to extraction, validation, or sealed holdout groups before their content is available to the Capability Mapper, extractor, or compiler. Grouping occurs at the root incident or task level so turns, branches, artifacts, and follow-up sessions from the same case cannot cross partitions.

An isolation broker assigns the full root case by a stable keyed hash of its native root ID before releasing redacted content. It routes extraction content to the task workspace and validation or holdout content directly to the evaluator vault. An evaluator-side duplicate detector may compare all partitions but returns only opaque collision IDs and invalidation status outside the vault; it never returns hidden content or similarity excerpts.

- **Extraction cases** may contribute evidence and examples.
- **Validation cases** guide revisions through the validation envelope but may not be copied into generated examples after their first use without being reclassified and replaced.
- **Sealed holdout cases** and their gold judgments are inaccessible to the extractor and compiler. They are revealed only to the isolated evaluator after the draft digest is frozen, run once as a final gate, and retired immediately after their result is disclosed.

Every evidence span and evaluation case records its partition. Source-level contamination, semantic duplicates, and derived synthetic cases are tracked. If too few independent cases exist, the report states that generalization is unvalidated rather than recycling extraction evidence as holdout data.

Gold decisions and scoring rubrics are frozen before evaluation results are shown. A post-result gold change requires a recorded ClaimDecision, a new evaluation-set version, and a fresh run. Any cross-partition access, semantic duplicate missed across partitions, disclosure of sealed content to a mapper, extractor, compiler, or revision agent, or attempt to reuse a retired set invalidates every affected run; promotion waits for a replacement sealed set.

### 12.6 Reproducibility and promotion thresholds

Every run records the model, model configuration, prompts, tools, adapter/compiler/evaluator versions, dependency lock, source snapshot set, draft digest, evaluation-set digest, scorer versions, and seeds where supported. Every behavioral and trigger case runs exactly three times within its single evaluation transaction using the same pinned configuration. If the runtime cannot perform or record all three repetitions, promotion is blocked rather than weakened.

The default minimum evaluation set contains:

- ten should-trigger and ten hard-negative trigger prompts;
- three independent sealed historical holdout cases;
- three novel boundary or counterfactual cases;
- the complete negative-path safety matrix from section 15.

Each frozen rubric assertion records an ID, category, within-category weight, scorer type and version, expected outcome, and the observed evidence required to score it. Assertion scorers return a value in `[0,1]`; Boolean assertions map fail/pass to `0/1`. Conditional `N/A` is permitted only when its condition and redistribution rule were frozen before the run. A post-run `N/A` is a failure. Within-category weights must sum to one after predetermined redistribution, and every required category must contain at least one applicable assertion.

For each run, a category score is the weighted mean of its assertions. The behavioral run score is 40% decision fidelity, 30% invariant and prohibition adherence, 20% cue and rationale fidelity, and 10% output-contract adherence. A case score is the arithmetic mean of its three run scores; the behavioral aggregate is the arithmetic mean across equally weighted independent holdout and boundary cases. Threshold comparisons use unrounded values; displayed values round to one decimal place. User-designated high-impact assertions are evaluated separately and must all pass.

Objective assertions use deterministic scorers. Any model grader is pinned to an exact model and configuration, blinded to the treatment label and prior scores, and produces evidence-linked structured output. Qualitative user review is recorded separately and is never converted into a numeric score.

Skill and no-skill arms are paired on identical cases and configurations. A timeout, missing output, scorer error, or absent repetition counts as a failed run. The no-skill baseline aggregate uses the same behavioral formula and exact case set. Trigger precision and recall treat each of the three prompt repetitions as a separate prediction and pool the resulting confusion counts; an absent prediction is incorrect. V1 makes no statistical significance claim: its baseline comparison is an explicit practical margin on this frozen set. A runtime that cannot control a seed records `seed: null`; repetition measures stability but is not described as bitwise reproducibility.

Promotion requires:

- 100% pass rate for safety, authorization, invariant, and prohibited-action assertions across all repeated runs;
- at least 90% trigger recall and 90% trigger precision across the trigger set;
- 100% agreement on user-designated high-impact decisions in sealed historical and boundary cases;
- at least 80% of the frozen weighted behavioral rubric on every holdout and boundary case;
- when the no-skill baseline is below 90%, an aggregate behavioral improvement of at least ten percentage points; when the baseline is at least 90%, a result within two points of baseline plus a documented capability-specific benefit;
- explicit qualitative approval by the user after reviewing conclusions and reasoning.

Thresholds may be made stricter for a capability. Lowering them requires a new design decision and cannot occur automatically when a run fails. Fewer than the minimum independent cases, missing gold, failed repetitions, or unavailable isolation blocks VersionApproval; the report may describe exploratory results but cannot call them validated.

## 13. Promotion and update gates

A domain skill version may be approved for export only when:

- all safety and invariant assertions pass;
- no high-impact conflict remains unresolved;
- historical replay reaches user-accepted fidelity;
- novel cases expose no known systematic misjudgment;
- the user explicitly approves the version.

Every capability has an immutable capability ID. Each draft has a semantic version, parent version, draft digest, compiler version, evidence snapshot set, evaluation-set version, and version state. Version states are `draft`, `evaluated`, `version-approved`, `exported`, `stale`, `superseded`, and `revoked`. The record may note an externally installed baseline, but v1 does not perform or verify installation.

VersionApproval binds the complete `ApprovalSubject`, not an independently mutable version label. Its v1 digest is `SHA-256("knowledge-distiller/approval-subject/v1\0" || JCS(payload))`, where JCS is RFC 8785 canonical JSON and arrays of set-like IDs are bytewise sorted. The payload directly includes the draft and export-manifest digests, provenance root, source-snapshot set, ContentGrant digests whose derived-processing bounds remain valid, current AuthorityAttestation decision digests, evaluator receipt and signer key ID, partition/evaluation-set/rubric/gold/scorer digests, compiler/evaluator versions and configurations, and schema version. The evaluator receipt is Ed25519-signed by an allowlisted evaluator key over the same evaluation-related fields and draft digest.

Key rotation preserves an authenticated trust chain and does not alter an existing subject. A hash, canonicalization, schema, or signature migration creates a new subject version and requires re-evaluation and fresh approval; mixed-version subjects are rejected. Recompilation, provenance repair, changed authority, source snapshots, repartitioning, rescoring, evaluator changes, or configuration changes likewise require a fresh subject and approval.

Rules have applicability and effective periods. When a source grant is revoked or a source is deleted, every derived claim and rule is re-evaluated. Unsupported rules are demoted or invalidated, affected evaluations are marked stale, and version approval is blocked until the impact is resolved. Rollback creates a new draft whose parent points to the current version and whose content matches a previously approved digest; history is never rewritten.

### 13.1 Source-removal and purge protocol

The provenance graph maintains a reverse index from every source snapshot to all redacted spans, claims, rules, drafts, reports, evaluator cases/runs, and immutable generations derived from it. A trusted revocation coordinator first commits the revoked source digest to a central deny registry and increments its authority epoch. Task, evaluator, request, export, and purge brokers validate that epoch before every read and commit. The evaluator immediately rejects new access, cancels or fences in-flight runs from older epochs, and makes their receipts invalid. Only after this freeze may data-plane cleanup begin.

For an active task, the coordinator acquires a newly fenced lease and enters `AUTH_STALE`. For an approved or exported terminal task, it creates a new update-mode repair task through the `StartRepair` transition; it never reopens terminal state. The repair task writes a durable `PURGE_PREPARE` containing the dependency closure before any deletion.

The task then builds and commits a clean replacement generation that omits affected source metadata and every derived node, invalidates approvals and evaluations, and marks affected exported versions `stale` or `revoked`. After the clean generation is authoritative, it deletes superseded task generations in the closure. It sends the evaluator an authenticated source-digest `PurgeRequest` binding the current authority and fencing epochs; the evaluator deletes affected cases, gold, runs, and caches under its own write-ahead transaction and returns a signed purge receipt. The task records that receipt, verifies all application-owned paths, and commits `PURGE_RESULT`. Recovery resumes from the durable prepare/result pair, making each deletion idempotent; no old-epoch receipt can become valid again. Secret-free audit IDs and purge receipts remain for 90 days, but locators, excerpts, case content, and derived guidance do not.

The append-only event and version logs retain content-free tombstones for purged nodes so audit order and version lineage remain verifiable without preserving the revoked material. “History is never rewritten” applies to these decision records and tombstones, not to source-derived content that retention or revocation requires the system to erase.

An exported copy is outside the private workspace and cannot be silently mutated. The version registry records its exact destination as affected and tells the user what remains there. Repair or deletion at that destination requires a new single-use MutationConsent and the export transaction protocol; if consent is absent, the copy remains visibly `stale` or `revoked` and the purge report names that boundary. Backups and provider retention remain subject to the deletion caveats in section 10.3.

Impact is high when a change affects an invariant, prohibited action, source or tool authorization, external side effect, security classification, required deliverable, promotion outcome, or a decision labeled high-impact by the user. Other changes are medium or low and remain visible without necessarily blocking compilation.

Every update produces a complete provenance-linked behavioral diff and reruns the full non-sealed regression set, then uses a fresh sealed set for its one-shot final gate. Passing tests authorize neither export nor installation. Export writes only to the exact user-approved destination after previewing the artifact list and collision behavior. Installation, replacement, rollback of an installed skill, and publication require separate tooling and fresh MutationConsent.

## 14. Proposed skill package

The implementation should keep the public instructions compact and progressively disclose details:

```text
knowledge-distiller/
├── SKILL.md
├── references/
│   ├── workflow.md
│   ├── knowledge-model.md
│   ├── question-policy.md
│   ├── adapter-contract.md
│   ├── adapter-compatibility.md
│   ├── authorization.md
│   ├── threat-model.md
│   ├── provenance.md
│   ├── evaluation.md
│   └── templates.md
├── scripts/
│   ├── state-management utility
│   ├── source normalization utilities
│   ├── evidence validation utility
│   ├── redaction scanner
│   └── skill compilation validator
└── assets/
    └── generated-skill templates
```

Exact script languages and native session paths are selected only after the adapter feasibility gate. The compatibility matrix, supported native versions, synthetic or authorized fixtures, and loss behavior are design inputs to the implementation plan rather than decisions deferred to individual implementation tasks.

The `scripts/` directory above contains reviewed utilities authored as part of the `knowledge-distiller` meta-skill implementation. It is not produced from personal evidence and is never copied into a generated domain skill. Domain drafts remain limited to `SKILL.md`, one-level references, and static assets.

## 15. Acceptance criteria for the meta-skill

The meta-skill itself is complete when it can demonstrate an end-to-end task that:

1. starts from a user-provided theme or seed;
2. distinguishes locator, metadata, preview, and content authority and refuses every ungranted dereference;
3. passes the compatibility gate and normalizes at least one supported Lark document and sessions from Codex, Claude Code, and Trae without losing required causality;
4. generates a candidate capability map;
5. distills one selected capability while asking only critical questions;
6. records and adjudicates a deliberately conflicting rule;
7. emits a separate domain skill draft and evidence pack while keeping the evaluation set in an isolated evaluator root;
8. passes sealed historical holdout, boundary, trigger, and safety evaluations at the thresholds in section 12;
9. pauses and resumes from a checkpoint without duplicating evidence;
10. exports a draft only after approval and does not perform installation or publication;
11. produces a complete derivation chain from each generated rule and test result back to its grants and immutable source snapshot;
12. deletes every task-workspace path, evaluator-vault path, and task test cache, verifies their absence, retains only the separate secret-free 90-day audit record, and reports backup/provider/export erasure boundaries;
13. purges one revoked source and its complete derivation closure across active generations and evaluator storage, while marking an external exported copy stale until separately authorized repair.

### 15.1 Mandatory negative-path conformance matrix

Acceptance includes tests that prove the system fails closed for:

- a locator supplied without content-reading intent;
- discovery services that return unavoidable content snippets;
- unapproved links, embeds, attachments, child documents, sibling sessions, and recursive descendants;
- expired or revoked grants, principal or tenant mismatch, and forbidden data classification;
- a Lark document that changes during ingestion;
- appended, reordered, edited, duplicated, or deleted session events;
- nested agents, forks, retries, compaction, and missing tool-call/result correlation;
- unsupported source schema and canonical-schema versions;
- ambiguous, forged, stale, superseded, revoked, issuer-untrusted, purpose-mismatched, or selector-mismatched AuthorityAttestation, plus participant identity that cannot be resolved to the owner;
- oversized raw input, output or staging volume; excessive decompression; archive depth, entry count, path traversal, link or sparse-file attacks; parser timeout, CPU or memory exhaustion; MIME/magic-byte mismatch; polyglots, macros, executable payloads, redirects, DNS rebinding, undeclared endpoints, inherited credentials, forbidden mounts/syscalls, and dependency-digest mismatch;
- prompt injection, approval-like text, and malicious commands inside source content;
- secrets and personal identifiers in text, tool output, artifacts, compiled guidance, and examples;
- any generated executable, script, macro, package manifest, archive, active document, symlink, hardlink, special file, executable bit, unapproved extension/MIME pair, path collision/traversal, post-validation substitution, or instruction to synthesize and run code;
- attempted access to sealed cases or gold data by the mapper, extractor, compiler, or revision agent; reuse of a disclosed sealed set; adaptive probing through sealed category/failure feedback; and attempts to include evaluator material in `SkillExport`;
- crashes before and after PREPARE, generation flush, evaluator receipt, COMMIT, pointer replacement, EXPORT_INTENT, destination rename, EXPORT_RESULT, PURGE_PREPARE, evaluator purge receipt, and PURGE_RESULT;
- concurrent writers, stale writers after lease takeover, torn final log frames, interior log corruption, fencing-epoch mismatch, and expired-lease recovery;
- pause/resume, revision back-edges, update epochs, grant expiry or revocation, and exhaustion/extension of every stage-specific budget;
- evaluation contamination, changed gold labels, model/version drift, and nondeterministic disagreement;
- malformed or unsigned evaluator envelopes, signer rotation, ApprovalSubject ordering/canonicalization/hash-version mismatch, and stale authority digests;
- source revocation during an evaluator run and affecting active, immutable-generation, evaluator, approved, exported, and backup-boundary artifacts;
- request-broker attempts to widen HTTP method, selector, tenant, revision, endpoint, response type, or byte ceiling;
- two export transactions racing to claim one MutationConsent and same-transaction recovery after its initial claim window expires;
- denied export approval and destination collisions.

Every case asserts both the visible error and the absence of unauthorized reads, writes, network calls, state advancement, and sensitive persisted output.
