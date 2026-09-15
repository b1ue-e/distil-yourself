# Adapter compatibility gate

Retrieved: 2026-09-07

This matrix separates evidence that a product can expose history from evidence that
Knowledge Distiller has a stable, versioned parse contract. Product capability is
not adapter compatibility. One narrow Lark raw-content normalizer and one Codex
rollout adapter are supported; dependency-injected ingestion can atomically retain
both sources in one task. A standalone synthetic Lark runtime is implemented, but
its checked-in profile stops every production call before executable or network
access; general production source runtimes remain unavailable.
Supported native versions: Lark / 1.0.0 / 1.0.86 / docx-v1-raw-content-v1 (normalizer only); Codex / 1.0.0 / 0.153.0 / rollout-jsonl-v1.
All other native tuples remain disabled until an exact version passes the required
conformance fixtures.

For standalone Lark runtime authorization, acquisition, and readiness, the
[runtime design](../../docs/specs/2026-09-11-local-lark-runtime-design.md) is the
canonical and single authority. This file summarizes implementation evidence for
the pinned tuple, control schemas, normalizer, and transport only.

Historical note: the `historical normalizer-only probe` used one separately
authorized current document. Its bounded in-memory result established
the raw normalizer fixture shape only. It did not establish the standalone
runtime's current control-response contracts, endpoint integrity, owner protocol,
or consistency mode, and it does not authorize a new probe or real read. The
`standalone-runtime control-contract probe` has not run; it requires separate
explicit approval for one exact document.

The user separately authorized a read-only compatibility probe over all local
Codex active and archived session roots. The probe observed 83 regular JSONL
files and 47,658 valid records without emitting content or locators. Repository
fixtures retain no observed content, path, session ID, or tenant locator; they
are fully synthetic. Of 48 complete files whose first session record pins
`0.153.0`, none satisfy the final fail-closed adapter: all contain at least one
unsupported causal or content projection. A bounded probe over exact,
newline-closed prefixes found one supported root prefix of 9 records producing
one canonical event. The 35 files from older versions remain blocked. These are
point-in-time aggregate observations; live session roots may change.

| Adapter | Readiness | Locally observed client | Product capability evidence | Stable parse-contract evidence |
| --- | --- | --- | --- | --- |
| Lark | `normalizer-supported` | `lark-cli 1.0.86` | Official Docx GET APIs expose current revision and raw text without fetching comment content. | Exact CLI envelope `ok/identity/data.content`, synthetic control schemas, observational sandwich, fake-executable fixture, and redaction/normalization tests pass for `docx-v1-raw-content-v1`; the checked-in production profile is `synthetic-only`. |
| Codex | `supported` | `codex-cli 0.153.0` | Official source defines persisted rollout lines and session roots; the CLI can resume/fork sessions. | Exact local closed-prefix broker, `rollout-jsonl-v1` synthetic fixture, redaction/normalization tests, and one authorized aggregate closed-prefix replay pass; complete files containing unsupported projections fail closed. |
| Claude Code | `blocked` | Executable unavailable through the local shim. | Official docs describe local JSONL transcripts and SDK session/message reads. | Public docs do not freeze every native JSONL record needed for the canonical causality contract. |
| Trae | `blocked` | `traecli 0.202.3(internal edition)` | TraeCode CLI help exposes resume by UUID/thread name and fork by UUID. | Neither command establishes a read/export schema; Trae Agent trajectory JSON is a different product boundary. |

`normalizer-supported` means only that already-authorized, already-redacted bytes
for the exact tuple may enter the pure normalizer. It does not authorize a read,
provide a production credential runner, or make the standalone CLI a source reader.
`supported` means the exact tuple has a read-only broker and pure normalizer that
pass conformance and may enter dependency-injected ingestion. It does not provide
a production runtime, install a skill, or authorize any future read.
`blocked` means no product/native schema tuple may be accepted by that adapter.

## Lark

- **Official evidence:** [get document metadata](https://open.feishu.cn/document/server-docs/docs/docs/docx-v1/document/get) and [get raw content](https://open.feishu.cn/document/server-docs/docs/docs/docx-v1/document/raw_content) specify read-only Docx v1 GET endpoints and `docx:document:readonly`. Installed `lark-cli 1.0.86` exposes them through `api GET` with explicit `--as user`.
- **Discovery/read interface:** automatic discovery and Wiki resolution are
  unavailable. Wiki URLs are unsupported. The closed request accepts only one
  exact Docx token or canonical Docx URL and never traverses links, redirects,
  shortcuts, embeds, attachments, child nodes, comments, blocks, media, or
  history. `docs +fetch` is excluded because it may broaden the body-only read.
- **Principal binding:** `lark-cli auth status --json --verify` must establish a
  verified user `openId`; exact Drive batch metadata must return one `docx` row
  whose `owner_id` equals that `openId`. Bot, automatic/fallback identity,
  collaborator/editor authority, creator inference, and shared-document authority
  fail closed. Raw IDs exist only for the in-memory comparison; opaque
  domain-separated digests persist.
- **Revision/append semantics:** one typed `LarkObservation` holds the exact token,
  Docx revision, and owner. The runtime observes before, reverifies the same
  `openId`, reads raw content, observes after, and requires exact typed equality.
  This observational sandwich proves only that no owner or revision change was
  observed. It is not an atomic or cryptographic snapshot, does not bind bytes to
  the revision, and does not prove replica consistency or later freshness.
- **Missing guarantee:** the raw-content endpoint exposes no native block graph or
  per-block author. The v1 normalizer therefore emits one synthetic block,
  records non-semantic formatting loss, and marks it unresolved and
  claim-ineligible. Actual CLI response compatibility and server-side consistency
  remain unconfirmed because the required approved exact-document probe has not
  run; this runtime is not real-read ready.
- **Fixtures required to unblock:** the exact normalizer tuple, four closed control
  schemas, and end-to-end runtime use only synthetic responses and an owner-owned
  fake native executable. Live activation still requires endpoint-integrity
  evidence, an accepted consistency mode, and one explicitly approved
  exact-document probe. Rich block schemas, other CLI versions/envelopes,
  comments, history, Wiki resolution, and implicit traversal remain blocked.
- **Failure behavior:** the checked-in `synthetic-only` profile returns
  `lark-live-disabled` before executable or network access even with
  `--allow-live-read`. If a future reviewed profile is activated, absent or changed
  binary, non-user/ambiguous identity, scope or owner mismatch, revision change,
  unknown/missing/duplicate responses, malformed JSON, overflow, timeout,
  redaction failure, or mutation path aborts the whole operation. There is no
  retry, fallback principal, selector broadening, partial commit, or content echo.

### Pinned synthetic runtime profile

The one profile is `lark / adapter 1.0.0 / lark-cli 1.0.86 /
docx-v1-raw-content-v1`. Its canonical record pins four closed synthetic control
schema digests, the raw schema digest, exact read/metadata scope alternatives,
30-second per-call timeout, 1 MiB control stdout, 64 MiB raw-envelope stdout,
64 KiB stderr, `endpoint_integrity.status=unverified`,
`consistency.mode=unaccepted`, and `status=synthetic-only`. Actual CLI response
compatibility has not been confirmed by the required approved exact-document
probe, so synthetic conformance must not be described as live readiness.

The transport executes only a previously installed native binary. It may inspect
the packaged Node launcher path solely to locate an existing `bin/lark-cli`; it
never executes the wrapper or downloads/updates anything. It pins the native
path and file fingerprint, uses fixed argv with `shell=False`, inherits only the
minimal credential environment plus notifier suppression, rejects proxy/base-URL
overrides, bounds stdout/stderr while streaming, kills and reaps on timeout or
overflow, performs no retry, and never switches principal. See the workflow for
the request and sequence; see authorization for the owner evidence.

## Codex

- **Official evidence:** [Codex CLI reference](https://learn.chatgpt.com/docs/developer-commands?surface=cli), [version-pinned rollout line and item definitions](https://github.com/openai/codex/blob/rust-v0.153.0/codex-rs/history/src/lib.rs), and [version-pinned response item definitions](https://github.com/openai/codex/blob/rust-v0.153.0/codex-rs/protocol/src/models.rs).
- **Discovery/read interface:** official source names `sessions` and
  `archived_sessions`, but production acquisition does not use resume, pickers,
  or directory scanning. The existing local broker opens one exact granted
  regular-file descriptor and confirms one immutable prefix twice. The user's
  broader all-session approval was used only for this bounded compatibility
  inventory; a future collection command must still create one exact grant and
  snapshot per file.
- **Principal binding:** `SessionIdentity`, `ContentGrant`, and
  `AuthorityAttestation` are external trust anchors. The parser never infers the
  owner from paths, session metadata, or message text. Only a native user message
  item classified `user.text` becomes a verified-owner claim-bearing event.
- **Revision/append semantics:** `0.153.0` records carry ordinals. The adapter
  requires one complete newline-terminated prefix, exactly one leading
  `session_meta`, and ordinals contiguous from zero. The broker excludes later
  appends and rejects prefix edits, reorder, truncation, links, and file changes.
- **Missing guarantee:** forked histories, rollback/edit/retry/supersession, and
  nested-agent lifecycle records are quarantined because their decision-relevant
  parent edges cannot be proved from one file. Non-text tool outputs and extracted
  text above the deterministic redactor's 1 MiB task limit are also rejected.
  `ItemCompleted` and unclassified developer context are rejected rather than
  presumed redundant. Versions other than `0.153.0` remain blocked.
- **Fixtures required to unblock:** the checked-in fixture is fully synthetic but
  preserves the observed outer line, session metadata, message metadata,
  function-call/result, reasoning, compaction, ordinal, and decimal shapes.
  Tests cover owner attribution, JSON-escape-before-redaction, tool pairing,
  cross-agent quarantine, partial lines, duplicate/missing IDs, reorder,
  concurrent-prefix protection, unknown/mixed schemas, fork, rollback, nested
  agents, encrypted content loss, resource ceilings, and source snapshot binding.
- **Failure behavior:** reject the entire session with a bounded code on unknown
  fields/records, noncontiguous ordinals, unsupported versions/content/causality,
  unpaired tools or communications, invalid redaction provenance, or source
  change. Never echo source values, resume/fork a session, broaden a selector,
  infer identity, or emit a best-effort partial graph.

## Claude Code

- **Official evidence:** [Claude Code sessions](https://code.claude.com/docs/en/sessions)
  and the SDK session APIs linked from that official documentation.
- **Discovery/read interface:** docs describe JSONL at
  `~/.claude/projects/<project>/<session-id>.jsonl`, with message, tool-use, and
  metadata records; SDKs expose session enumeration and message reads. This is
  capability evidence only and does not authorize filesystem enumeration/read.
- **Principal binding:** a local project/session path is not proof that an event
  belongs to the verified owner. The public contract does not establish the
  deterministic owner identity mapping required for claim-bearing events.
- **Revision/append semantics:** Resume appends to the selected session; fork
  creates a new session that copies the selected history. Sessions are subject to
  default 30-day cleanup. No immutable read snapshot or complete versioned
  fork/causality schema was established.
- **Missing guarantee:** the public docs do not freeze every JSONL variant or all
  relations required for tools, compaction, branches, nested agents, and messages.
- **Fixtures required to unblock:** version-pinned synthetic JSONL covering every
  documented record variant and canonical relation, concurrent append, cleanup,
  partial final lines, schema drift, tool terminality, compaction honesty, nested
  agents, cross-agent messaging, fork/edit/retry/supersession, and owner binding.
- **Failure behavior:** fail closed on unavailable executable/API, unsupported or
  mixed record versions, partial/changing files, expired/missing sessions,
  unresolved ownership, missing causal relations, or any directory scan not
  separately authorized. Do not repair, rewrite, or lock native files/indexes.

## Trae

- **Official evidence:** local TraeCode CLI `--version`, `resume --help`, and
  `fork --help` output only. The observed version is
  `traecli 0.202.3(internal edition)`. The executable is `trae-cli`.
  `traecli` without the hyphen is an unrelated Coco executable and was not used
  as evidence.
- **Discovery/read interface:** `resume --help` accepts a session UUID or thread
  name and `fork --help` accepts a UUID. Neither command establishes a transcript
  read API or machine-readable export schema.
- **Principal binding:** no documented export field binds records to a verified
  owner/account/workspace principal.
- **Revision/append semantics:** resume and fork are navigation/product
  capabilities; no versioned snapshot, watermark, append, export, or branch-edge
  schema was established.
- **Missing guarantee:** there is no supported TraeCode CLI native schema/version,
  causal completeness guarantee, or safe discovery API. Open-source Trae Agent
  trajectory JSON belongs to a different product and is not TraeCode evidence.
- **Fixtures required to unblock:** an official version-pinned schema plus
  synthetic exports for history, forks, tools, edits/retries/supersession,
  compaction, nested agents, message delivery, owner binding, concurrent changes,
  auth/permission failures, schema drift, and source-mutation attempts.
- **Failure behavior:** fail closed when the official reader/schema is absent,
  identity or causality is ambiguous, versions are unknown/mixed, or export
  changes during capture. Never substitute Trae Agent output for TraeCode session
  data.

## Shipping gate

An adapter may move from `blocked` only when an explicitly listed product version
and native schema version have a read-only, principal-bound snapshot interface and
pass 100% of their synthetic and explicitly authorized redacted fixtures. Expected
graphs must enumerate all decision-relevant content and causal relations. Any
authentication, permission, format-change, unsupported-version, content-loss, or
source-mutation condition rejects and quarantines the entire source/root case;
there is no best-effort parser, fallback principal, partial ingestion, or content
in diagnostics.
