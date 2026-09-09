# Local Codex Standalone Runtime Design

Date: 2026-09-09

Status: proposed for implementation after user review

## Objective

Make one explicitly selected local Codex session ingestible through a standalone
Knowledge Distiller CLI command without Python host injection. The runtime must
reuse the existing authorization, broker, adapter, redaction, provenance, and
atomic-ingestion boundaries. This milestone uses only synthetic session files
for implementation and acceptance; it does not authorize a real session read.

## Milestone boundary

This milestone adds one local Codex runtime and one CLI entry point. It does not
add session discovery, directory enumeration, sibling reads, Lark production
credentials, automatic capability mapping, evidence display, evaluation,
export, installation, publication, key generation, key retention, or purge.

The existing dependency-injected `ingest-source` API remains unchanged. The new
command is:

```text
kd.py ingest-codex-session TASK_PATH PRIVATE_REQUEST.json \
  --redaction-key-file REDACTION_KEY
```

The command emits only the existing bounded `IngestionResult`. It never emits
the request, source path, project identifier, key path, source text, redacted
text, grant fields, or provenance internals.

## Components

### `local_runtime.py`

This module owns the standalone-only boundary:

1. strictly decode the private request;
2. obtain the current effective OS user ID and current Unix time;
3. derive opaque local principal, tenant, task, grant, and attestation IDs;
4. materialize the exact `AuthorizationContext`, `ContentGrant`,
   `AuthorityAttestation`, `SessionRequest`, and `SessionIdentity` values;
5. read and validate the redaction key through the shared safe-file boundary;
6. call the existing `ingestion.ingest_source` transaction.

It does not parse Codex JSONL, write task artifacts directly, enumerate a
directory, resolve a username, read a credential store, or mutate the source.

### `source_io.py`

The existing descriptor-relative reader gains optional descriptor metadata
constraints. When `expected_owner_uid` is supplied, the UID check occurs on the
same open descriptor used for both prefix reads. When owner-only mode is
required for a secret, the same descriptor must be a single-link regular file
whose permission bits grant no group or other access.

Existing callers that omit these options retain their current policy. The
implementation must not add a second path walker or a pre-open `stat` check.

### `brokers.py`

`SessionIdentity` carries the trusted expected owner UID. The local session
broker passes that UID into the descriptor reader. A request cannot override
the effective UID or claim that a differently owned file belongs to the active
principal.

### `kd.py`

The new command reads the private request only after its arguments and runtime
mode pass preflight. It delegates construction and ingestion to
`local_runtime.py`. Existing `ingest-source` continues to require an injected
runtime, preserving backward compatibility and the previous trust boundary.

## Private request contract

Schema: `knowledge-distiller.local-codex-ingestion-request/v1`.

The JSON object is closed and contains exactly:

| Field | Contract |
| --- | --- |
| `schema_version` | Exact schema string above |
| `transaction_id` | Existing safe transaction identifier |
| `expected_generation_id` | Exact current task generation |
| `session_path` | One explicit file path; never a directory or glob |
| `project_id` | Opaque project binding required by the Codex adapter |
| `prefix_length` | Integer in `1..MAX_GRAPH_BYTES` |
| `prefix_digest` | `sha256:` plus 64 lowercase hexadecimal digits |
| `derived_processing_until` | Exclusive Unix time for derived evidence reuse |

The decoder is duplicate-key-safe, rejects unknown/missing fields, invalid
Unicode, booleans as integers, oversized input, wildcard paths, nonzero prefix
starts, unsupported product/schema selection, and any caller-supplied identity,
issuer, grant, attestation, owner, product version, or native schema value.

The runtime pins the sole supported tuple:

```text
codex / 1.0.0 / 0.153.0 / rollout-jsonl-v1
```

Newer or older native records fail closed in the existing adapter. This runtime
does not inspect source content to select or upgrade an adapter.

## Identity and authorization

The trusted local identity source is the process effective UID. It is converted
to an opaque SHA-256 identifier; the username is never resolved or persisted.
The tenant/account scope is a separate domain-separated digest of the local UID
and task scope. The task ID is a digest of the already selected task path and
current generation, so it is stable for this one ingestion decision without
exposing the path.

The explicit command invocation is the current content-read decision. At the
start of the operation, the runtime creates:

- a `ContentGrant` issued by the opaque active principal;
- a verified-ownership `AuthorityAttestation` issued by the fixed trusted local
  owner verifier; and
- an `AuthorizationContext` whose authenticated issuers are exactly those two
  derived identities.

Both records bind the task, principal, tenant, exact path, byte-0 prefix,
purpose `distill-knowledge`, issue time, five-minute read window, requested
derived-processing deadline, and revocation `false`. Their record IDs and
decision digests are domain-separated canonical SHA-256 values. The derived
deadline must be later than issue time and no later than 90 days after issue.

The runtime never treats session text, request identity fields, file
readability, or a historical conversation as authorization. The source broker
revalidates both generated records immediately before source I/O. The source
descriptor must report `st_uid == geteuid()`; otherwise acquisition fails.

This is a local authenticated ownership binding, not organizational authority
for another person's content. A differently owned session remains unsupported.

## Redaction key boundary

The caller supplies one explicit key file. The runtime opens it through the
shared descriptor-relative, no-follow reader and requires:

- one regular file with link count one;
- owner UID equal to the current effective UID;
- exact permission bits `0600`;
- no sparse or special-file representation;
- 32 through 64 raw bytes with no decoding or newline trimming; and
- stable descriptor identity while reading.

The key bytes exist only for the current process invocation. They are passed to
the existing deterministic redactor and are never added to CLI arguments,
environment variables, errors, logs, journal records, generation artifacts, or
telemetry. Creating, rotating, storing, and deleting key files is outside this
milestone.

## Data flow

```text
validated CLI arguments
  -> bounded private-request read
  -> strict closed request decode
  -> OS effective UID + current time
  -> owner-only redaction-key descriptor read
  -> generated ContentGrant + AuthorityAttestation + context
  -> Codex SessionRequest + trusted SessionIdentity(expected UID)
  -> existing local broker exact-prefix descriptor read
  -> existing Codex normalizer and redactor
  -> existing atomic private generation transaction
  -> bounded IngestionResult
```

The source prefix remains pinned by caller-supplied length and digest. The
existing broker performs two digest-checked reads on one descriptor. Appends
beyond the prefix remain excluded; edit, reorder, truncation, owner change,
inode substitution, link substitution, and metadata instability fail closed.

## Error handling

All new public failures are allowlisted code-only diagnostics. Input-shape,
unsafe request-file, unsafe key-file, invalid derived deadline, and local
identity failures exit as CLI input errors. Authorization, source ownership,
source mutation, adapter, redaction, task lineage, and atomic persistence
failures exit as ingestion rejections.

No exception message may contain a caller field. Unexpected exceptions collapse
to an existing bounded fallback. A failure before commit leaves the previous
generation authoritative and does not add a partial grant, source, evidence, or
provenance artifact.

## Test strategy

Development follows red-green-refactor and uses only temporary synthetic files.
Tests cover:

- strict request decoding, duplicate keys, unknown fields, scalar subclasses,
  UTF-8/resource bounds, wildcard paths, digest/length/time limits;
- effective-UID derivation without username resolution;
- same-descriptor source owner verification and owner substitution rejection;
- `0600`, owner, symlink, hardlink, sparse, special, moving, short, long, and
  changed key-file rejection;
- exact creation and independent revalidation of generated grant, attestation,
  context, session request, and session identity;
- standalone CLI success through the real broker, adapter, redactor, and atomic
  ingestion transaction using the checked-in synthetic Codex fixture;
- stale generation, wrong task phase, source prefix change, unsupported native
  content, and crash behavior;
- stdout/stderr/journal/generation absence of paths, key bytes, raw session text,
  owner UID, and private request fields; and
- no invocation of directory listing, Lark code, shell execution, network
  access, username lookup, credential discovery, export, or installation.

The focused local-runtime suite, the existing ingestion/broker/source-I/O/CLI
suites, and the strict full repository suite must all pass. Static compilation,
`git diff --check`, repository `__pycache__` absence, independent specification
review, independent quality/security review, and explicit simplification review
are completion gates.

## Acceptance

The milestone is complete when the standalone command can ingest the synthetic
Codex prefix into a real temporary task and reach the same post-ingestion state
as the dependency-injected path, without caller-provided identity or Python
runtime injection. It must prove exact selector/prefix/UID/tuple binding and
leave no private material in public output or control records.

Success does not mean automatic session discovery, support for arbitrary Codex
versions, production Lark access, automatic knowledge extraction, evaluation,
export, installation, or permission to read any real session.

## Next dependency

After this milestone, the next design may add the Lark standalone runtime using
authenticated `lark-cli` user identity and document owner/revision receipts.
That work must not reuse OS ownership as remote authority and may require a
separate authentication/configuration approval.
