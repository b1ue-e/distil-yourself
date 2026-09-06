# Adapter compatibility gate

Retrieved: 2026-09-06

This matrix separates evidence that a product can expose history from evidence that
Knowledge Distiller has a stable, versioned parse contract. Product capability is
not adapter compatibility. One narrow Lark raw-content normalizer is supported;
trusted ingestion and every session adapter remain unavailable.
Supported native versions: Lark / 1.0.0 / 1.0.86 / docx-v1-raw-content-v1 (normalizer only).
All other native reads and parsers remain disabled until an exact version passes
the required conformance fixtures.

The Lark row includes one explicitly authorized current-document compatibility
probe. Raw content flowed directly into the deterministic redactor; only bounded
schema fields, counts, and digests were emitted, and no observed content or
tenant locator was retained in repository fixtures. No session data or indexes
were inspected.

| Adapter | Readiness | Locally observed client | Product capability evidence | Stable parse-contract evidence |
| --- | --- | --- | --- | --- |
| Lark | `normalizer-supported` | `lark-cli 1.0.86` | Official Docx GET APIs expose current revision and raw text without fetching comment content. | Exact CLI envelope `ok/identity/data.content`, revision sandwich, synthetic fixture, and redaction/normalization tests pass for `docx-v1-raw-content-v1`; trusted ingestion remains blocked. |
| Codex | `blocked` | `codex-cli 0.153.0` | Sessions can be resumed or forked; a running `codex exec --json` emits NDJSON events. | The cited interface does not specify a stable export API/schema for stored transcripts. |
| Claude Code | `blocked` | Executable unavailable through the local shim. | Official docs describe local JSONL transcripts and SDK session/message reads. | Public docs do not freeze every native JSONL record needed for the canonical causality contract. |
| Trae | `blocked` | `traecli 0.202.3(internal edition)` | TraeCode CLI help exposes resume by UUID/thread name and fork by UUID. | Neither command establishes a read/export schema; Trae Agent trajectory JSON is a different product boundary. |

`normalizer-supported` means only that already-authorized, already-redacted bytes
for the exact tuple may enter the pure normalizer. It does not authorize a read,
provide a production credential runner, persist evidence, or enable ingestion.
`blocked` means no product/native schema tuple may be accepted by that adapter.

## Lark

- **Official evidence:** [get document metadata](https://open.feishu.cn/document/server-docs/docs/docs/docx-v1/document/get) and [get raw content](https://open.feishu.cn/document/server-docs/docs/docs/docx-v1/document/raw_content) specify read-only Docx v1 GET endpoints and `docx:document:readonly`. Installed `lark-cli 1.0.86` exposes them through `api GET` with explicit `--as user`.
- **Discovery/read interface:** resolve one explicitly selected Wiki node to one
  `docx` object, then call only `GET /open-apis/docx/v1/documents/:id` and
  `GET /open-apis/docx/v1/documents/:id/raw_content`. `docs +fetch` is excluded
  because it may attach visible comment content. Links, embeds, attachments,
  child nodes, comments, and history are never followed.
- **Principal binding:** verified user identity, Wiki creator, and Wiki owner must
  match before content access. The observed CLI envelope must contain the exact
  literal `identity=user`; bot, auto, or fallback identities fail closed.
- **Revision/append semantics:** read current `revision_id`, capture raw content,
  then read `revision_id` again. Both must equal the approved canonical decimal
  revision. The observed compatibility probe remained at revision `3365`.
- **Missing guarantee:** the raw-content endpoint exposes no native block graph or
  per-block author. The v1 normalizer therefore emits one synthetic block,
  records non-semantic formatting loss, and marks it unresolved and
  claim-ineligible. A production authorization/credential runner and private
  ingestion transaction remain Task 7 work.
- **Fixtures required to unblock:** the exact normalizer tuple is covered by a
  fully synthetic observed-shape fixture and expected canonical output. Rich
  block schemas, other CLI versions/envelopes, comments, history, and every
  implicit traversal remain blocked and require separate fixtures/approval.
- **Failure behavior:** fail closed on an absent binary, non-user or ambiguous
  identity, owner mismatch, revision change, unknown/missing/duplicate response
  fields, malformed JSON, unvalidated redaction output, resource limits, or any
  mutation path. Never retry under another principal, broaden the selector, or
  echo content.

## Codex

- **Official evidence:** [Codex CLI reference](https://developers.openai.com/codex/cli/reference).
- **Discovery/read interface:** `codex resume` accepts a session UUID/name and
  offers `--last`/`--all`; `fork` exists. `codex exec --json` describes NDJSON
  events for a running task, not a stored-transcript export contract. Local
  `codex resume --help` confirmed UUID/name and picker behavior.
- **Principal binding:** the CLI operates in its current authenticated context,
  but the cited interfaces do not provide the stable owner/principal binding
  required for claim attribution in an exported session.
- **Revision/append semantics:** resume and fork are product capabilities. No
  stable session snapshot, append watermark, stored-event schema, or fork-edge
  representation was established.
- **Missing guarantee:** no supported stored-transcript schema/version or stable
  stored-session read API exists for this adapter.
- **Fixtures required to unblock:** version-pinned synthetic exports for resume,
  fork, retries, edits, compaction, tool call/chunks/result, sub-agent lifecycle,
  and cross-agent messages, plus concurrent append/truncation, identity ambiguity,
  malformed NDJSON, unknown records, auth failure, and permission denial.
- **Failure behavior:** fail closed on picker-only discovery, absent stable export,
  unresolved owner, live append without a pinned snapshot, unknown record/schema,
  gaps, or conflicting causal identifiers. Never scan session directories or
  silently reinterpret `codex exec --json` as stored transcript export.

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
