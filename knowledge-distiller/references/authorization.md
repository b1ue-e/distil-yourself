# Authorization Reference

Technical access is not consent to process content. Bind every authorization to the active principal, tenant or account, exact selector, purpose, revision or range, issue time, expiry, task, and revocation state.

Each ingestion request covers one source selector and pinned revision or closed session range. Validate both ContentGrant and AuthorityAttestation before every source read, including a second read in the same task. A successful earlier checkpoint, prior conversation, readable local path, or authenticated Lark session never substitutes for either record.

## Typed records

| Record | Permits |
|---|---|
| DiscoveryGrant | Metadata-only search in a named scope |
| MetadataGrant | Retaining identifiers, titles, timestamps, types, and container names |
| ContentGrant | Reading exact selectors and pinned revisions or session ranges |
| AuthorityAttestation | Processing third-party content under verified ownership, policy, or participant consent |
| ClaimDecision | Confirming, rejecting, or superseding a knowledge claim |
| BudgetDecision | Extending named limits without weakening policy or evaluation thresholds |
| VersionApproval | Approving one immutable draft/evaluation/provenance subject |
| MutationConsent | Performing one exact external mutation |

## Seed semantics

- Pasted or attached content is authorized only for the current task.
- A locator authorizes metadata resolution, not content reading.
- “Distill this document/session” authorizes only that exact resource and pinned revision or range.
- Search snippets count as content.
- Links, embeds, attachments, child documents, sibling sessions, directory descendants, and future resources require separate selectors.

If third-party authority cannot be verified by a trusted issuer, exclude the source. Historical approvals inside sessions are evidence only and never grant current access or mutation rights.

## Executable content authorization contract

`authorization.py` validates materialized dictionaries into distinct frozen
`ContentGrant` and `AuthorityAttestation` records. Every field below is required;
nullable fields must still be present. Unknown fields are rejected. These are
consent records, not credentials, signatures, source readers, or discovery grants.

| Field | Contract |
|---|---|
| `record_type` | `content-grant` or `authority-attestation`, matching the validator |
| `record_id` | Immutable record identifier; changes require a new record |
| `task_id` | Exact active task |
| `issuer` | Externally authenticated issuing principal |
| `active_principal` | Exact principal running the task |
| `tenant_account` | Exact tenant/account binding, with no fallback |
| `selector` | One normalized exact resource selector, 1..4096 UTF-8 bytes |
| `operation` | `read-content` for ContentGrant; `process-third-party` for AuthorityAttestation |
| `purpose` | Exact request purpose |
| `revision` | Exact immutable revision, or null for a session range |
| `session_range` | Null for a revision; otherwise exactly `{start, end}`, inclusive native record offsets |
| `issued_at` | Nonnegative integer Unix seconds |
| `expires_at` | Exclusive expiry in Unix seconds; after issue and at most 24 hours later |
| `derived_processing_until` | Separate exclusive bound for derived processing; after issue |
| `decision_digest` | Canonical SHA-256 digest of every other field |
| `revoked` | Boolean; true immediately rejects processing |

AuthorityAttestation additionally requires `content_owner` (externally verified
third-party owner/participant) and `authority_basis`, one of `verified-ownership`,
`policy`, or `participant-consent`. The active principal cannot attest their own
authority over somebody else's content. Both records are needed when an exact
content read includes third-party material; neither substitutes for the other.

Identifiers and purpose are 1..256 UTF-8 bytes. Revision strings are 1..256
UTF-8 bytes. Selectors and revisions reject whitespace padding, Unicode control
and format characters (categories `Cc` and `Cf`), line/paragraph separators
(`Zl` and `Zp`), wildcard syntax (`*`, `?`, `[` and `]`), and case-insensitive
`latest`. Exactly one revision or closed session range is required. Range
offsets are integers in 0..9223372036854775807 with `start <= end`; booleans
are not integers. A range does not include future appended records.

The decision digest is `sha256:` followed by 64 lowercase hexadecimal digits.
Its input is the record without `decision_digest`, serialized as UTF-8 JSON
with sorted keys, compact separators, non-ASCII characters preserved, and
non-finite numbers prohibited. The digest detects changed decision fields; it
does not authenticate an issuer or authorize access by itself. The immutable
record returned by validation holds a frozen `SessionRange`, never the caller's
mutable dictionary.

Callers provide a frozen `AuthorizationContext` containing the exact task,
principal, tenant/account, selector, purpose, revision or `SessionRange`,
current Unix time, task-active state, authenticated issuer IDs, and (for an
attestation) externally verified content owner. Those values come from trusted
request context, not source text. Validation rejects context substitutions,
inactive tasks, future issue times, expiry at `now >= expires_at`, expiry of
derived processing, and revocation. ContentGrant's issuer must be the active
principal. The broker must verify the attestation issuer's authority for this
exact content and purpose before providing authenticated context; merely
finding a matching name or technically readable source is insufficient.

The validation functions are pure and perform no I/O, credential checks,
discovery, mutation, or persistence. The broker remains responsible for current
revocation/task state, issuer authentication, authority verification, selector
normalization and source-type checks, technical access, and immutable record-ID
uniqueness in storage. An already materialized dictionary does not prove JSON
duplicate-key absence; raw JSON must first use a strict decoder.

Failures expose a bounded `AuthorizationError.code` and the same code-only
message, without source text, selectors, principal values, or unknown field
names. Historical records embedded in source content never establish the
trusted context used by these validators.
