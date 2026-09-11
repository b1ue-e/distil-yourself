# Local Lark Standalone Runtime Design

Date: 2026-09-11

Status: approved for planning

## Objective

Make one explicitly selected Docx document's observed current plain-text
snapshot ingestible through a standalone Knowledge Distiller CLI command. The
runtime must derive authority
from the verified `lark-cli --as user` identity and the document's server-side
owner metadata, then reuse the existing broker, adapter, redaction, provenance,
and atomic-ingestion boundaries.

This milestone establishes a synthetic, testable implementation first. It does
not authorize reading a real cloud document. Live access remains forbidden
until two facts are established for the pinned profile: endpoint integrity and
an accepted raw-content-to-revision consistency contract.

## Decision and alternatives

The selected approach is a pinned, low-level `lark-cli` runtime using exact
OpenAPI resources:

- [Docx basic information](https://open.feishu.cn/document/server-docs/docs/docs/docx-v1/document/get.md)
  supplies the current `revision_id`;
- [Drive metadata](https://open.feishu.cn/document/server-docs/docs/drive-v1/file/batch_query.md)
  supplies `owner_id`; and
- [Docx raw content](https://open.feishu.cn/document/server-docs/docs/docs/docx-v1/document/raw_content.md)
  supplies only the document's plain text.

Two alternatives are rejected for this milestone:

1. `docs +fetch` is convenient but may include comments and reference sidecars,
   broadening the authorized source beyond the document body.
2. Wiki resolution or shared-document ingestion would require a larger
   authority model. A readable document is not necessarily owned by the active
   user, and a Wiki URL token is not a Docx `document_id`.

## Scope

The milestone adds one owner-only Lark runtime and one CLI entry point:

```text
kd.py ingest-lark-document TASK_PATH PRIVATE_REQUEST.json \
  --redaction-key-file REDACTION_KEY --allow-live-read
```

| Concern | Milestone decision |
| --- | --- |
| Supported input | One bare 27-character Docx token or one canonical HTTPS Docx URL |
| Fetched source | Plain text from the exact Docx raw-content endpoint |
| Authority | Verified `user` Open ID must equal server-returned `owner_id` |
| Persisted selector | Domain-separated SHA-256 of the normalized Docx token |
| Public output | Existing bounded `IngestionResult` and allowlisted error codes only |
| Unsupported | Wiki URLs, search, folders, batches, related resources, comments, references, blocks, media, history, bot identity, collaborator authority, automatic auth, credential export, CLI update, or any write |

`--allow-live-read` is mandatory for any production ingestion network call but
is not sufficient by itself: the checked-in compatibility profile must also be
`live-enabled`. The initial implementation profile is `synthetic-only`, so the
production ingestion command fails before executable resolution, auth
verification, or metadata access even when the flag is present. The separately
designed compatibility-probe command is the only exception and requires its own
`--allow-live-probe` consent flag; it cannot ingest or persist content.

## Private request contract

Schema: `knowledge-distiller.local-lark-ingestion-request/v1`.

The closed JSON object contains exactly:

| Field | Contract |
| --- | --- |
| `schema_version` | Exact schema string above |
| `transaction_id` | Existing safe transaction identifier |
| `expected_generation_id` | Exact current task generation |
| `document_selector` | One bare 27-character Docx token or canonical HTTPS Docx URL |
| `derived_processing_until` | Exclusive Unix time, later than now and at most 90 days after now |

The decoder rejects duplicate, unknown, or missing fields; invalid Unicode;
oversized input; query strings; fragments; userinfo; alternate ports; non-Docx
paths; wildcard selectors; clock overflow; and any caller-supplied identity,
owner, issuer, grant, attestation, tenant, revision, product version, schema
digest, scope, or credential value.

The token grammar is exactly ASCII `[A-Za-z0-9]{27}`. A URL must use literal
lowercase `https`, contain an ASCII lowercase DNS host ending in exactly
`.larkoffice.com`, `.larksuite.com`, or `.feishu.cn`, and have exactly
`/docx/<token>` as its raw path. Empty labels, leading/trailing hyphens,
userinfo, any explicit port including `:443`, percent encoding, parameters,
query, fragment, trailing slash, Unicode host/path text, and decoded-path
equivalence are rejected. Host case is not normalized; invalid input fails.
The URL is parsed locally into its token and is never dereferenced. Bare tokens
and URLs for the same token normalize to one immutable
`ParsedDocumentSelector` containing only the validated token and this source
identifier:

```text
sha256("lark-docx-selector/v1\0" || utf8(token))
```

Only this digest is stored in authorization and provenance records. The current
revision is always resolved from the server. `latest`, `-1`, and a
caller-provided revision are not accepted. Runtime time has no clock-skew
tolerance; an invalid clock or overflow fails closed. Derived evidence remains
subject to task state and the existing authorization policy even before the
90-day upper bound.

## Pinned compatibility profile

| Property | Required value |
| --- | --- |
| Product | `lark` |
| Knowledge Distiller adapter version | `1.0.0` |
| Native `lark-cli` binary version | `1.0.86` |
| Native response schema | `docx-v1-raw-content-v1` |
| Read authorization | `docx:document:readonly` or broader `docx:document` |
| Metadata authorization | `drive:drive.metadata:readonly` or broader `drive:drive` |
| Per-call timeout | 30 seconds |
| Private request limit | 1 MiB |
| Identity/metadata stdout limit | 1 MiB each |
| Raw-response stdout limit | 64 MiB, including its JSON envelope |
| Stderr limit | 64 KiB each |
| Retries | None; any failure aborts the whole sandwich |
| Initial live status | `synthetic-only` |

The exact command shapes are:

```text
lark-cli --version
lark-cli auth status --json --verify
lark-cli api GET /open-apis/docx/v1/documents/<token> --as user
lark-cli drive metas batch_query --user-id-type open_id \
  --data - --as user --format json
lark-cli api GET /open-apis/docx/v1/documents/<token>/raw_content --as user
```

The Drive request body is canonical JSON supplied on stdin and contains one
`{doc_token, doc_type: "docx"}` item plus `with_url: false`. The
Docx-basic-information GET and Drive-metadata query both repeat after raw
content. Every remote API command is explicitly `--as user`; the auth command
must itself report that the selected and verified identity is `user`.

The runtime resolves `lark-cli` once to a canonical regular-file path, records
its device, inode, owner, mode, size, and nanosecond modification time, executes
that path directly, and rechecks the fingerprint before and after every call.
It requires exact version `1.0.86`, suppresses update notices, and never runs an
update. A different binary or schema needs a separate fixture, review, and
explicit profile addition.

The machine-readable profile is a canonical checked-in record containing its
status, tuple, accepted control-schema digests, raw schema digest, scope
alternatives, limits, endpoint-integrity evidence reference and digest, and
accepted consistency mode. Runtime code pins the record's canonical digest, so
editing the data file alone cannot enable access. Moving to `live-enabled`
requires a reviewed code-and-profile change after all live gates pass. This is
an accidental-access control under the trusted-user threat model, not protection
against a user who edits and executes the program itself.

### Control response contracts

Each authority-bearing response has a checked-in, closed JSON schema whose
canonical digest is pinned by the profile. Duplicate keys are rejected before
schema validation; `additionalProperties` is false at every object. Update
notices are suppressed rather than accepted as unknown output. The schemas must
establish at least these projections:

| Response | Required security projection |
| --- | --- |
| Verified auth | selected identity `user`; `verified: true`; profile-pinned active status and valid-token literals; non-empty `openId`; unique string `scope` array |
| Docx basic information | success and identity `user`; exact selected `document_id`; integer `revision_id` in `1..2^63-1`; all returned display fields typed but discarded |
| Drive metadata | success and identity `user`; exactly one metadata result; empty failed list; exact token; type `docx`; non-empty `owner_id`; all other returned metadata fields typed but discarded |
| Missing-scope error | failure and identity `user`; exact authorization/missing-scope type and subtype; bounded numeric code; unique bounded `missing_scopes` string array |

The raw-content envelope is not a control response and remains owned by the
existing pinned native adapter. Before a live profile is enabled, one approved
probe must capture only schema shape, literals, and digests needed to confirm or
revise these contracts; it must not retain document content or identifiers.

## Identity and owner authority

The runtime verifies the current login with:

```text
lark-cli auth status --json --verify
```

It requires a successful, verified `user` identity with an active token and a
non-empty user `openId`. It does not fall back to bot identity and does not run
`auth login`. Missing login, an expired token, missing scopes, or an ambiguous
identity fails with a bounded diagnostic and remediation hint outside source
artifacts.

The verified auth response's `identities.user.scope` is the only proactive scope
evidence. The pinned parser requires it to be a JSON array of unique non-empty
strings and checks the two exact capability alternatives listed in the profile
table. No other scope name is treated as broader by inference. A successful
endpoint call confirms operational access but does not replace the precheck;
structured `missing_scopes` in a typed CLI error may refine the bounded error
code, while free-form error text is never trusted.

The runtime queries Drive metadata with `user_id_type=open_id` and requires one
successful `docx` result whose echoed token equals the selected document token.
Its `owner_id` must exactly equal the verified user's `openId`. Read access,
editor access, collaborator membership, latest-editor identity, and document
creator inference are not accepted as authority.

Raw Open IDs are used only for the in-memory equality check. Because an Open ID
is application-scoped and both values come from the same verified user token,
the persisted identifiers are:

```text
principal = sha256("lark-user-principal/v1\0" || utf8(open_id))
account   = sha256("lark-account-scope/v1\0" || utf8(open_id))
```

The design does not claim that `account` is a portable tenant ID or correlate
the user across applications. Once owner equality succeeds, the opaque content
owner is the opaque principal. The authority attestation is issued by fixed
identity `lark-owner-verifier-v1` and records `verified-ownership` as its basis.

The generated `ContentGrant`, `AuthorityAttestation`, and
`AuthorizationContext` bind the task, generation, principal, account scope,
selector digest, purpose `distill-knowledge`, resolved revision, issue time,
five-minute read window, derived-processing deadline, and revocation `false`.
They are independently revalidated by the existing broker immediately before
content acquisition.

### Local trust model

The runtime is a single-user interactive process. It trusts the invoking
effective OS user, that user's Lark credential store, the pinned executable,
the operating system, and Feishu's authenticated API service. The redaction key
and credential store must belong to the same effective user that launches the
runtime. Shared service accounts, multi-user daemons, untrusted same-UID
processes, hostile local administrators, compromised Feishu responses, and
cross-application Open-ID correlation are outside the threat model.

## Observationally stable read protocol

The raw-content endpoint has no revision selector or response revision. The
runtime therefore uses an owner-and-revision sandwich:

```text
local task eligibility preflight
  -> verify active lark-cli user identity
  -> before: Docx revision + Drive owner metadata
  -> materialize short-lived authorization records
  -> acquire task writer lease and recheck generation/phase/source absence
  -> reverify the same active user identity
  -> read exact Docx raw-content endpoint
  -> after: Docx revision + Drive owner metadata
  -> require identical token, type, owner, and revision
  -> normalize, redact, and atomically commit
```

The initial local preflight avoids remote metadata access for an already
invalid task. It is not treated as a concurrency guarantee. The existing
ingestion transaction rechecks generation, phase, and source absence while
holding its writer lease before raw content is acquired.

The before snapshot must contain the selected token, type `docx`, observed
current revision, and matching owner. Immediately before content I/O, identity
verification must return the same user Open ID. The after snapshot must return
the same token, type, owner, and revision as the before snapshot. An observed
login change, owner change, edit/revision change, deletion, metadata ambiguity,
failed-list entry, missing field, or duplicate result discards the acquired
bytes and fails closed without committing artifacts. An edit-and-revert or
owner-transfer-and-revert that is not reflected by the two observations is not
claimed to be detected.

The resulting guarantee is deliberately narrow: no owner or revision change
was observed across the raw read. It does not cryptographically bind the bytes
to that revision, prove replica consistency, or promise that the document is
still current after the second metadata read. Under the local trust model,
Feishu is trusted not to return content inconsistent with two matching revision
observations. Live readiness requires either authoritative support for that
assumption or an explicit user decision to accept observational consistency. A
compatibility probe can validate shapes and behavior but cannot prove this
server-side property.

## Components

### `lark_runtime.py`

Owns strict request decoding, local task preflight, verified identity and
metadata materialization, opaque authorization derivation, acquisition callback
construction, and delegation to `ingestion.ingest_source`. It does not parse
document text or write task artifacts directly.

Selector parsing, endpoint construction, and selector hashing occur once in the
immutable `ParsedDocumentSelector`. Each before/after pair becomes the same
typed `LarkObservation`, and one equality validator owns all token, type, owner,
and revision comparison. Other layers do not duplicate those rules.

### `lark_cli_transport.py`

Owns the narrow subprocess boundary. It exposes typed operations for version,
verified user identity, Docx basic information, Drive owner metadata, and raw
content. Each operation uses its profile-fixed argument vector, `shell=False`,
bounded stdout and stderr, a timeout, and an allowlisted environment sufficient
for the CLI's local credential store. No proxy or API-base override variable is
inherited.

The transport strictly decodes identity and metadata JSON because those values
form authorization evidence. For raw content it checks process status and byte
bounds but keeps stdout opaque; the pinned native adapter alone validates the
exact `{ok, identity, data.content}` envelope and extracts document text. The
broker counts raw envelope bytes before the adapter parses it.

The transport never accepts arbitrary verbs, paths, flags, base URLs,
executables, environment entries, or output files. It never reads credential
files itself and never places credentials in argv, environment values,
exceptions, logs, or artifacts.

### `brokers.py`

The Lark broker gains an ambient-user credential mode for the production
transport. An empty injected secret environment is valid only when the trusted
runner returns independently verified user, owner, selector, revision,
version, schema, no-fallback, and transport receipts. Existing explicit-secret
resolver behavior and dependency-injected tests remain backward compatible.

The runner remains a trust boundary. A `redirected=false` receipt requires one
of two forms of reviewed evidence: the exact CLI version's source or official
contract proves a fixed official API origin and redirect rejection, or the
launcher exposes the final origin and redirect chain so the transport can prove
the request stayed on the approved HTTPS origin. Canonical executable
fingerprinting, a sanitized no-proxy/no-base-override environment, fixed argv,
and explicit `--as user` are necessary but do not by themselves prove HTTP
redirect behavior. Lack of either sufficient evidence form must not become a
false receipt; live readiness then remains blocked pending a compliant launcher.

### `runtime_support.py`

Moves only the existing safe redaction-key reader, effective-UID check, and
runtime clock from the Codex-specific module. Both runtimes use those exact
operations. No generic runtime framework or speculative abstraction is added,
and Codex behavior must remain unchanged.

### `kd.py`

Adds the closed `ingest-lark-document` command. Argument validation and bounded
request-file reading happen before remote access. Existing commands and the
dependency-injected `ingest-source` API retain their contracts.

## Transport and output safety

All subprocess outputs are bounded while streaming; a process is terminated as
soon as a limit is exceeded. The raw-content success envelope remains opaque
bytes until the broker hands it to the pinned isolated adapter. Identity and
metadata parsers never accept values from the document body.

Every subprocess starts in its own process group. On timeout, output overflow,
or cancellation, the transport sends termination to the group, waits at most
one second, escalates to a group kill, and always reaps the child before
returning a bounded error. Partial stdout and stderr are discarded.

Only these remote resources are permitted:

```text
GET  /open-apis/docx/v1/documents/:document_id
POST /open-apis/drive/v1/metas/batch_query
GET  /open-apis/docx/v1/documents/:document_id/raw_content
```

Version 1 performs no automatic retry. A timeout, rate limit, process failure,
malformed response, or transient API error aborts the complete operation. A new
manual invocation starts a new identity check, metadata sandwich, authorization
window, and transaction.

### Sensitive-data policy

The selector token is necessarily present in the two fixed Docx child-process
argv values. Under the single-user trust model, same-UID process inspection is
trusted; the token is never logged or persisted by Knowledge Distiller. The
Drive token travels on stdin. Open IDs, owner IDs, titles, credential material,
raw and redacted text, request bodies, and key material must not appear in CLI
output, errors, journals, control records, telemetry, or process environment.
Private source, authorization, evidence, and provenance artifacts retain only
the fields required by their existing contracts, using the selector and
identity digests defined above.

## Reused dependency contracts

| Dependency | Invariant relied upon |
| --- | --- |
| `authorization` | Closed grants, attestations, and context are independently validated for exact scope and expiry |
| `brokers.fetch_lark` | Fixed raw endpoint, trusted receipt validation, byte bounds, exact selector/owner/observed-revision/profile binding |
| `native_adapters` | Exact 1.0.86 success envelope and schema digest; no adapter auto-detection |
| `ingestion.ingest_source` | Writer lease, generation/phase/source recheck before acquisition, redaction before persistence, atomic generation commit |
| `TaskCoordinator` | Previous generation remains authoritative after any pre-commit failure |
| `source_io` | Bounded, no-follow, same-effective-user read of the private request and `0600` redaction key |

## Error handling

New public failures are allowlisted code-only diagnostics grouped into invalid
request, unavailable/incompatible CLI, unverified identity, missing scope,
owner mismatch, unstable document, transport failure, response-too-large, and
runtime failure. Raw CLI stderr and upstream messages are not propagated when
they can contain identifiers or content.

`missing-scope` is emitted only from a successfully decoded structured CLI
error whose typed subtype or missing-scope list establishes that condition. An
unknown authorization response collapses to the generic unverified-identity or
transport code; message text is never pattern-matched into a security decision.

No failure may commit a partial source, grant, attestation, evidence,
provenance, or generation. Existing task artifacts remain authoritative.
Unexpected exceptions collapse to one bounded runtime code.

## Test strategy

Development follows red-green-refactor. All repository tests use synthetic
subprocess results and the existing redacted Lark fixture; they do not invoke
the network, inspect a real login, or read a real document. Transport tests put
an owner-controlled executable fixture named `lark-cli` first in a temporary
PATH. The normal resolver, fingerprinting, fixed argv, stdin, streaming bounds,
timeout cleanup, parsers, broker, adapter, and transaction all remain active.

For the end-to-end command-handler test only, the test process replaces one
private live-gate predicate with a synthetic-test gate. No CLI flag, request
field, environment variable, data file, or installed entry point exposes that
substitution. Production tests separately prove that the checked-in
`synthetic-only` profile rejects before executable resolution or network access.
“No Python runtime injection” means the public command accepts no caller
runtime/transport object; it does not prohibit private unit-test substitution.

Tests cover:

- closed request decoding and selector normalization;
- exact CLI version/path/fingerprint pinning, executable replacement rejection,
  sanitized environment, and update-notice isolation;
- verified user identity parsing and rejection of bot, stale, expired,
  ambiguous, or changed identities;
- exact Drive owner comparison using `open_id` and rejection of collaborator,
  latest-editor, missing, duplicate, mismatched, and failed metadata;
- observed revision-before/raw/revision-after stability, owner-transfer races,
  and no claim of cryptographic content-to-revision binding;
- fixed argv, environment allowlist, `shell=False`, timeout, byte bounds,
  process termination, malformed envelopes, and bounded diagnostics;
- ambient-user broker binding without weakening existing injected credential
  behavior;
- stale generation, wrong phase, duplicate source, adapter mismatch,
  redaction failure, and crash-before-commit behavior;
- end-to-end standalone ingestion into a real temporary task using only
  synthetic Lark responses; and
- absence of raw tokens/selectors, Open IDs, titles, raw text, redacted text,
  key material, request fields, and upstream diagnostics from stdout, stderr,
  journals, and control records; only defined opaque digests may persist.

The focused Lark runtime suite, existing broker/adapter/ingestion/CLI suites,
and full repository suite must pass. Static compilation, `git diff --check`,
repository `__pycache__` absence, specification review, quality/security review,
and explicit review for deletable, mergeable, or redundant code are completion
gates.

## Acceptance and live-read gate

The synthetic milestone is complete when the standalone command can ingest one
synthetic observed-current Docx response through the real broker, adapter, redactor, and
atomic transaction without caller-supplied identity, owner, revision,
authorization records, credentials, or Python runtime injection.

Live activation is a separate reviewed change with this sequence:

1. The initial `synthetic-only` digest rejects ordinary ingestion before every
   Lark-related subprocess.
2. The user explicitly approves one exact selector for a future
   `probe-lark-document ... --allow-live-probe` invocation. That separately
   gated command may exercise only the pinned reads, retains no content or
   identifiers, and cannot commit an ingestion generation.
3. Review establishes endpoint-integrity evidence and the control/raw schema
   digests. Authoritative revision-consistency support is recorded, or the user
   explicitly accepts the observational guarantee.
4. A reviewed code change updates both canonical profile and pinned digest to
   `live-enabled` with those evidence references and the accepted consistency
   mode.
5. Every later ingestion still requires its own exact private request and the
   explicit `--allow-live-read` flag. Absence of either the live profile or flag
   rejects before executable resolution, auth, metadata, or content access.

The probe verifies binary identity, response envelopes, effective scope
capabilities, owner/revision shapes, raw-content schema digest, and the
observable sandwich. It cannot prove server-side consistency, and success
authorizes neither later documents nor broader traversal. The probe command and
live-profile activation are deferred; the synthetic milestone cannot perform a
real Lark read.

## Deferred work

- Resolve one explicit Wiki URL to one Docx object without traversing siblings.
- Define authority for shared documents not owned by the active user.
- Add reviewed compatibility tuples for later `lark-cli` versions.
- Add explicit document discovery only after defining a separate consent and
  scope model.
